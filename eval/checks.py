"""Scoring rules of the benchmark. Pure functions: no model, no network - so they are unit-tested.

A benchmark run (one question to the agent) is a dict:
    calls      [{"name", "args", "ok", "error", "ids"}]   tool calls in order; "ids" = vacancy ids a search returned
    answer     the final text
    retracts   how many times the guard threw an answer away
    ungrounded claims in the FINAL answer that no tool result contains (computed by the runner with the agent's guard)
    error / timeout / db_ok
    prior_ids  ids returned by earlier turns of the same conversation (memory cases)
A case (eval/cases_*.yaml) says what a good run looks like, in `expect`. `evaluate()` returns one boolean per
dimension (None = not asked in this case) plus `passed` (all asked dimensions true).
"""
from __future__ import annotations

import re
from typing import Any

from app.agent.agent import _BOLD, _JOB_ID, _URL, invented_titles
from app.agent.language import dominant_language
from app.geo import city_variants, is_brussels, normalize
from app.skills import find_skills, language_levels

DIMENSIONS = ("tool_ok", "args_ok", "ids_ok", "lang_ok", "honest_ok", "text_ok", "error_free")


# ------------------------------------------------------------------ small helpers
def in_order(expected: list[str], actual: list[str]) -> bool:
    """True if `expected` appears in `actual` as a subsequence (other calls in between are fine)."""
    it = iter(actual)
    return all(any(name == a for a in it) for name in expected)


def _norm(value: Any) -> str:
    return normalize(str(value or ""))


def _place_ok(args: dict, wanted: str) -> bool:
    if wanted.lower() == "brussels":
        return args.get("region") == "brussels" or is_brussels(args.get("city"))
    city = _norm(args.get("city"))
    return bool(city) and city in {_norm(v) for v in city_variants(wanted)}


def args_match(args: dict, conditions: dict) -> bool:
    """Every condition holds for these tool arguments.

    plain key: value        argument equals value (case-insensitive)
    place: brussels         the place searched (Brussels = region; cities in NL/FR/EN spellings)
    keywords_any: [a, b]    the keywords contain one of these (substring)
    job_type_any: [..]      job_type is one of these
    not_set: [language]     these arguments are absent or empty (e.g. the user's language must not be used as ad language)
    """
    for key, wanted in conditions.items():
        if key == "place":
            if not _place_ok(args, wanted):
                return False
        elif key == "keywords_any":
            kw = _norm(args.get("keywords"))
            if not any(_norm(w) in kw for w in wanted):
                return False
        elif key == "job_type_any":
            if args.get("job_type") not in wanted:
                return False
        elif key == "not_set":
            if any(args.get(a) not in (None, "", 0, "0") for a in wanted):
                return False
        elif _norm(args.get(key)) != _norm(wanted):
            return False
    return True


# ---------------------------------------------------------------------- one case
def evaluate(case: dict, run: dict) -> dict[str, Any]:
    exp = case.get("expect", {}) or {}
    calls = run.get("calls", [])
    names = [c["name"] for c in calls]
    answer = (run.get("answer") or "").strip()
    out: dict[str, Any] = {k: None for k in DIMENSIONS}

    # --- tools: the right ones, in the right order, none that are forbidden
    if any(k in exp for k in ("tools", "forbid_tools", "no_tool_calls")):
        ok = True
        if exp.get("no_tool_calls"):
            ok = ok and not names
        if "tools" in exp:
            ok = ok and (in_order(exp["tools"], names) if exp.get("order", True) else set(exp["tools"]) <= set(names))
        ok = ok and not (set(exp.get("forbid_tools", [])) & set(names))
        out["tool_ok"] = ok

    # --- arguments: for each tool, at least one call satisfies all conditions
    if exp.get("args"):
        out["args_ok"] = all(
            any(args_match(c["args"], cond) for c in calls if c["name"] == tool_name)
            for tool_name, cond in exp["args"].items())

    # --- ids: arguments that must point at vacancies the search really returned
    if exp.get("id_args"):
        results = [c["ids"] for c in calls if c["name"] == "search_jobs" and c.get("ids")]
        known = set(run.get("prior_ids", [])) | {i for ids in results for i in ids}
        latest = list(results[-1]) if results else list(run.get("prior_ids", []))
        ok = True
        for tool_name, rule in exp["id_args"].items():
            used = [c for c in calls if c["name"] == tool_name]
            if not used:
                ok = False
                continue
            for c in used:
                got = _ids_of(c["args"])
                if rule == "all":                      # compare ALL vacancies from the latest search (max 8)
                    ok = ok and set(latest[:8]) <= set(got) and bool(got)
                else:                                  # "known": every id must come from a search result
                    ok = ok and bool(got) and set(got) <= known
        out["ids_ok"] = ok

    # --- language of the answer
    if exp.get("language"):
        out["lang_ok"] = bool(answer) and dominant_language(answer) == exp["language"]

    # --- honesty: nothing invented, and (if asked) no vacancy listed when there are none
    honest = not run.get("retracts") and not run.get("ungrounded")
    if exp.get("no_vacancies"):
        honest = honest and not (_JOB_ID.search(answer) or _URL.search(answer) or invented_titles(answer, '"jobs"'))
    out["honest_ok"] = honest

    # --- text conditions
    text_checks = []
    low = answer.lower()
    if exp.get("contains_any"):
        text_checks.append(any(w.lower() in low for w in exp["contains_any"]))
    if exp.get("not_contains"):
        text_checks.append(not any(w.lower() in low for w in exp["not_contains"]))
    if exp.get("not_exact"):
        text_checks.append(low.strip(" .!\n") not in [w.lower() for w in exp["not_exact"]])
    if text_checks:
        out["text_ok"] = all(text_checks)

    # --- stability
    out["error_free"] = not run.get("error") and not run.get("timeout") and run.get("db_ok", True) and bool(answer)

    asked = [v for v in out.values() if v is not None]
    out["passed"] = all(asked)
    done = [c for c in calls if c["ok"] is not None]
    out["valid_calls"] = (sum(1 for c in done if c["ok"]), len(done))
    return out


