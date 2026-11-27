import json

import pytest

from app import tools
from app.providers.aggregator import SearchOutcome
from app.providers.base import Job
from app.tools import call_tool, tool_schemas


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))


@pytest.fixture
def fake_search(monkeypatch):
    seen = {}

    def fake(query):
        seen["query"] = query
        job = Job(id="t:1", source="t", external_id="1", title="Test barista", company="TestCo",
                  city="Gent", region="flanders", description="word " * 100)
        return SearchOutcome(jobs=[job], attribution=[{"text": "x", "url": "y"}])

    monkeypatch.setattr("app.tools.search_jobs.search_all", fake)
    return seen


def test_schema_is_simple_for_small_models():
    schema = next(s for s in tool_schemas() if s["function"]["name"] == "search_jobs")
    params = schema["function"]["parameters"]
    assert schema["type"] == "function"
    assert params["properties"]["job_type"]["enum"] == ["student", "part-time", "internship", "full-time"]
    assert "anyOf" not in json.dumps(params) and "title" not in params["properties"]["city"]
    assert params["required"] == []


def test_search_jobs_ok(fake_search):
    res = call_tool("search_jobs", {"keywords": "barista", "city": "Gent", "job_type": "student"})
    assert res["ok"] and res["error"] is None
    assert res["data"]["count"] == 1 and res["data"]["jobs"][0]["id"] == "t:1"
    assert len(res["data"]["jobs"][0]["summary"]) <= 220          # keeps the LLM prompt small
    assert fake_search["query"].city == "Gent"


def test_blank_and_null_optionals_are_ignored(fake_search):
    res = call_tool("search_jobs", {"keywords": "x", "city": "", "job_type": None, "region": "null"})
    assert res["ok"]


def test_arguments_may_arrive_as_json_string(fake_search):
    assert call_tool("search_jobs", '{"keywords": "x"}')["ok"]
    assert call_tool("search_jobs", "")["ok"]


def test_invalid_argument_gives_helpful_error(fake_search):
    res = call_tool("search_jobs", {"job_type": "freelance"})
    assert not res["ok"] and "job_type" in res["error"] and res["data"] is None
    assert not call_tool("search_jobs", {"limit": 500})["ok"]


def test_unknown_tool_lists_available_ones():
    res = call_tool("hack_the_planet", {})
    assert not res["ok"] and "search_jobs" in res["error"]


def test_exception_inside_tool_is_caught(monkeypatch):
    def boom(query):
        raise RuntimeError("database exploded")
    monkeypatch.setattr("app.tools.search_jobs.search_all", boom)
    res = call_tool("search_jobs", {})
    assert res == {"ok": False, "data": None, "error": "database exploded"}


def test_garbage_arguments_never_raise():
    for bad in ("{not json", 42, ["a"]):
        assert call_tool("search_jobs", bad)["ok"] is False


def test_every_call_is_logged(fake_search, tmp_path):
    call_tool("search_jobs", {"keywords": "k" * 500})
    call_tool("search_jobs", {"job_type": "nope"})
    lines = (tmp_path / "logs" / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
    first, second = json.loads(lines[0]), json.loads(lines[1])
    assert first["tool"] == "search_jobs" and first["ok"] is True and "duration_ms" in first
    assert len(first["args"]["keywords"]) < 200                    # long text is truncated in logs
    assert second["ok"] is False and second["error"]
