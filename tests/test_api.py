"""API smoke tests with FastAPI's TestClient. No real Ollama, no internet. Rows are TEST DATA."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import db, llm, main
from app.agent.agent import Agent
from app.config import Settings, get_settings
from app.providers.aggregator import SearchOutcome
from app.providers.base import Job

SAMPLE_CV = (Path(__file__).parent.parent / "data" / "sample_cvs" / "emma_verbeke.txt").read_bytes()
PROFILE = {"name": "Emma", "summary": "student", "skills": ["Excel"], "languages": ["Dutch (native)"],
           "education": [], "experience": [], "interests": [], "city": None}


@pytest.fixture
def client(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(main, "_agent", None)
    monkeypatch.setattr("app.agent.agent.load_stored_profile", lambda: None)
    monkeypatch.setattr(main, "installed_models", lambda: ["qwen2.5:7b", "bge-m3:latest", "llama3.2:3b"])
    monkeypatch.setattr(main, "_ollama_get", lambda path: {"version": "9.9.9", "models": []})
    return TestClient(main.app, raise_server_exceptions=False)


def add_job(n=1, **over):
    row = {"id": f"t:{n}", "source": "t", "external_id": str(n), "title": f"Test job {n}", "company": "TestCo",
           "city": "Gent", "language": "nl", "description": "Klantendienst en kassa", "url": "https://x/1"}
    row.update(over)
    with db.connect() as conn:
        db.upsert_job(conn, row)


# ---------------------------------------------------------------------- health
def test_health_when_everything_is_fine(client):
    data = client.get("/api/health").json()
    assert data["ok"] and data["ollama"]["version"] == "9.9.9"
    assert data["models"]["chat"]["installed"] is True and data["models"]["embed"]["installed"] is True
    assert data["database"]["ok"] and data["adzuna"]["monthly_budget"] == 25


def test_health_when_ollama_is_down_still_answers(client, monkeypatch):
    monkeypatch.setattr(main, "installed_models", lambda: None)
    data = client.get("/api/health").json()
    assert data["ok"] is False and "Ollama is not running" in data["ollama"]["message"]
    assert data["database"]["ok"] is True            # the non-LLM parts keep working


# ------------------------------------------------------------------------ jobs
def test_jobs_endpoint_passes_filters_and_returns_source_info(client, monkeypatch):
    seen = {}

    def fake_search(query):
        seen["q"] = query
        job = Job(id="t:1", source="t", external_id="1", title="Test job", city="Gent", region="flanders")
        return SearchOutcome(jobs=[job], data_source="snapshot", snapshot_date="2026-11-27",
                             notes=["offline"], attribution=[{"text": "x", "url": "y"}])
    monkeypatch.setattr(main, "search_all", fake_search)
    data = client.get("/api/jobs", params={"keywords": "kok", "job_type": "student", "city": "Gent",
                                           "region": "flanders", "limit": 5}).json()
    assert seen["q"].job_type == "student" and seen["q"].city == "Gent" and seen["q"].limit == 5
    assert data["count"] == 1 and data["data_source"] == "snapshot" and data["notes"] == ["offline"]


def test_jobs_invalid_filter_gives_friendly_422(client):
    res = client.get("/api/jobs", params={"job_type": "freelance", "limit": 500})
    assert res.status_code == 422 and res.json()["error"].startswith("Invalid request")


def test_jobs_with_match_scores(client, monkeypatch):
    with db.connect() as conn:
        db.save_profile(conn, PROFILE)
    monkeypatch.setattr(main, "search_all", lambda q: SearchOutcome(jobs=[
        Job(id="t:1", source="t", external_id="1", title="Kassamedewerker", description="Klantendienst en kassa",
            language="nl")]))
    monkeypatch.setattr(llm, "embed", lambda texts: (_ for _ in ()).throw(llm.LLMError("no embeddings")))
    job = client.get("/api/jobs", params={"match": "true"}).json()["jobs"][0]
    assert job["match"]["score"] > 0 and "unavailable" in job["match"]["reason"]


def test_job_details_and_unknown_job(client):
    add_job()
    assert client.get("/api/jobs/t:1").json()["title"] == "Test job 1"
    res = client.get("/api/jobs/t:404")
    assert res.status_code == 404 and "No vacancy" in res.json()["error"]


def test_tool_endpoint(client):
    add_job()
    res = client.post("/api/tools/get_job_details", json={"job_id": "t:1"}).json()
    assert res["ok"] and res["data"]["company"] == "TestCo"
    assert client.post("/api/tools/get_job_details", json={}).json()["ok"] is False   # bad arguments: ok=false, not a crash
    assert client.post("/api/tools/rm_rf", json={}).status_code == 404


# ------------------------------------------------------------------------- chat
def say(*pieces):
    return [SimpleNamespace(message=SimpleNamespace(content=p, tool_calls=None), done=False) for p in pieces] + [
        SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), done=True, eval_count=3,
                        eval_duration=10**9, prompt_eval_count=5, total_duration=10**9)]


class FakeOllama:
    def __init__(self, *script):
        self.script = list(script)

    def chat(self, **kw):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter(item)


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def use_agent(monkeypatch, *script):
    monkeypatch.setattr(main, "_agent", Agent(settings=Settings(), client=FakeOllama(*script)))


def test_chat_streams_tokens_and_done(client, monkeypatch):
    use_agent(monkeypatch, say("Hello ", "world"))
    res = client.post("/api/chat", json={"message": "hi"})
    assert res.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(res.text)
    assert [e for e, _ in events] == ["token", "token", "done"]
    assert events[-1][1]["text"] == "Hello world"


def test_chat_ollama_down_is_an_error_event_not_a_crash(client, monkeypatch):
    use_agent(monkeypatch, ConnectionError("x"), ConnectionError("x"))
    monkeypatch.setattr("app.agent.agent.time.sleep", lambda s: None)
    events = parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    assert events[0][0] == "error" and "Cannot reach Ollama" in events[0][1]["message"]


def test_chat_rejects_empty_message(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_chat_busy_gives_a_polite_message(client, monkeypatch):
    use_agent(monkeypatch, say("x"))
    assert main._chat_lock.acquire(blocking=False)
    try:
        events = parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    finally:
        main._chat_lock.release()
    assert events[0][0] == "error" and "still being answered" in events[0][1]["message"]
    assert not main._chat_lock.locked()


def test_chat_reset(client, monkeypatch):
    use_agent(monkeypatch, say("a"))
    client.post("/api/chat", json={"message": "hi"})
    assert main.get_agent().history
    client.post("/api/chat/reset")
    assert main.get_agent().history == []


# ---------------------------------------------------------------------- profile
def test_cv_upload_text_and_pasted_text(client):
    res = client.post("/api/profile/cv", files={"file": ("cv.txt", SAMPLE_CV, "text/plain")}).json()
    assert res["ok"] and res["cv_chars"] > 500
    assert client.get("/api/profile").json()["has_cv"] is True
    pasted = client.post("/api/profile/cv", data={"text": "I am a student who speaks Dutch and English well."}).json()
    assert pasted["ok"]


@pytest.mark.parametrize("kwargs,message", [
    ({"files": {"file": ("cv.txt", b"", "text/plain")}}, "empty"),
    ({"data": {"text": "   "}}, "Upload a PDF"),
    ({"files": {"file": ("cv.txt", b"\x00\x01" * 100, "text/plain")}}, "not supported"),
])
def test_cv_upload_problems_are_400_with_a_message(client, kwargs, message):
    res = client.post("/api/profile/cv", **kwargs)
    assert res.status_code == 400 and message in res.json()["error"]


def test_scanned_pdf_upload(client):
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.write(buf)
    res = client.post("/api/profile/cv", files={"file": ("scan.pdf", buf.getvalue(), "application/pdf")})
    assert res.status_code == 400 and "No text found" in res.json()["error"]


def test_analyze_needs_cv_then_saves_profile(client, monkeypatch):
    assert client.post("/api/profile/analyze").status_code == 400
    client.post("/api/profile/cv", files={"file": ("cv.txt", SAMPLE_CV, "text/plain")})
    fake = SimpleNamespace(chat=lambda **kw: SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"name": "Emma Verbeke", "skills": ["Excel"], "languages": ["Dutch (native)"]}))))
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    res = client.post("/api/profile/analyze").json()
    assert res["profile"]["name"] == "Emma Verbeke"
    assert client.get("/api/profile").json()["profile"]["skills"] == ["Excel"]


def test_analyze_reports_ollama_problems_as_503(client, monkeypatch):
    client.post("/api/profile/cv", files={"file": ("cv.txt", SAMPLE_CV, "text/plain")})
    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(
        chat=lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))))
    res = client.post("/api/profile/analyze")
    assert res.status_code == 503 and "Cannot reach Ollama" in res.json()["error"]


def test_profile_can_be_edited(client):
    assert client.put("/api/profile", json=PROFILE).json()["ok"]
    assert client.get("/api/profile").json()["profile"]["skills"] == ["Excel"]
    assert client.put("/api/profile", json={"skills": "not a list"}).status_code == 422


# ----------------------------------------------------------------- applications
def test_application_lifecycle(client):
    add_job(1)
    add_job(2)
    assert client.post("/api/applications", json={"job_id": "t:1"}).json()["saved"]["status"] == "interested"
    assert client.post("/api/applications", json={"job_id": "t:404"}).status_code == 400
    assert client.patch("/api/applications/t:1", json={"status": "interview", "notes": "Monday"}).json()[
        "updated"]["status"] == "interview"
    assert client.patch("/api/applications/t:2", json={"status": "applied"}).status_code == 404
    client.post("/api/applications", json={"job_id": "t:2", "status": "applied"})
    assert client.get("/api/applications").json()["count"] == 2
    assert client.get("/api/applications", params={"status": "applied"}).json()["count"] == 1
    assert client.delete("/api/applications/t:1").json()["ok"]
    assert client.delete("/api/applications/t:1").status_code == 404
    assert client.post("/api/applications", json={"job_id": "t:1", "status": "hired"}).status_code == 422


# ------------------------------------------------------------------------ model
def test_model_switching(client, monkeypatch):
    monkeypatch.setattr(main, "_agent", Agent(settings=Settings(), client=FakeOllama()))
    for name in ("ollama_model", "num_ctx", "think"):                    # the API changes global settings:
        monkeypatch.setattr(get_settings(), name, getattr(get_settings(), name))   # restore them afterwards
    res = client.put("/api/model", json={"model": "llama3.2:3b", "num_ctx": 4096, "think": True}).json()
    assert res["model"] == "llama3.2:3b" and res["num_ctx"] == 4096 and res["think"] is True
    assert main.get_agent().model == "llama3.2:3b"
    missing = client.put("/api/model", json={"model": "gpt-9000"})
    assert missing.status_code == 400 and "ollama pull gpt-9000" in missing.json()["error"]
    assert client.put("/api/model", json={"num_ctx": 10}).status_code == 422


def test_model_switch_when_ollama_is_down(client, monkeypatch):
    monkeypatch.setattr(main, "installed_models", lambda: None)
    assert client.put("/api/model", json={"model": "x"}).status_code == 503


# ---------------------------------------------------------------------- privacy
def test_delete_my_data(client):
    add_job()
    client.post("/api/profile/cv", files={"file": ("cv.txt", SAMPLE_CV, "text/plain")})
    client.post("/api/applications", json={"job_id": "t:1"})
    assert client.delete("/api/my-data").json()["ok"]
    assert client.get("/api/profile").json() == {"profile": None, "has_cv": False, "cv_chars": 0, "cv_preview": ""}
    assert client.get("/api/applications").json()["count"] == 0
    assert client.get("/api/jobs/t:1").status_code == 200            # public vacancies stay


# ----------------------------------------------------------------- vacancy copy
def test_health_reports_the_local_vacancy_copy(client):
    assert client.get("/api/health").json()["vacancies"]["offers"] == 0          # nothing downloaded yet


def test_refresh_endpoint(client, monkeypatch):
    monkeypatch.setattr(main.forem_mirror, "refresh", lambda **kw: 123)
    res = client.post("/api/refresh").json()
    assert res["ok"] and res["offers"] == 123


def test_refresh_failure_is_a_503_with_a_message(client, monkeypatch):
    def quota(**kw):
        raise main.ProviderError("Le Forem open data daily request limit reached; the saved copy is kept.")
    monkeypatch.setattr(main.forem_mirror, "refresh", quota)
    res = client.post("/api/refresh")
    assert res.status_code == 503 and "saved copy is kept" in res.json()["error"]


# ------------------------------------------------------------------- robustness
def test_unexpected_server_error_is_a_calm_json_message(client, monkeypatch):
    monkeypatch.setattr(main, "search_all", lambda q: 1 / 0)
    res = client.get("/api/jobs")
    assert res.status_code == 500 and res.json() == {"error": "Something went wrong on the server. Please try again."}


def test_homepage_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "JobPilot" in res.text
