"""Pre-flight check: is everything ready for the demo? Prints a green / yellow / red report.

    python -m app.preflight            # quick check (no model call), a few seconds
    python -m app.preflight --chat     # also asks the real model one tiny question (loads the model)
    python -m app.preflight --json     # machine-readable

Status meaning:  ok = fine,  warn = works but degraded (the app has a fallback),  fail = must be fixed,
skip = not tested. The exit code is 1 when anything FAILED, so it can be used in a script.
Every check is wrapped: a broken check becomes a red line, never a crash.
"""
from __future__ import annotations

import importlib
import json
import socket
import sys
import time
from dataclasses import asdict, dataclass
from typing import Callable

import httpx

from app import db
from app.agent import roles
from app.config import ROOT_DIR, get_settings


@dataclass
class Check:
    name: str
    status: str          # ok | warn | fail | skip
    detail: str
    ms: int = 0
    fix: str = ""        # what to do when it is not ok


def _timed(name: str, func: Callable[[], tuple[str, str] | tuple[str, str, str]]) -> Check:
    """Run one check; turn exceptions into a red line."""
    started = time.perf_counter()
    try:
        result = func()
        status, detail = result[0], result[1]
        fix = result[2] if len(result) > 2 else ""
    except Exception as e:
        status, detail, fix = "fail", f"the check itself crashed: {type(e).__name__}: {e}", ""
    return Check(name, status, detail, round((time.perf_counter() - started) * 1000), fix)


# ------------------------------------------------------------------ the checks
def check_python():
    v = sys.version_info
    ok = v >= (3, 11)
    return ("ok" if ok else "fail", f"Python {v.major}.{v.minor}.{v.micro}", "" if ok else "Install Python 3.11 or newer.")


def check_packages():
    missing = []
    for name in ("ollama", "fastapi", "uvicorn", "httpx", "pydantic", "pypdf", "numpy", "dotenv"):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        return "fail", "missing: " + ", ".join(missing), r".venv\Scripts\python -m pip install -r requirements.txt"
    return "ok", "all required packages are installed"


def check_web_files():
    needed = ["index.html", "app.js", "util.js", "style.css", "vendor/vue.global.prod.js"]
    missing = [n for n in needed if not (ROOT_DIR / "web" / n).exists()]
    return ("fail", "missing web files: " + ", ".join(missing), "Run git pull / restore the web folder.") if missing \
        else ("ok", "the web interface files are present (works without internet)")


def _tags() -> list[dict]:
    r = httpx.get(get_settings().ollama_host.rstrip("/") + "/api/tags", timeout=4)
    r.raise_for_status()
    return r.json().get("models", [])


def check_ollama():
    s = get_settings()
    try:
        version = httpx.get(s.ollama_host.rstrip("/") + "/api/version", timeout=4).json().get("version")
    except Exception:
        return "fail", f"Ollama does not answer at {s.ollama_host}", "Start the Ollama app (system tray), then run this check again."
    return "ok", f"Ollama {version} at {s.ollama_host}"


def _has(models: list[str], wanted: str) -> bool:
    return any(m == wanted or m == wanted + ":latest" or m.split(":")[0] == wanted for m in models)


def check_chat_model():
    """Every model the active agent team needs (chat agent, CV reader, letter writer)."""
    needed = roles.models_in_use()
    names = [m["name"] for m in _tags()]
    missing = [m for m in needed if not _has(names, m)]
    if missing:
        return "fail", "not installed: " + ", ".join(missing), "; ".join(f"ollama pull {m}" for m in missing)
    team = roles.active_name()
    return "ok", f"team '{team}': " + ", ".join(f"{r}={roles.model_for(r)}" for r in roles.ROLES) + " (all installed)"


def check_tool_support():
    s = get_settings()
    model = roles.model_for("orchestrator")              # only the chat agent calls tools
    info = httpx.post(s.ollama_host.rstrip("/") + "/api/show", json={"model": model}, timeout=10).json()
    caps = info.get("capabilities") or []
    if caps and "tools" not in caps:
        return "fail", f"'{model}' does not support tool calling (capabilities: {', '.join(caps)})", \
            "Choose a model with tool support in the Profile tab."
    return "ok", "the model supports tool calling" if caps else "tool support not reported by this Ollama version (assumed)"


def check_embed_model():
    s = get_settings()
    names = [m["name"] for m in _tags()]
    if not _has(names, s.embed_model):
        return "warn", f"embedding model '{s.embed_model}' is not installed: match scores will use skills and language only", \
            f"ollama pull {s.embed_model}"
    return "ok", f"embedding model '{s.embed_model}' is installed"


def check_database():
    with db.connect() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    need = {"jobs", "http_cache", "profile", "applications", "forem_offers", "embeddings", "api_budget"}
    if need - set(tables):
        return "fail", "tables missing: " + ", ".join(sorted(need - set(tables)))
    return "ok", f"database {get_settings().db_file.name} opens ({len(tables)} tables)"


def check_vacancies():
    from app.providers import forem_mirror
    from app.providers.snapshot import SnapshotProvider
    info = forem_mirror.info()
    if info["offers"] and info["age_hours"] is not None and info["age_hours"] < 24:
        return "ok", f"{info['offers']:,} Le Forem offers stored locally, refreshed {info['age_hours']} h ago"
    if info["offers"]:
        return "warn", f"{info['offers']:,} offers stored locally but {info['age_hours']} h old (they are labelled 'cached')", \
            "Start the server (it refreshes in the background) or: python -m app.providers.forem_mirror"
    snap = SnapshotProvider()
    if snap.available()[0]:
        return "warn", f"no local list yet; searches use the live portal, then the saved snapshot of {snap.snapshot_date()}", \
            "python -m app.providers.forem_mirror   (needs internet, one request)"
    return "fail", "no vacancy data at all (no local list, no snapshot)", "python -m app.providers.forem_mirror"


