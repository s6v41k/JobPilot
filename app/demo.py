"""Demo mode: everything that helps the live presentation go well.

    python -m app.demo list                        # the scripted scenarios and the recordings we have
    python -m app.demo prepare                     # the day before: fresh vacancies, fresh real snapshot, warm-up, pre-flight
    python -m app.demo record --scenario S1        # run a scenario with the REAL model and save everything it does
    python -m app.demo record --scenario S1 --offline   # same, from the saved snapshot (no internet, no Adzuna call)

A *recording* is a real run: every token, tool call and tool result of the real model, with the time it took.
If the model is too slow or breaks during the demo, the UI can **replay** a recording. Replays are always labelled
("REPLAY of a real recording from <date>") - they are never passed off as live answers.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from app import db
from app.config import ROOT_DIR, get_settings

RECORDINGS_DIR = ROOT_DIR / "data" / "demo_recordings"
SNAPSHOT_DIR = ROOT_DIR / "data" / "snapshots"


# ---------------------------------------------------------------------- scenarios
@dataclass
class Scenario:
    id: str
    title: str
    prompt: str
    expect: list[str]                    # the tools the agent should call (what we point at on the screen)
    minutes: float                       # how long it takes LIVE on the demo laptop (warm model)
    say: str                             # one sentence for the presenter
    tags: list[str] = field(default_factory=list)


# The prompts here are the ones in docs/demo_script.md (a test keeps them identical).
SCENARIOS: list[Scenario] = [
    Scenario("S1", "Find, compare, decide",
             "Find student jobs in Brussels and tell me which one fits my CV best",
             ["search_jobs", "match_cv"], 2.5,
             "Nobody told the agent to call match_cv: it decided to compare the vacancies with my CV by itself.",
             ["multi-step", "tool trace"]),
    Scenario("S2", "Dutch internship",
             "Zoek een stage in Antwerpen",
             ["search_jobs"], 1.0,
             "It answers in the language I wrote in, and searched the Dutch and French words for me.",
             ["language"]),
    Scenario("S3", "French student job",
             "Chercher un job étudiant à Liège",
             ["search_jobs"], 1.0,
             "Same agent, third language. Every vacancy comes from the real Le Forem data.",
             ["language"]),
    Scenario("S4", "Nothing is invented",
             "Is there a job as an astronaut in Mechelen?",
             ["search_jobs"], 1.0,
             "When there is nothing, it says so. Answers with vacancies that no tool returned are thrown away.",
             ["honesty", "guard"]),
]


def get_scenario(scenario_id: str) -> Scenario:
    for sc in SCENARIOS:
        if sc.id.lower() == scenario_id.lower():
            return sc
    raise KeyError(f"Unknown scenario '{scenario_id}'. Available: {', '.join(s.id for s in SCENARIOS)}")


# ---------------------------------------------------------------------- recordings
def _dir(directory: Path | None) -> Path:
    return directory or RECORDINGS_DIR


def list_recordings(directory: Path | None = None) -> list[dict]:
    """Metadata of every recording (without the events)."""
    out = []
    for file in sorted(_dir(directory).glob("*.json")):
        try:
            rec = json.loads(file.read_text(encoding="utf-8"))
            out.append({k: rec.get(k) for k in ("id", "scenario", "question", "recorded_at", "model",
                                                "original_seconds", "data_note")})
        except Exception:
            continue                                   # a broken file must not break the demo page
    return out


def load_recording(recording_id: str, directory: Path | None = None) -> dict:
    safe = "".join(ch for ch in recording_id if ch.isalnum() or ch in "-_")
    path = _dir(directory) / f"{safe}.json"
    if not safe or not path.exists():
        raise FileNotFoundError(f"There is no recording '{recording_id}'. Record one with: python -m app.demo record --scenario S1")
    return json.loads(path.read_text(encoding="utf-8"))


def capture(events: Iterator[dict], question: str, recording_id: str, model: str, data_note: str = "") -> dict:
    """Turn a live event stream into a recording (consumes the stream)."""
    started = time.perf_counter()
    captured = [{"t": round(time.perf_counter() - started, 2), **event} for event in events]
    return {
        "version": 1, "id": recording_id, "scenario": recording_id, "question": question, "model": model,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "original_seconds": round(time.perf_counter() - started, 1), "data_note": data_note, "events": captured,
    }


def save_recording(rec: dict, directory: Path | None = None) -> Path:
    folder = _dir(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec['id']}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def ensure_recorded_jobs(rec: dict) -> int:
    """Put the vacancies of a recording into the local database, so Save / Details work during a replay.
    (They are real vacancies from the day of the recording, stored as 'snapshot' data.)"""
    rows = {}
    for event in rec.get("events", []):
        if event.get("type") != "tool_result" or not isinstance(event.get("data"), dict):
            continue
        for job in event["data"].get("jobs") or []:
            source, _, external = job["id"].partition(":")
            rows[job["id"]] = {"id": job["id"], "source": source, "external_id": external or job["id"],
                               "title": job.get("title") or job["id"], "company": job.get("company"),
                               "city": job.get("city"), "region": job.get("region"), "job_type": job.get("job_type"),
                               "language": job.get("language"), "posted_at": job.get("posted_at"),
                               "url": job.get("url"), "description": job.get("summary"), "is_snapshot": True}
    with db.connect() as conn:
        for row in rows.values():
            db.upsert_job(conn, row)
    return len(rows)


def replay_events(rec: dict, sleep: Callable[[float], None] = time.sleep, max_gap: float = 2.0,
                  token_delay: float = 0.02) -> Iterator[dict]:
    """Play a recording back, with long waits shortened (max `max_gap` s) so a 4-minute run takes ~15 s.

    The first event is {"type": "replay", ...}: the UI uses it to show the 'REPLAY' label."""
    ensure_recorded_jobs(rec)
    yield {"type": "replay", "recording": rec["id"], "recorded_at": rec.get("recorded_at"), "model": rec.get("model"),
           "original_seconds": rec.get("original_seconds"), "data_note": rec.get("data_note", "")}
    previous = 0.0
    for event in rec["events"]:
        gap = min(max(event.get("t", 0) - previous, 0.0), max_gap)
        previous = event.get("t", previous)
        sleep(min(gap, token_delay) if event["type"] == "token" else gap)
        yield {k: v for k, v in event.items() if k != "t"}


# ------------------------------------------------------------------------ recording a real run
def newest_snapshot_date() -> str | None:
    from app.providers.snapshot import SnapshotProvider
    return SnapshotProvider().snapshot_date()


def offline_environment(profile_name: str | None = "emma_verbeke") -> str:
    """Point the app at a throw-away database seeded from the newest REAL snapshot: no internet, no Adzuna.
    Returns a short note describing the data. (Used for recordings, never for the live app.)"""
    from eval.run_scenario import sample_profile
    snapshots = sorted(SNAPSHOT_DIR.glob("forem_*.json"))
    if not snapshots:
        raise RuntimeError("There is no saved Le Forem snapshot in data/snapshots/ to record from.")
    newest = snapshots[-1]
    when = newest.stem.split("_", 1)[1]
    settings = get_settings()
    settings.update(db_path=str(Path(tempfile.mkdtemp()) / "record.db"), adzuna_app_id="", adzuna_app_key="",
                    today_override=when)
    records = json.loads(newest.read_text(encoding="utf-8"))["results"]
    with db.connect() as conn:
        db.forem_replace_all(conn, records)
        if profile_name:
            profile, cv_text = sample_profile(profile_name)
            db.save_profile(conn, profile, cv_text=cv_text)
    cv = f"fictional CV '{profile_name}'" if profile_name else "no CV profile"
    return f"real Le Forem snapshot of {when} ({len(records)} offers), {cv}"


def record(scenario_id: str | None, question: str | None, model: str | None = None, offline: bool = False,
           directory: Path | None = None, say: Callable[[str], None] = print) -> Path:
    """Run a question through the REAL agent and save the recording."""
    from app.agent.agent import Agent
    from app.warmup import BackgroundWarmer
    scenario = get_scenario(scenario_id) if scenario_id else None
    question = question or (scenario.prompt if scenario else None)
    if not question:
        raise ValueError("Give --scenario or --question.")
    note = offline_environment() if offline else "live data (as the app sees it right now)"
    settings = get_settings()
    model = model or settings.ollama_model
    say(f"Recording '{scenario.id if scenario else 'custom'}' with {model} on {note}")
    say("Warming the model first (not part of the recording)...")
    BackgroundWarmer().warm_now()
    say(f"Question: {question}")
    agent = Agent(model=model)
    rec = capture(agent.run(question), question, scenario.id if scenario else "custom", model, note)
    errors = [e for e in rec["events"] if e["type"] == "error"]
    if errors:
        raise RuntimeError("The run ended with an error, nothing saved: " + errors[0]["message"])
    path = save_recording(rec, directory)
    tools = [e["name"] for e in rec["events"] if e["type"] == "tool_call"]
    say(f"Saved {path.name}: {rec['original_seconds']} s, tools {tools}")
    if scenario and tools != scenario.expect:
        say(f"WARNING: the agent called {tools} but the script expects {scenario.expect}. Re-record, or change the script.")
    return path


# -------------------------------------------------------------------------- prepare
def save_snapshot(count: int = 300, directory: Path | None = None, today: date | None = None) -> Path | None:
    """Write today's real offers (from the local list) to data/snapshots/forem_<date>.json: the offline safety net."""
    today = today or date.today()
    with db.connect() as conn:
        if not db.forem_mirror_state(conn):
            return None
        picked: dict[str, dict] = {}
        for job_type, share in (("student", 0.4), ("internship", 0.2), ("part-time", 0.2), (None, 0.2)):
            for region in ("brussels", "flanders", "wallonia"):
                nuts = {"brussels": "BE1", "flanders": "BE2", "wallonia": "BE3"}[region]
                for rec in db.forem_search(conn, today.isoformat(), nuts=nuts, job_type=job_type,
                                           limit=max(5, int(count * share / 3))):
                    picked[rec["numerooffreforem"]] = rec
    folder = directory or SNAPSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"forem_{today.isoformat()}.json"
    path.write_text(json.dumps({"fetched": today.isoformat(), "source": "ODWB offres-d-emploi-forem (CC BY-SA 4.0)",
                                "results": list(picked.values())}, ensure_ascii=False), encoding="utf-8")
    return path


