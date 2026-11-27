"""The model benchmark.

    python -m eval.benchmark list                                   # candidates and cases
    python -m eval.benchmark screening                              # stage 1: 10 quick cases, every candidate, 2 repeats
    python -m eval.benchmark deep --models qwen2.5:7b,qwen3:8b      # stage 2: 24 cases on the best models, 3 repeats
    python -m eval.benchmark cv                                     # the cv_analyst role: structured CV extraction
    python -m eval.benchmark letters                                # the writer role: cover letters (+ manual rubric)
    python -m eval.report                                           # tables, chart, docs/model_selection.md

How a fair run works (lessons from docs/experiments_log.md, sections 12-13):
  * the same data every time: the saved REAL Le Forem snapshot, the fictional CV "Emma Verbeke", no internet, no Adzuna;
  * ONE model at a time in memory: every model starts after `ollama stop` of all others, then is warmed up
    (loaded + system prompt read) - the warm-up is not part of any timing;
  * every case is repeated (the same setup varies 2x from run to run on this CPU);
  * a case that takes too long is cut off and counted as a timeout;
  * results are appended to eval/results/bench_<stage>.jsonl after EVERY run, so a stopped benchmark can be continued
    (finished runs are skipped) - important, because a full run takes hours.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml

from app import db
from app.config import ROOT_DIR, get_settings
from eval import checks

EVAL_DIR = ROOT_DIR / "eval"
RESULTS_DIR = EVAL_DIR / "results"
OLLAMA_EXE = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
CASE_TIMEOUT_S = 240


# --------------------------------------------------------------------------- loading
def load_cases(stage: str) -> list[dict]:
    path = EVAL_DIR / f"cases_{stage}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_candidates(include_extra: bool = False) -> list[dict]:
    data = yaml.safe_load((EVAL_DIR / "candidates.yaml").read_text(encoding="utf-8"))
    return list(data["candidates"]) + (list(data.get("deep_extra") or []) if include_extra else [])


def pick_candidates(names: str | None, include_extra: bool) -> list[dict]:
    pool = load_candidates(include_extra=True)
    if not names:
        return load_candidates(include_extra)
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    chosen = [c for n in wanted for c in pool if c["name"] == n or c["model"] == n]
    missing = [n for n in wanted if not any(c["name"] == n or c["model"] == n for c in pool)]
    if missing:
        raise SystemExit(f"Unknown candidate(s): {', '.join(missing)}. See: python -m eval.benchmark list")
    return chosen


# ----------------------------------------------------------------------- environment
class OllamaEnv:
    """Everything that touches the real Ollama (replaced by a fake in the tests)."""

    def __init__(self, host: str | None = None):
        self.host = (host or get_settings().ollama_host).rstrip("/")

    def resident(self) -> dict[str, float]:
        data = json.load(urllib.request.urlopen(f"{self.host}/api/ps", timeout=5))
        return {m["name"]: round(m["size"] / 2**30, 2) for m in data.get("models", [])}

    def unload_all(self) -> None:
        for name in self.resident():
            subprocess.run([str(OLLAMA_EXE), "stop", name], capture_output=True)
        time.sleep(3)

    def warm(self) -> dict:
        from app.warmup import BackgroundWarmer
        started = time.perf_counter()
        state = BackgroundWarmer().warm_now()
        return {"status": state["status"], "message": state["message"], "warm_s": round(time.perf_counter() - started, 1)}

    def load_only(self, model: str) -> float:
        from app import warmup
        return warmup.load_model(model)

    def free_ram_gb(self) -> float | None:
        try:
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                            ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ve", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.avail / 2**30, 1)
        except Exception:
            return None


def prepare_offline_environment() -> str:
    """Throw-away database seeded from the real snapshot; no internet, no Adzuna."""
    from app import demo
    note = demo.offline_environment("emma_verbeke")
    get_settings().update(request_timeout=150.0)
    return note


def set_up_case(case: dict) -> None:
    """Clean state for one case: no tracked applications; the CV profile present or absent as the case wants."""
    from eval.run_scenario import sample_profile
    with db.connect() as conn:
        conn.execute("DELETE FROM applications")
        if case.get("profile", True):
            profile, cv_text = sample_profile("emma_verbeke")
            db.save_profile(conn, profile, cv_text=cv_text)
        else:
            conn.execute("DELETE FROM profile")


# ---------------------------------------------------------------------- one run
def _evidence(results: list[dict]) -> str:
    return " ".join(json.dumps({"ok": r["ok"], "data": r["data"]}, ensure_ascii=False) for r in results if r.get("data"))


def run_case(case: dict, candidate: dict, repeat: int, memory: bool, timeout_s: float = CASE_TIMEOUT_S,
             make_agent: Callable[[], Any] | None = None) -> dict:
    """Ask the agent the case's question(s) with the real model and describe what happened."""
    from app.agent.agent import Agent, ungrounded_claims
    settings = get_settings()
    settings.update(ollama_model=candidate["model"], think=bool(candidate.get("think", False)),
                    agent_profile="", role_overrides={})
    set_up_case(case)
    agent = (make_agent or Agent)()
    prompts = case.get("turns") or [case["prompt"]]
    calls: list[dict] = []
    results: list[dict] = []
    prior_ids: list[str] = []
    answer, error, retracts, stats = "", None, 0, {}
    started = time.perf_counter()
    timed_out = False
    for index, prompt in enumerate(prompts):
        last_turn = index == len(prompts) - 1
        if index and not memory:
            agent.reset()                                        # "without memory": the conversation is forgotten
        for event in agent.run(prompt):
            kind = event["type"]
            if kind == "tool_call" and last_turn:
                calls.append({"name": event["name"], "args": dict(event["arguments"] or {}), "ok": None,
                              "error": None, "ids": [], "ms": None})
            elif kind == "tool_result":
                data = event.get("data")
                results.append({"ok": event["ok"], "data": data})
                if last_turn:
                    pending = next((c for c in reversed(calls) if c["name"] == event["name"] and c["ok"] is None), None)
                    if pending:
                        pending.update(ok=bool(event["ok"]), error=event.get("error"), ms=event.get("duration_ms"))
                        if event["name"] == "search_jobs" and isinstance(data, dict):
                            pending["ids"] = [j["id"] for j in data.get("jobs", [])]
                elif event["name"] == "search_jobs" and isinstance(data, dict):
                    prior_ids += [j["id"] for j in data.get("jobs", [])]
            elif kind == "retract" and last_turn:
                retracts += 1
            elif kind == "error":
                error = event["message"]
            elif kind == "done" and last_turn:
                answer, stats = event.get("text", ""), event.get("stats", {})
            if time.perf_counter() - started > timeout_s:
                timed_out = True
                break
        if timed_out or error:
            break
    wall = round(time.perf_counter() - started, 1)
    try:
        with db.connect() as conn:
            conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    run = {
        "calls": calls, "answer": answer, "retracts": retracts, "error": error, "timeout": timed_out, "db_ok": db_ok,
        "prior_ids": prior_ids, "ungrounded": ungrounded_claims(answer, _evidence(results)) if answer else [],
    }
    return {
        "stage": None, "model": candidate["name"], "case": case["id"], "category": case["category"], "repeat": repeat,
        "memory": memory, "wall_s": wall, "eval_count": stats.get("eval_count", 0), "eval_duration": stats.get("eval_duration", 0),
        "prompt_tokens": stats.get("prompt_eval_count", 0), "run": run, "checks": checks.evaluate(case, run),
    }