def _ids_of(args: dict) -> list[str]:
    raw = args.get("job_ids", args.get("job_id", []))
    if isinstance(raw, str):
        raw = [p for p in re.split(r"[,\s;\[\]'\"]+", raw) if p]
    return [str(x) for x in raw]


# ------------------------------------------------------------- CV extraction (cv_analyst role)
def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#.]{3,}", _norm(text))}


def gold_from_cv(cv_text: str) -> dict:
    """The facts that are literally written in a (fictional) CV: name, city, skills and languages."""
    from app.tools.profile_tools import _split_items, sections
    lines = [l.strip() for l in cv_text.splitlines() if l.strip() and not l.strip().startswith("[")]
    found = sections(cv_text)
    return {
        "name": lines[0].title(), "city": lines[1].split(",")[0],
        "skills": _split_items(found.get("skills", [])),
        "languages": language_levels(_split_items(found.get("languages", []))),
    }


def score_profile(extracted: dict, gold: dict, cv_text: str) -> dict[str, float | bool]:
    """How good is a model's structured extraction of ONE CV? (the rule-based fallback is NOT used here)"""
    skills = [_norm(s) for s in extracted.get("skills") or []]
    gold_skills = [_norm(s) for s in gold["skills"]]
    cv_words = _words(cv_text)

    def covered(g: str) -> bool:
        gw = _words(g)
        return any(g in s or s in g or (gw and gw <= _words(s)) for s in skills)

    recall = sum(covered(g) for g in gold_skills) / len(gold_skills) if gold_skills else 1.0
    if skills:
        supported = sum(1 for s in skills if (_words(s) and len(_words(s) & cv_words) / len(_words(s)) >= 0.6))
        precision = supported / len(skills)
    else:
        precision = 0.0
    items = [i for key in ("education", "experience") for i in (extracted.get(key) or [])]
    invented = sum(1 for i in items if _words(i) and len(_words(i) & cv_words) / len(_words(i)) < 0.5)
    languages = language_levels(extracted.get("languages") or [])
    return {
        "name_ok": _norm(extracted.get("name")) == _norm(gold["name"]),
        "city_ok": _norm(gold["city"]) in _norm(extracted.get("city")),
        "skills_recall": round(recall, 3), "skills_precision": round(precision, 3),
        "languages_ok": languages == gold["languages"],
        "invented_items": invented, "items": len(items),
    }


# ------------------------------------------------------------------ letters (writer role)
TECH_SKILLS = {"python", "java", "javascript", "web development", "sql", "git", "linux", "cloud", "data analysis",
               "software testing", "excel", "office suite", "design"}


def score_letter(letter: str, language: str, profile: dict, job: dict) -> dict[str, Any]:
    """Automatic checks for a cover letter; the writing quality itself is scored by hand (docs/letter_rubric.md)."""
    from app.tools.cover_letter import HARD_LIMIT_WORDS, problems_in, word_count
    words = word_count(letter)
    issues = problems_in(letter, profile, language)
    tech = sorted(find_skills(letter) & TECH_SKILLS)
    checks = {
        "words": words,
        "length_ok": 60 <= words <= HARD_LIMIT_WORDS,
        "language_ok": dominant_language(letter) == language,
        "clean": not issues,                       # no placeholders, not greeting the candidate, no invented school
        "no_tech_list": len(tech) <= 1,            # the brief: motivation and qualities, not a list of technologies
        "mentions_company": bool(job.get("company")) and _norm(job["company"]).split()[0] in _norm(letter),
    }
    checks["issues"] = issues
    checks["tech_mentioned"] = tech
    checks["auto_ok"] = all(checks[k] for k in ("length_ok", "language_ok", "clean", "no_tech_list"))
    return checks