def prepare(say: Callable[[str], None] = print, warm: bool = True) -> bool:
    """The routine for the day before the demo. Returns True when the pre-flight has no red lines."""
    from app import preflight
    from app.providers import forem_mirror
    from app.warmup import BackgroundWarmer
    say("1/4 Downloading the current Le Forem vacancies (one request)...")
    say("    " + forem_mirror.refresh_if_stale(max_age_hours=0))
    say("2/4 Saving a fresh real snapshot as the offline safety net...")
    try:
        path = save_snapshot()
        say(f"    saved {path.name}" if path else "    skipped: there is no local vacancy list yet")
    except Exception as e:
        say(f"    could not save a snapshot ({type(e).__name__}: {e})")
    if warm:
        say("3/4 Loading the model and warming it up (about a minute and a half)...")
        say("    " + BackgroundWarmer().warm_now()["message"])
    else:
        say("3/4 Warm-up skipped.")
    say("4/4 Pre-flight check:")
    checks = preflight.run_checks(chat=False)
    for c in checks:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "skip": "skip"}[c.status]
        say(f"    [{mark}] {c.name}: {c.detail}")
    summary = preflight.summarize(checks)
    say(f"\n{summary['verdict']}. Next: record the scripted scenarios once, on this laptop:  python -m app.demo record --scenario S1")
    return summary["ready"]


# ------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show scenarios and recordings")
    p = sub.add_parser("prepare", help="get everything ready the day before")
    p.add_argument("--no-warm", action="store_true", help="skip loading the model")
    r = sub.add_parser("record", help="record a real run")
    r.add_argument("--scenario", help="S1, S2, ...")
    r.add_argument("--question", help="a custom question instead of a scenario")
    r.add_argument("--model")
    r.add_argument("--offline", action="store_true", help="use the saved snapshot, no internet, no Adzuna")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        have = {rec["id"]: rec for rec in list_recordings()}
        for sc in SCENARIOS:
            rec = have.get(sc.id)
            status = f"recorded {rec['recorded_at'][:10]} ({rec['model']}, {rec['original_seconds']} s)" if rec else "no recording yet"
            print(f"{sc.id}  {sc.title:<22} ~{sc.minutes} min live   {status}\n    \"{sc.prompt}\"")
        return 0
    if args.cmd == "prepare":
        return 0 if prepare(warm=not args.no_warm) else 1
    try:
        record(args.scenario, args.question, args.model, args.offline)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