def check_search_tool():
    """A real search through the tool door, from the LOCAL list only (never touches the network)."""
    from app.providers import forem_mirror
    from app.providers.base import JobQuery
    from app.providers.forem import ForemProvider
    if not forem_mirror.info()["offers"]:
        return "skip", "no local list to search (see the check above)"
    jobs = ForemProvider().search_local(JobQuery(job_type="student", limit=3))
    if not jobs:
        return "warn", "the local list contains no current student offers"
    return "ok", f"local search works ({len(jobs)} student offers found, e.g. '{jobs[0].title[:40]}')"


def check_adzuna():
    s = get_settings()
    with db.connect() as conn:
        used = db.budget_used(conn, "adzuna")
    if not s.adzuna_configured:
        return "warn", "no Adzuna keys in .env (only needed as a last-resort source)"
    left = s.adzuna_monthly_budget - used
    return ("ok" if left > 5 else "warn"), f"Adzuna keys set; {used}/{s.adzuna_monthly_budget} calls used this month ({left} left)"


def check_profile():
    with db.connect() as conn:
        profile = db.load_profile(conn)
    if not profile:
        return "warn", "no CV profile yet: matching and letters need one", "Profile tab -> upload CV -> Analyze my CV"
    return "ok", f"profile of '{profile.get('name') or 'unknown'}' with {len(profile.get('skills') or [])} skills"


def check_port():
    with socket.socket() as sock:
        sock.settimeout(0.5)
        in_use = sock.connect_ex(("127.0.0.1", 8000)) == 0
    return ("warn", "port 8000 is already in use (the server may be running already)") if in_use \
        else ("ok", "port 8000 is free")


def check_embedding_call():
    from app import llm
    vector = llm.embed(["preflight"])[0]
    return "ok", f"embedding works ({len(vector)} numbers)"


def check_chat_call():
    """One tiny real question: proves the model loads and answers (and measures the cold start)."""
    from app import llm
    started = time.perf_counter()
    answer = llm.chat_text("Answer with one word.", "Say OK.", model=roles.model_for("orchestrator"), max_tokens=8,
                           temperature=0)
    seconds = time.perf_counter() - started
    status = "ok" if seconds < 90 else "warn"
    return status, f"the model answered '{answer[:20]}' in {seconds:.0f} s" + (
        " (slow: it was probably loading into memory)" if seconds > 20 else "")


# ----------------------------------------------------------------------- runner
def run_checks(chat: bool = False) -> list[Check]:
    """Run all checks. Checks that need Ollama are skipped (not failed twice) when Ollama is down."""
    results: list[Check] = []
    plan: list[tuple[str, Callable]] = [
        ("Python", check_python), ("Packages", check_packages), ("Web files", check_web_files),
        ("Database", check_database), ("Vacancy data", check_vacancies), ("Local search", check_search_tool),
        ("Adzuna", check_adzuna), ("CV profile", check_profile), ("Port 8000", check_port),
    ]
    for name, func in plan:
        results.append(_timed(name, func))
    ollama = _timed("Ollama", check_ollama)
    results.append(ollama)
    llm_checks: list[tuple[str, Callable]] = [("Chat model", check_chat_model), ("Tool calling", check_tool_support),
                                              ("Embedding model", check_embed_model)]
    if chat:
        llm_checks += [("Embedding call", check_embedding_call), ("Chat call", check_chat_call)]
    for name, func in llm_checks:
        results.append(_timed(name, func) if ollama.status == "ok" else Check(name, "skip", "Ollama is not running"))
    if not chat:
        results.append(Check("Chat call", "skip", "not run (use --chat to load the model and ask it one question)"))
    return results


def summarize(checks: list[Check]) -> dict:
    count = {s: sum(c.status == s for c in checks) for s in ("ok", "warn", "fail", "skip")}
    verdict = "NOT READY" if count["fail"] else ("READY (with warnings)" if count["warn"] else "READY")
    return {"verdict": verdict, "ready": count["fail"] == 0, "counts": count}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    checks = run_checks(chat="--chat" in argv)
    summary = summarize(checks)
    if "--json" in argv:
        print(json.dumps({**summary, "checks": [asdict(c) for c in checks]}, indent=2, ensure_ascii=False))
        return 0 if summary["ready"] else 1
    colour = {"ok": "\033[32m", "warn": "\033[33m", "fail": "\033[31m", "skip": "\033[90m"}
    icon = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "skip": "skip"}
    print("\nJobPilot pre-flight check\n")
    for c in checks:
        print(f"  {colour[c.status]}[{icon[c.status]}]\033[0m {c.name:<16} {c.detail}" + (f"  ({c.ms} ms)" if c.ms > 500 else ""))
        if c.fix and c.status in ("warn", "fail"):
            print(f"         -> {c.fix}")
    n = summary["counts"]
    tone = colour["fail"] if not summary["ready"] else colour["warn"] if n["warn"] else colour["ok"]
    print(f"\n  {tone}{summary['verdict']}\033[0m   ({n['ok']} ok, {n['warn']} warnings, {n['fail']} failed, {n['skip']} skipped)\n")
    return 0 if summary["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
