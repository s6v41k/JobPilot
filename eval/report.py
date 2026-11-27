"""Turn the benchmark results into tables, charts and docs/model_selection.md.

    python -m eval.report                 # every stage that has results
    python -m eval.report --stage deep

Nothing here talks to a model: change eval/weights.yaml and run it again to re-rank the models.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import Any

import yaml

from app.config import ROOT_DIR
from eval.benchmark import RESULTS_DIR, load_done

DOCS = ROOT_DIR / "docs"
IMG = DOCS / "img"
BEGIN, END = "<!-- BEGIN AUTO-GENERATED (python -m eval.report) -->", "<!-- END AUTO-GENERATED -->"
METRICS = ("correct_tool", "valid_args", "multistep", "language", "honesty", "speed", "stability", "memory_footprint")
LABELS = {"correct_tool": "Right tools", "valid_args": "Valid args", "multistep": "Multi-step", "language": "Language",
          "honesty": "Honesty", "speed": "Speed", "stability": "Stability", "memory_footprint": "Small size"}


def load_weights(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT_DIR / "eval" / "weights.yaml").read_text(encoding="utf-8"))


def _pct(values: list[bool]) -> float | None:
    values = [v for v in values if v is not None]
    return round(100 * sum(values) / len(values), 1) if values else None


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))] if ordered else 0.0


# ---------------------------------------------------------------------- per model
def model_metrics(records: list[dict], weights: dict) -> dict[str, Any]:
    """Raw numbers, the 0-100 score of every metric, and the weighted final score of ONE model."""
    norm = weights["normalization"]
    chk = [r["checks"] for r in records]
    ok_calls = sum(c["valid_calls"][0] for c in chk)
    all_calls = sum(c["valid_calls"][1] for c in chk)
    valid_calls = round(100 * ok_calls / all_calls, 1) if all_calls else None
    args_ok = _pct([c["args_ok"] for c in chk])
    chain = [c["tool_ok"] and c["ids_ok"] is not False for r, c in zip(records, chk) if r["category"] == "chain"]
    walls = [r["wall_s"] for r in records]
    seconds = sum(r["eval_duration"] for r in records) / 1e9
    raw = {
        "runs": len(records), "avg_s": round(statistics.mean(walls), 1), "p95_s": round(p95(walls), 1),
        "tokens_per_s": round(sum(r["eval_count"] for r in records) / seconds, 1) if seconds else None,
        "prompt_tokens": round(statistics.mean(r["prompt_tokens"] for r in records)),
        "memory_gb": next((r["memory_gb"] for r in records if r.get("memory_gb")), None),
        "guard_discards": sum(1 for r in records if r["run"]["retracts"]),
        "timeouts": sum(1 for r in records if r["run"]["timeout"]),
        "errors": sum(1 for r in records if r["run"]["error"]),
        "pass_rate": _pct([c["passed"] for c in chk]),
    }
    parts = [x for x in (args_ok, valid_calls) if x is not None]
    score = {
        "correct_tool": _pct([c["tool_ok"] for c in chk]),
        "valid_args": round(sum(parts) / len(parts), 1) if parts else None,
        "multistep": _pct(chain),
        "language": _pct([c["lang_ok"] for c in chk]),
        "honesty": _pct([c["honest_ok"] for c in chk]),
        "speed": round(min(100.0, 100 * norm["speed_reference_s"] / raw["avg_s"]), 1) if raw["avg_s"] else None,
        "stability": _pct([c["error_free"] for c in chk]),
        "memory_footprint": round(max(0.0, 100 * (1 - raw["memory_gb"] / norm["memory_reference_gb"])), 1)
        if raw["memory_gb"] else None,
    }
    return {"raw": raw, "score": score, "final": final_score(score, weights),
            "quality": final_score({k: v for k, v in score.items() if k != "speed"}, weights)}


def final_score(score: dict, weights: dict) -> float:
    """Weighted mean of the metrics that exist (a metric nobody could measure is left out, not counted as 0)."""
    w = weights["weights"]
    used = {k: v for k, v in score.items() if v is not None}
    total = sum(w[k] for k in used)
    return round(sum(w[k] * v for k, v in used.items()) / total, 1) if total else 0.0


def by_category(records: list[dict]) -> dict[str, float | None]:
    cats = sorted({r["category"] for r in records})
    return {c: _pct([r["checks"]["passed"] for r in records if r["category"] == c]) for c in cats}


def rank_models(records: list[dict], weights: dict) -> list[dict]:
    names = list(dict.fromkeys(r["model"] for r in records))
    out = [{"model": n, **model_metrics([r for r in records if r["model"] == n], weights),
            "categories": by_category([r for r in records if r["model"] == n])} for n in names]
    return sorted(out, key=lambda m: m["final"], reverse=True)


def memory_effect(records: list[dict]) -> list[dict]:
    """Memory cases (two turns) with the conversation kept vs forgotten."""
    rows = []
    for model in dict.fromkeys(r["model"] for r in records):
        mine = [r for r in records if r["model"] == model and r["category"] == "memory"]
        if not mine:
            continue
        rows.append({"model": model,
                     "with": _pct([r["checks"]["passed"] for r in mine if r["memory"]]),
                     "without": _pct([r["checks"]["passed"] for r in mine if not r["memory"]])})
    return rows


# --------------------------------------------------------------------- role stages
def cv_table(records: list[dict]) -> list[dict]:
    rows = []
    for model in dict.fromkeys(r["model"] for r in records):
        mine = [r for r in records if r["model"] == model]
        ok = [r["scores"] for r in mine if r.get("scores")]
        mean = lambda key: round(statistics.mean(float(s[key]) for s in ok), 2) if ok else None   # noqa: E731
        rows.append({"model": model, "runs": len(mine), "errors": sum(1 for r in mine if r.get("error")),
                     "name_ok": _pct([s["name_ok"] for s in ok]), "city_ok": _pct([s["city_ok"] for s in ok]),
                     "skills_recall": mean("skills_recall"), "skills_precision": mean("skills_precision"),
                     "languages_ok": _pct([s["languages_ok"] for s in ok]),
                     "invented": sum(s["invented_items"] for s in ok), "items": sum(s["items"] for s in ok),
                     "seconds": round(statistics.mean(r["seconds"] for r in mine), 1)})
    return sorted(rows, key=lambda r: (r["skills_recall"] or 0) + (r["skills_precision"] or 0), reverse=True)


def letter_table(records: list[dict]) -> list[dict]:
    rows = []
    for model in dict.fromkeys(r["model"] for r in records):
        mine = [r for r in records if r["model"] == model]
        ok = [r["checks"] for r in mine if r.get("checks")]
        rows.append({"model": model, "letters": len(mine), "errors": sum(1 for r in mine if r.get("error")),
                     "auto_ok": _pct([c["auto_ok"] for c in ok]), "language_ok": _pct([c["language_ok"] for c in ok]),
                     "clean": _pct([c["clean"] for c in ok]), "no_tech_list": _pct([c["no_tech_list"] for c in ok]),
                     "avg_words": round(statistics.mean(c["words"] for c in ok)) if ok else None,
                     "seconds": round(statistics.mean(r["seconds"] for r in mine), 1)})
    return sorted(rows, key=lambda r: (r["auto_ok"] or 0), reverse=True)


# --------------------------------------------------------------------- markdown
def _fmt(v: Any, suffix: str = "") -> str:
    return "-" if v is None else f"{v}{suffix}"


def ranking_table(ranked: list[dict]) -> str:
    head = "| # | Model | **Score** | " + " | ".join(LABELS[m] for m in METRICS) + " | Avg s | p95 s | tok/s | GB | Guard discards | Timeouts |"
    sep = "|" + "---|" * (len(METRICS) + 9)
    rows = []
    for i, m in enumerate(ranked, start=1):
        s, r = m["score"], m["raw"]
        rows.append(f"| {i} | `{m['model']}` | **{m['final']}** | " + " | ".join(_fmt(s[k]) for k in METRICS)
                    + f" | {r['avg_s']} | {r['p95_s']} | {_fmt(r['tokens_per_s'])} | {_fmt(r['memory_gb'])} | {r['guard_discards']} | {r['timeouts']} |")
    return "\n".join([head, sep] + rows)


def category_table(ranked: list[dict]) -> str:
    cats = sorted({c for m in ranked for c in m["categories"]})
    rows = ["| Model | " + " | ".join(cats) + " |", "|" + "---|" * (len(cats) + 1)]
    rows += [f"| `{m['model']}` | " + " | ".join(_fmt(m["categories"].get(c), " %") for c in cats) + " |" for m in ranked]
    return "\n".join(rows)


def simple_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    out = ["| Model | " + " | ".join(t for _, t in columns) + " |", "|" + "---|" * (len(columns) + 1)]
    out += [f"| `{r['model']}` | " + " | ".join(_fmt(r.get(k)) for k, _ in columns) + " |" for r in rows]
    return "\n".join(out)


# ------------------------------------------------------------------------ charts
INK, MUTED, GRID, SURFACE, ACCENT, NEUTRAL = "#191919", "#666666", "#e6e6e6", "#ffffff", "#257de0", "#b9bec7"
RAMP = ["#eaf2fc", "#c3dbf7", "#8fbcf0", "#5a9be8", "#257de0", "#1a5fb0", "#134682"]   # one hue, light -> dark


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": INK, "axes.labelcolor": MUTED,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": GRID})
    return plt


def chart_scores(ranked: list[dict], title: str, path: Path) -> None:
    """Left: ranked bars of the weighted score (winner in the accent colour, the rest grey).
    Right: speed vs quality, every model labelled directly."""
    plt = _plt()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, max(4.2, 0.62 * len(ranked) + 2.2)),
                                   gridspec_kw={"width_ratios": [1.15, 1]}, facecolor=SURFACE)
    names = [m["model"] for m in ranked][::-1]
    values = [m["final"] for m in ranked][::-1]
    colours = [NEUTRAL] * (len(values) - 1) + [ACCENT]
    bars = ax1.barh(names, values, color=colours, height=0.58, edgecolor=SURFACE, linewidth=2)
    for bar, v in zip(bars, values):
        ax1.text(v + 1.2, bar.get_y() + bar.get_height() / 2, f"{v:.1f}", va="center", fontsize=11, color=INK)
    ax1.set_xlim(0, 108)
    ax1.set_title("Weighted score (0-100): higher is better", loc="left", fontsize=12, color=INK)
    ax1.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax1.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax1.spines[side].set_visible(False)
    ax1.tick_params(axis="y", length=0, labelsize=11)

    for m in ranked:
        best = m is ranked[0]
        x, y = m["raw"]["avg_s"], m["quality"]
        ax2.scatter([x], [y], s=110, color=ACCENT if best else NEUTRAL, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax2.annotate(m["model"], (x, y), textcoords="offset points", xytext=(9, 6), fontsize=10, color=INK)
    ax2.set_xlabel("Average seconds per question (faster is left)")
    ax2.set_ylabel("Quality score without speed (0-100)")
    ax2.set_title("Speed against quality: the best model is top-left", loc="left", fontsize=12, color=INK)
    ax2.grid(True, color=GRID, linewidth=0.8)
    ax2.set_axisbelow(True)
    ax2.set_ylim(0, 105)
    xs = [m["raw"]["avg_s"] for m in ranked]
    ax2.set_xlim(0, max(xs) * 1.35)
    for side in ("top", "right"):
        ax2.spines[side].set_visible(False)
    fig.suptitle(title, x=0.01, ha="left", fontsize=14, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)


def chart_heatmap(ranked: list[dict], title: str, path: Path) -> None:
    """Every metric of every model as a number on a one-hue scale (also the table view of the first chart)."""
    plt = _plt()
    from matplotlib.colors import BoundaryNorm, ListedColormap
    cmap = ListedColormap(RAMP)
    norm = BoundaryNorm([0, 40, 55, 70, 80, 90, 95, 100.01], cmap.N)
    fig, ax = plt.subplots(figsize=(12, max(3.4, 0.62 * len(ranked) + 1.8)), facecolor=SURFACE)
    for r, m in enumerate(ranked):
        for c, key in enumerate(METRICS):
            v = m["score"][key]
            ax.add_patch(plt.Rectangle((c, r), 1, 1, facecolor=SURFACE if v is None else cmap(norm(v)),
                                       edgecolor=SURFACE, linewidth=3))
            if v is not None:
                ax.text(c + 0.5, r + 0.5, f"{v:.0f}", ha="center", va="center", fontsize=11,
                        color="#ffffff" if v >= 80 else INK)
            else:                                           # no case asked for this metric: say so, do not leave a hole
                ax.text(c + 0.5, r + 0.5, "n/a", ha="center", va="center", fontsize=10, color=MUTED)
    ax.set_xlim(0, len(METRICS)); ax.set_ylim(len(ranked), 0)
    ax.set_xticks([i + 0.5 for i in range(len(METRICS))], [LABELS[k] for k in METRICS], fontsize=10)
    ax.set_yticks([i + 0.5 for i in range(len(ranked))], [m["model"] for m in ranked], fontsize=11)
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    fig.suptitle(title, x=0.01, ha="left", fontsize=14, color=INK, fontweight="bold")
    fig.text(0.01, 0.01, "Darker = better (0-100). Speed and size are scaled by eval/weights.yaml.", fontsize=9, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------- the document
def build_sections(results_dir: Path, weights: dict, img_dir: Path, stages: list[str] | None = None) -> str:
    parts: list[str] = []
    w = weights["weights"]
    parts.append("### Score weights (`eval/weights.yaml`)\n\n" + ", ".join(f"{LABELS[k].lower()} {int(v * 100)} %" for k, v in w.items()) + ".")
    for stage, title in (("screening", "Stage 1 - screening (10 quick cases, every candidate)"),
                         ("deep", "Stage 2 - deep test (24 cases, the best candidates)")):
        if stages and stage not in stages:
            continue
        records = load_done(results_dir / f"bench_{stage}.jsonl")
        if not records:
            continue
        ranked = rank_models(records, weights)
        runs = len(records)
        chart_scores(ranked, title, img_dir / f"{stage}_scores.png")
        chart_heatmap(ranked, title + ": all metrics", img_dir / f"{stage}_metrics.png")
        parts.append(f"### {title}\n\n{runs} runs. ![{stage} scores](img/{stage}_scores.png)\n\n![{stage} metrics](img/{stage}_metrics.png)\n\n"
                     + ranking_table(ranked) + "\n\n**Share of cases passed completely, by category**\n\n" + category_table(ranked))
        mem = memory_effect(records)
        if mem:
            parts.append("**Memory: the same two-turn cases with the conversation kept vs forgotten** (share passed)\n\n"
                         + simple_table(mem, [("with", "With memory %"), ("without", "Without memory %")]))
    cv = load_done(results_dir / "bench_cv.jsonl")
    if cv and (not stages or "cv" in stages):
        parts.append("### Role `cv_analyst`: structured CV extraction (the model alone, no rule-based safety net)\n\n"
                     + simple_table(cv_table(cv), [("runs", "Runs"), ("errors", "Errors"), ("name_ok", "Name %"), ("city_ok", "City %"),
                                                   ("skills_recall", "Skills recall"), ("skills_precision", "Skills precision"),
                                                   ("languages_ok", "Languages exact %"), ("invented", "Invented items"), ("seconds", "Avg s")]))
    letters = load_done(results_dir / "bench_letters.jsonl")
    if letters and (not stages or "letters" in stages):
        parts.append("### Role `writer`: cover letters (automatic checks; the writing quality is scored by hand, see `docs/letter_rubric.md`)\n\n"
                     + simple_table(letter_table(letters), [("letters", "Letters"), ("errors", "Errors"), ("auto_ok", "All checks %"),
                                                            ("language_ok", "Right language %"), ("clean", "No placeholders / invented school %"),
                                                            ("no_tech_list", "No technology list %"), ("avg_words", "Avg words"), ("seconds", "Avg s")]))
        write_letters_for_review(letters, results_dir / "letters_for_review.md")
    return "\n\n".join(parts)


def write_letters_for_review(records: list[dict], path: Path) -> None:
    """All generated letters in one file, for the manual rubric (docs/letter_rubric.md)."""
    lines = ["# Letters to score by hand\n", "Score each letter 1-5 on the five criteria of docs/letter_rubric.md and write the numbers into the table at the end.\n"]
    for r in sorted(records, key=lambda r: (r["case"], r["model"])):
        if not r.get("letter"):
            continue
        lines.append(f"\n## {r['case']} - `{r['model']}` ({r['checks']['words']} words)\n\n*Vacancy: {r['job']['title']} - {r['job']['company']}*\n\n{r['letter']}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def update_document(doc: Path, generated: str) -> None:
    """Replace only the auto-generated block; the hand-written conclusions stay."""
    block = f"{BEGIN}\n\n{generated}\n\n{END}"
    if doc.exists() and BEGIN in doc.read_text(encoding="utf-8"):
        text = doc.read_text(encoding="utf-8")
        text = text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]
    else:
        text = DOC_TEMPLATE.replace("{{AUTO}}", block)
    doc.write_text(text, encoding="utf-8")


DOC_TEMPLATE = """# Model selection

