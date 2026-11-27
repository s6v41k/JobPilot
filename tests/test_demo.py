"""Demo mode: scenarios, recordings, replay, preparation. No model is loaded and no network is used."""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, demo, main
from app.config import Settings, get_settings
from app.tools import TOOLS

DOC = Path(__file__).parent.parent / "docs" / "demo_script.md"
SNAPSHOT = json.loads((Path(__file__).parent.parent / "data" / "snapshots" / "forem_2026-11-27.json")
                      .read_text(encoding="utf-8"))["results"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(demo, "RECORDINGS_DIR", tmp_path / "rec")
    monkeypatch.setattr(main, "_agent", None)
    monkeypatch.setattr(main._warmer, "trigger", lambda: None)


RUN = [  # what a real run looks like (shape of the events the agent yields)
    {"type": "tool_call", "name": "search_jobs", "arguments": {"city": "Brussels"}},
    {"type": "tool_result", "name": "search_jobs", "ok": True, "error": None, "duration_ms": 40, "data": {
        "count": 1, "data_source": "snapshot", "snapshot_date": "2026-11-27", "attribution": [{"text": "Le Forem", "url": "u"}],
        "jobs": [{"id": "forem:777", "title": "Serveur - Job etudiant", "company": "Accent", "city": "Ixelles",
                  "region": "brussels", "job_type": "student", "language": "fr", "posted_at": "2026-11-26",
                  "url": "https://x/777", "summary": "Occupation: Serveur"}]}},
    {"type": "token", "text": "Found "}, {"type": "token", "text": "one."},
    {"type": "done", "text": "Found one.", "stats": {"model": "m", "elapsed_s": 1}},
]


def recording(**over):
    rec = demo.capture(iter(RUN), "Find jobs", "S1", "qwen-test", "test data")
    rec.update(over)
    return rec


# ---------------------------------------------------------------------- scenarios
def test_scenarios_are_consistent_with_the_tools_and_the_written_script():
    ids = [sc.id for sc in demo.SCENARIOS]
    assert len(ids) == len(set(ids)) >= 4
    script = DOC.read_text(encoding="utf-8")
    for sc in demo.SCENARIOS:
        assert set(sc.expect) <= set(TOOLS), sc.id                          # only real tools
        assert sc.prompt in script, f"docs/demo_script.md must contain the prompt of {sc.id}"


def test_get_scenario_is_case_insensitive_and_helpful():
    assert demo.get_scenario("s1").id == "S1"
    with pytest.raises(KeyError, match="Available: S1"):
        demo.get_scenario("S99")


# --------------------------------------------------------------------- recordings
def test_capture_save_list_load_roundtrip(tmp_path):
    rec = recording()
    assert [e["type"] for e in rec["events"]] == [e["type"] for e in RUN] and all("t" in e for e in rec["events"])
    demo.save_recording(rec)
    listed = demo.list_recordings()
    assert [r["id"] for r in listed] == ["S1"] and listed[0]["model"] == "qwen-test" and "events" not in listed[0]
    assert demo.load_recording("S1")["question"] == "Find jobs"


def test_unknown_or_malicious_recording_ids_are_refused():
    for bad in ("nope", "../../etc/passwd", "..", ""):
        with pytest.raises(FileNotFoundError):
            demo.load_recording(bad)


def test_a_broken_recording_file_does_not_break_the_list():
    demo.save_recording(recording())
    (demo.RECORDINGS_DIR / "broken.json").write_text("{not json", encoding="utf-8")
    assert [r["id"] for r in demo.list_recordings()] == ["S1"]


# -------------------------------------------------------------------------- replay
def test_replay_starts_with_a_label_and_shortens_waits():
    rec = recording()
    for i, event in enumerate(rec["events"]):
        event["t"] = [0.0, 100.0, 100.5, 100.6, 220.0][i]                   # a 220 s run
    slept = []
    events = list(demo.replay_events(rec, sleep=slept.append))
    assert events[0]["type"] == "replay" and events[0]["model"] == "qwen-test" and events[0]["recording"] == "S1"
    assert [e["type"] for e in events[1:]] == ["tool_call", "tool_result", "token", "token", "done"]
    assert all("t" not in e for e in events)
    assert max(slept) <= 2.0 and sum(slept) < 6                               # 220 s became a few seconds
    assert all(s <= 0.02 for s, e in zip(slept[2:4], events[3:5]) if e["type"] == "token")


def test_replay_stores_the_recorded_vacancies_so_save_works():
    list(demo.replay_events(recording(), sleep=lambda s: None))
    with db.connect() as conn:
        job = db.get_job(conn, "forem:777")
    assert job["title"] == "Serveur - Job etudiant" and job["is_snapshot"] == 1 and job["url"] == "https://x/777"


# ----------------------------------------------------------------------------- API
@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def parse_sse(text):
    return [(b.split("\n")[0][7:], json.loads(b.split("\n")[1][6:])) for b in text.strip().split("\n\n")]


def test_demo_endpoint_lists_scenarios_and_recordings(client):
    before = client.get("/api/demo").json()["scenarios"]
    assert [s["id"] for s in before] == ["S1", "S2", "S3", "S4"] and all(s["recording"] is None for s in before)
    demo.save_recording(recording())
    after = {s["id"]: s for s in client.get("/api/demo").json()["scenarios"]}
    assert after["S1"]["recording"]["model"] == "qwen-test" and after["S2"]["recording"] is None


def test_chat_can_replay_a_recording_and_it_is_labelled(client, monkeypatch):
    demo.save_recording(recording())
    monkeypatch.setattr(demo.time, "sleep", lambda s: None)
    events = parse_sse(client.post("/api/chat", json={"message": "Find jobs", "replay": "S1"}).text)
    assert [e for e, _ in events] == ["replay", "tool_call", "tool_result", "token", "token", "done"]
    assert events[0][1]["model"] == "qwen-test"
    assert main._agent is None                                              # the real agent was never touched


def test_replay_of_a_missing_recording_is_a_friendly_error(client):
    events = parse_sse(client.post("/api/chat", json={"message": "x", "replay": "S3"}).text)
    assert events[0][0] == "error" and "python -m app.demo record" in events[0][1]["message"]


def test_replay_id_is_validated(client):
    assert client.post("/api/chat", json={"message": "x", "replay": "../secret"}).status_code == 422


# ----------------------------------------------------------------------- recording
class FakeAgent:
    def __init__(self, model=None, events=None):
        self.events = events if events is not None else RUN

    def run(self, question):
        yield from self.events


def test_record_saves_a_real_run_and_warns_when_tools_differ(monkeypatch, tmp_path):
    monkeypatch.setattr("app.agent.agent.Agent", lambda model=None: FakeAgent())
    monkeypatch.setattr("app.warmup.BackgroundWarmer.warm_now", lambda self: {})
    said = []
    path = demo.record("S2", None, directory=tmp_path / "out", say=said.append)
    assert path.exists() and json.loads(path.read_text(encoding="utf-8"))["scenario"] == "S2"
    assert any("Saved S2.json" in line for line in said) and not any("WARNING" in line for line in said)
    said.clear()
    demo.record("S1", None, directory=tmp_path / "out", say=said.append)         # S1 expects match_cv too
    assert any("WARNING" in line and "match_cv" in line for line in said)


def test_a_failed_run_is_not_saved(monkeypatch, tmp_path):
    bad = [{"type": "error", "message": "Cannot reach Ollama."}]
    monkeypatch.setattr("app.agent.agent.Agent", lambda model=None: FakeAgent(events=bad))
    monkeypatch.setattr("app.warmup.BackgroundWarmer.warm_now", lambda self: {})
    with pytest.raises(RuntimeError, match="Cannot reach Ollama"):
        demo.record("S1", None, directory=tmp_path / "out", say=lambda t: None)
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.json"))


def test_record_needs_a_scenario_or_a_question():
    with pytest.raises(ValueError):
        demo.record(None, None)


def test_offline_environment_uses_the_real_snapshot_and_no_adzuna(monkeypatch):
    s = get_settings()
    for name in ("db_path", "adzuna_app_id", "adzuna_app_key", "today_override"):
        monkeypatch.setattr(s, name, getattr(s, name))
    monkeypatch.setattr(s, "adzuna_app_id", "id")
    monkeypatch.setattr(s, "adzuna_app_key", "key")
    note = demo.offline_environment()
    assert "snapshot of 2026-11-27" in note and s.adzuna_configured is False and s.today_override == "2026-11-27"
    with db.connect() as conn:
        assert db.forem_mirror_state(conn)["offers"] == len(SNAPSHOT) and db.load_profile(conn)["name"]


def test_today_override_hides_nothing_that_was_current_on_that_day():
    from app.providers.base import JobQuery
    from app.providers.forem import ForemProvider
    with db.connect() as conn:
        db.forem_replace_all(conn, SNAPSHOT)
    assert ForemProvider(settings=Settings(today_override="2026-11-27")).search(JobQuery(limit=5))
    assert ForemProvider(settings=Settings(today_override="2031-01-01")).search(JobQuery(limit=5)) == []


# ------------------------------------------------------------------------- prepare
def test_save_snapshot_from_the_local_list(tmp_path):
    assert demo.save_snapshot(directory=tmp_path) is None                    # no local list yet
    with db.connect() as conn:
        db.forem_replace_all(conn, SNAPSHOT)
    path = demo.save_snapshot(count=60, directory=tmp_path, today=date(2026, 11, 27))
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [r["numerooffreforem"] for r in data["results"]]
    assert path.name == "forem_2026-11-27.json" and ids and len(ids) == len(set(ids))
    assert all(i in {r["numerooffreforem"] for r in SNAPSHOT} for i in ids)          # only real offers


def test_prepare_runs_every_step_and_never_raises(monkeypatch):
    from app import preflight
    from app.providers import forem_mirror
    monkeypatch.setattr(forem_mirror, "refresh_if_stale", lambda max_age_hours=0: "Le Forem copy NOT refreshed: quota")
    monkeypatch.setattr(demo, "save_snapshot", lambda: 1 / 0)
    monkeypatch.setattr("app.warmup.BackgroundWarmer.warm_now", lambda self: {"message": "model ready"})
    monkeypatch.setattr(preflight, "run_checks", lambda chat=False: [preflight.Check("Ollama", "ok", "fine")])
    lines = []
    assert demo.prepare(say=lines.append) is True
    text = "\n".join(lines)
    assert "NOT refreshed: quota" in text and "could not save a snapshot" in text and "model ready" in text and "READY" in text


def test_prepare_reports_not_ready_when_preflight_fails(monkeypatch):
    from app import preflight
    from app.providers import forem_mirror
    monkeypatch.setattr(forem_mirror, "refresh_if_stale", lambda max_age_hours=0: "ok")
    monkeypatch.setattr(demo, "save_snapshot", lambda: None)
    monkeypatch.setattr(preflight, "run_checks", lambda chat=False: [preflight.Check("Ollama", "fail", "down")])
    assert demo.prepare(say=lambda t: None, warm=False) is False


# ---------------------------------------------------------------------------- CLI
def test_cli_list_and_missing_arguments(capsys):
    assert demo.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "S1" in out and "no recording yet" in out and "Zoek een stage in Antwerpen" in out
    assert demo.main(["record"]) == 1
    assert "Give --scenario or --question" in capsys.readouterr().out