# --------------------------------------------------------------------- the stages
def _key(rec: dict) -> tuple:
    return (rec["model"], rec["case"], rec["repeat"], rec.get("memory", True))


def load_done(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                                            # a half-written last line after a crash
    return out


def append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_stage(stage: str, candidates: list[dict], repeats: int, env: Any = None, case_ids: list[str] | None = None,
              limit: int | None = None, timeout_s: float = CASE_TIMEOUT_S, results_dir: Path | None = None,
              say: Callable[[str], None] = print, make_agent: Callable[[], Any] | None = None) -> Path:
    """stage = "screening" or "deep". Appends one JSON line per run; skips runs that are already there."""
    env = env or OllamaEnv()
    path = (results_dir or RESULTS_DIR) / f"bench_{stage}.jsonl"
    cases = load_cases(stage)
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
    if limit:
        cases = cases[:limit]
    done = {_key(r) for r in load_done(path)}
    jobs = []                                                   # (candidate, case, repeat, memory)
    for cand in candidates:
        for repeat in range(1, repeats + 1):
            for case in cases:
                modes = [True, False] if case.get("memory") == "both" else [True]
                jobs += [(cand, case, repeat, m) for m in modes]
    todo = [j for j in jobs if (j[0]["name"], j[1]["id"], j[2], j[3]) not in done]
    say(f"{stage}: {len(jobs)} runs planned, {len(jobs) - len(todo)} already done, {len(todo)} to go -> {path.name}")
    note = prepare_offline_environment() if make_agent is None else "test environment"
    say(f"data: {note}")
    current_model, t0 = None, time.perf_counter()
    for number, (cand, case, repeat, memory) in enumerate(todo, start=1):
        if cand["name"] != current_model:
            current_model = cand["name"]
            say(f"\n=== {cand['name']}: unloading everything, loading the model ...")
            env.unload_all()
            warm = env.warm()
            say(f"    {warm['message']} (free RAM {env.free_ram_gb()} GB)")
            resident = env.resident()
            memory_gb = resident.get(cand["model"]) or resident.get(cand["model"] + ":latest")
        rec = run_case(case, cand, repeat, memory, timeout_s, make_agent)
        rec.update(stage=stage, memory_gb=memory_gb, free_ram_gb=env.free_ram_gb())
        append(path, rec)
        c = rec["checks"]
        flag = "PASS" if c["passed"] else "fail"
        elapsed = time.perf_counter() - t0
        eta = elapsed / number * (len(todo) - number)
        say(f"[{number:3d}/{len(todo)}] {cand['name']:18s} {case['id']} r{repeat}{'' if memory else ' (no memory)'}: {flag} "
            f"{rec['wall_s']:6.1f}s tools={[x['name'] for x in rec['run']['calls']]} "
            f"{'TIMEOUT ' if rec['run']['timeout'] else ''}{'retract ' * rec['run']['retracts']}(eta {eta / 60:.0f} min)")
    return path


def run_cv_stage(candidates: list[dict], repeats: int, env: Any = None, results_dir: Path | None = None,
                 say: Callable[[str], None] = print, build: Callable | None = None) -> Path:
    """The cv_analyst role: can the model alone (no rule-based safety net) fill in the profile of a CV?"""
    from app.llm import LLMError, chat_json
    from app.tools.profile_tools import EXTRACT_SYSTEM, MAX_CV_CHARS, Profile
    env = env or OllamaEnv()
    path = (results_dir or RESULTS_DIR) / "bench_cv.jsonl"
    done = {_key(r) for r in load_done(path)}
    cvs = sorted((ROOT_DIR / "data" / "sample_cvs").glob("*.txt"))
    for cand in [c for c in candidates if not c.get("think")]:
        first = True
        for cv in cvs:
            text = cv.read_text(encoding="utf-8")
            gold = checks.gold_from_cv(text)
            for repeat in range(1, repeats + 1):
                rec = {"stage": "cv", "model": cand["name"], "case": cv.stem, "repeat": repeat, "memory": True}
                if _key(rec) in done:
                    continue
                if first:
                    say(f"=== {cand['name']}: loading ...")
                    env.unload_all()
                    env.load_only(cand["model"])
                    first = False
                started = time.perf_counter()
                try:
                    fn = build or (lambda t, m: chat_json(Profile, EXTRACT_SYSTEM, f"CV:\n---\n{t[:MAX_CV_CHARS]}\n---",
                                                          model=m, max_tokens=700))
                    extracted = fn(text, cand["model"]).model_dump()
                    rec.update(error=None, extracted=extracted, scores=checks.score_profile(extracted, gold, text))
                except (LLMError, Exception) as e:                         # a broken model must not stop the benchmark
                    rec.update(error=str(e)[:200], extracted=None, scores=None)
                rec["seconds"] = round(time.perf_counter() - started, 1)
                append(path, rec)
                s = rec["scores"] or {}
                say(f"  {cand['name']:18s} {cv.stem:14s} r{repeat}: {rec['seconds']:5.1f}s "
                    f"skills recall {s.get('skills_recall')} precision {s.get('skills_precision')} languages_ok {s.get('languages_ok')}"
                    + (f"  ERROR {rec['error']}" if rec["error"] else ""))
    return path


def pick_letter_jobs() -> list[tuple[dict, str]]:
    """Three real vacancies (one per language of the saved snapshot) and the language of each letter."""
    from app.providers.snapshot import SnapshotProvider
    jobs = [j for j in SnapshotProvider().load_all() if j.source == "forem" and j.description]
    chosen = []
    for lang in ("fr", "nl", "en"):
        job = next((j for j in jobs if j.language == lang), None)
        if job:
            chosen.append((job.to_row(), lang))
    if chosen:
        chosen.append((chosen[0][0], "en"))                     # the same vacancy in English: language switching
    return chosen


def run_letter_stage(candidates: list[dict], env: Any = None, results_dir: Path | None = None,
                     say: Callable[[str], None] = print, write: Callable | None = None) -> Path:
    """The writer role: automatic letter checks; the letters are saved for the manual rubric."""
    from app.tools.cover_letter import write_letter
    from eval.run_scenario import sample_profile
    env = env or OllamaEnv()
    path = (results_dir or RESULTS_DIR) / "bench_letters.jsonl"
    done = {_key(r) for r in load_done(path)}
    profile, _ = sample_profile("emma_verbeke")
    profile.setdefault("education", ["Bachelor Business Management, Thomas More Mechelen"])
    for cand in [c for c in candidates if not c.get("think")]:
        first = True
        for number, (job, lang) in enumerate(pick_letter_jobs(), start=1):
            rec = {"stage": "letters", "model": cand["name"], "case": f"L{number}-{lang}", "repeat": 1, "memory": True}
            if _key(rec) in done:
                continue
            if first:
                say(f"=== {cand['name']}: loading ...")
                env.unload_all()
                env.load_only(cand["model"])
                first = False
            started = time.perf_counter()
            try:
                letter = (write or write_letter)(job, profile, lang, "friendly", cand["model"])
                rec.update(error=None, letter=letter, job={"id": job["id"], "title": job["title"], "company": job["company"]},
                           language=lang, checks=checks.score_letter(letter, lang, profile, job))
            except Exception as e:
                rec.update(error=str(e)[:200], letter=None, checks=None, language=lang)
            rec["seconds"] = round(time.perf_counter() - started, 1)
            append(path, rec)
            c = rec["checks"] or {}
            say(f"  {cand['name']:18s} {rec['case']}: {rec['seconds']:5.1f}s words {c.get('words')} auto_ok {c.get('auto_ok')} "
                f"issues {c.get('issues')}" + (f"  ERROR {rec['error']}" if rec["error"] else ""))
    return path


# ------------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["list", "screening", "deep", "cv", "letters"])
    parser.add_argument("--models", help="comma separated candidate names (default: all of that stage)")
    parser.add_argument("--repeats", type=int, help="screening 2, deep 3, cv 2 by default")
    parser.add_argument("--cases", help="comma separated case ids")
    parser.add_argument("--limit", type=int, help="only the first N cases (for a quick try)")
    parser.add_argument("--timeout", type=float, default=CASE_TIMEOUT_S)
    parser.add_argument("--fresh", action="store_true", help="forget earlier results of this stage and start again")
    args = parser.parse_args(argv)

    if args.stage == "list":
        print("Candidates:")
        for c in load_candidates(include_extra=True):
            print(f"  {c['name']:20s} {c.get('note', '')}")
        for stage in ("screening", "deep"):
            print(f"\n{stage}: " + ", ".join(f"{c['id']}({c['category']})" for c in load_cases(stage)))
        return 0
    candidates = pick_candidates(args.models, include_extra=args.stage == "deep")
    out = RESULTS_DIR / f"bench_{args.stage}.jsonl"
    if args.fresh and out.exists():
        out.unlink()
    ids = args.cases.split(",") if args.cases else None
    if args.stage in ("screening", "deep"):
        run_stage(args.stage, candidates, args.repeats or (2 if args.stage == "screening" else 3), case_ids=ids,
                  limit=args.limit, timeout_s=args.timeout)
    elif args.stage == "cv":
        prepare_offline_environment()
        run_cv_stage(candidates, args.repeats or 2)
    else:
        prepare_offline_environment()
        run_letter_stage(candidates)
    print("\nDone. Next:  python -m eval.report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