How we chose the language models for JobPilot (assignment extras d, e). Method: `eval/benchmark.py`
(two stages, repeated runs, one model in memory at a time, real data, no internet). Everything below the line
"AUTO-GENERATED" is produced by `python -m eval.report` from `eval/results/*.jsonl`; edit `eval/weights.yaml` and
run it again to change the ranking. The text outside the generated block is written by hand.

**Caveats that apply to every number:** one laptop (16 GB RAM, CPU only), the data is a saved real snapshot of Le Forem
offers, the CV is a fictional student, and local models vary from run to run (the same setup took 108 s and 220 s),
so differences of a few points are noise.

{{AUTO}}

## Pros and cons of the candidates

*(written by hand after the final run)*

## Final choice and justification

*(written by hand after the final run)*
"""


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["screening", "deep", "cv", "letters"], help="only this stage")
    args = parser.parse_args(argv)
    generated = build_sections(RESULTS_DIR, load_weights(), IMG, [args.stage] if args.stage else None)
    if not generated.strip() or "Stage" not in generated and "Role" not in generated:
        print("No results yet. Run: python -m eval.benchmark screening")
        return 1
    update_document(DOCS / "model_selection.md", generated)
    print(generated)
    print(f"\nWrote docs/model_selection.md and charts in docs/img/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
