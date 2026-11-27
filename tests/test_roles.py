"""Agent teams: agents.yaml, role resolution, and that each role really uses its own model/tools."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, llm, main
from app.agent import roles
from app.agent.agent import Agent
from app.agent.prompts import build_system_prompt
from app.config import get_settings
from app.tools import call_tool

GOOD = """
active: mix
profiles:
  mix:
    description: test team
    roles:
      orchestrator: {model: "chat-model:1b", num_ctx: 4096, think: true, tools: [search_jobs, match_cv]}
      cv_analyst:   {model: "cv-model:7b"}
      writer:       {model: default}
  plain:
    roles:
      orchestrator: {model: default}
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(s, "agent_profile", "")
    monkeypatch.setattr(s, "role_overrides", {})
    monkeypatch.setattr(s, "ollama_model", "default-model:7b")
    monkeypatch.setattr(s, "agents_file", str(tmp_path / "agents.yaml"))
    monkeypatch.setattr("app.agent.agent.load_stored_profile", lambda: None)
    monkeypatch.setattr(main._warmer, "trigger", lambda: None)
    roles._cache.update(key=None)
    return tmp_path


def write(tmp_path, text):
    (tmp_path / "agents.yaml").write_text(text, encoding="utf-8")
    roles._cache.update(key=None)


# ------------------------------------------------------------------- the shipped file
def test_the_shipped_agents_yaml_is_valid(monkeypatch):
    monkeypatch.setattr(get_settings(), "agents_file", "agents.yaml")
    roles._cache.update(key=None)
    all_teams, active, problems = roles.load()
    assert problems == [] and active == "default"
    assert {"default", "lean_tools", "small_chat", "qwen3_chat"} <= set(all_teams)
    assert set(all_teams["lean_tools"]["roles"]["orchestrator"]["tools"]) <= set(__import__("app.tools", fromlist=["TOOLS"]).TOOLS)


# ----------------------------------------------------------------------- resolution
def test_default_team_uses_the_default_model_everywhere(isolated):
    write(isolated, "active: default\nprofiles:\n  default:\n    roles:\n      orchestrator: {model: default}\n")
    for role in roles.ROLES:
        rc = roles.role_config(role)
        assert rc.model == "default-model:7b" and rc.source == "default model" and rc.tools is None


def test_team_models_per_role_and_per_role_options(isolated):
    write(isolated, GOOD)
    chat, cv, writer = (roles.role_config(r) for r in ("orchestrator", "cv_analyst", "writer"))
    assert (chat.model, chat.num_ctx, chat.think, chat.tools, chat.source) == ("chat-model:1b", 4096, True, ["search_jobs", "match_cv"], "team")
    assert cv.model == "cv-model:7b" and cv.num_ctx == get_settings().num_ctx and cv.think is get_settings().think
    assert writer.model == "default-model:7b" and writer.source == "default model"
    assert roles.models_in_use() == ["chat-model:1b", "cv-model:7b", "default-model:7b"]


def test_runtime_override_beats_the_team_and_can_be_removed(isolated, monkeypatch):
    write(isolated, GOOD)
    monkeypatch.setattr(get_settings(), "role_overrides", {"writer": "writer-model:3b", "orchestrator": "other:2b"})
    assert roles.role_config("writer").source == "override" and roles.model_for("writer") == "writer-model:3b"
    assert roles.model_for("orchestrator") == "other:2b" and roles.model_for("cv_analyst") == "cv-model:7b"
    monkeypatch.setattr(get_settings(), "role_overrides", {})
    assert roles.model_for("writer") == "default-model:7b"


def test_choosing_another_team_at_runtime(isolated, monkeypatch):
    write(isolated, GOOD)
    assert roles.active_name() == "mix"
    monkeypatch.setattr(get_settings(), "agent_profile", "plain")
    assert roles.active_name() == "plain" and roles.model_for("orchestrator") == "default-model:7b"
    monkeypatch.setattr(get_settings(), "agent_profile", "does-not-exist")
    assert roles.active_name() == "mix"                                          # unknown name: the file decides


def test_unknown_role_is_an_error():
    with pytest.raises(KeyError):
        roles.role_config("janitor")


# ------------------------------------------------------------------- broken files
@pytest.mark.parametrize("content,message", [
    ("profiles: [unclosed", "could not be read"),
    ("just a string", "no 'profiles' section"),
    ("profiles: {}", "no 'profiles' section|no usable team"),
    ("profiles:\n  a: 5\n", "not a mapping"),
])
def test_a_broken_file_falls_back_to_the_built_in_team_and_reports_it(isolated, content, message):
    write(isolated, content)
    all_teams, active, problems = roles.load()
    assert active == "default" and "default" in all_teams and problems
    assert any(__import__("re").search(message, p) for p in problems)
    assert roles.model_for("orchestrator") == "default-model:7b"                  # still works


def test_a_missing_file_falls_back(isolated):
    assert any("not found" in p for p in roles.problems())
    assert roles.model_for("writer") == "default-model:7b"


def test_bad_entries_are_ignored_with_a_warning_not_a_crash(isolated):
    write(isolated, """
active: nope
profiles:
  t:
    roles:
      orchestrator: {model: m1, num_ctx: 5, tools: [search_jobs, not_a_tool]}
      janitor: {model: x}
      writer: {model: m2, tools: [search_jobs]}
""")
    all_teams, active, problems = roles.load()
    text = " | ".join(problems)
    assert active == "t" and "active team 'nope' does not exist" in text
    assert "unknown role 'janitor'" in text and "unknown tools ['not_a_tool']" in text
    assert "num_ctx must be" in text and "only the orchestrator uses tools" in text
    assert roles.role_config("orchestrator").tools == ["search_jobs"]
    assert roles.role_config("orchestrator").num_ctx == get_settings().num_ctx
    assert roles.role_config("cv_analyst").model == "default-model:7b"            # missing role: default


def test_the_file_is_reread_when_it_changes(isolated):
    write(isolated, GOOD)
    assert roles.model_for("cv_analyst") == "cv-model:7b"
    write(isolated, GOOD.replace("cv-model:7b", "new-cv:3b"))
    assert roles.model_for("cv_analyst") == "new-cv:3b"


# ----------------------------------------------------- the roles really use their settings
class FakeChat:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            return iter([SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), done=True, eval_count=1,
                                         eval_duration=1, prompt_eval_count=1, total_duration=1)])
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


def test_the_chat_agent_uses_the_orchestrator_model_options_and_tools(isolated):
    write(isolated, GOOD)
    client = FakeChat("x")
    agent = Agent(settings=get_settings(), client=client)
    events = list(agent.run("hello there"))
    call = client.calls[0]
    assert call["model"] == "chat-model:1b" and call["options"]["num_ctx"] == 4096 and call["think"] is True
    assert {t["function"]["name"] for t in call["tools"]} == {"search_jobs", "match_cv"}
    assert "get_job_details" not in call["messages"][0]["content"]                 # the prompt lists only these tools
    assert events[-1]["stats"]["model"] == "chat-model:1b"


def test_a_pinned_model_beats_the_team(isolated):
    write(isolated, GOOD)
    client = FakeChat("x")
    list(Agent(settings=get_settings(), client=client, model="pinned:1b").run("hello"))
    assert client.calls[0]["model"] == "pinned:1b"


def test_the_prompt_only_has_rules_for_tools_the_agent_can_use():
    lean = build_system_prompt(tools=["search_jobs", "get_job_details"])
    full = build_system_prompt()
    assert "extract_profile" in full and "match_cv ONCE" in full
    assert "extract_profile" not in lean and "match_cv" not in lean
    numbers = [line.split(".")[0] for line in lean.split("RULES:\n")[1].split("\n\n")[0].splitlines()]
    assert numbers == [str(i) for i in range(1, len(numbers) + 1)]                 # still numbered 1..n


def test_cv_reading_uses_the_cv_analyst_model(isolated, monkeypatch):
    write(isolated, GOOD)
    client = FakeChat(json.dumps({"name": "Emma", "skills": ["Excel"]}))
    monkeypatch.setattr(llm, "get_client", lambda: client)
    with db.connect() as conn:
        db.save_cv_text(conn, "Emma is a student of business who has worked in a brasserie for two years.")
    assert call_tool("extract_profile", {})["ok"]
    assert client.calls[0]["model"] == "cv-model:7b"


def test_letters_use_the_writer_model(isolated, monkeypatch):
    write(isolated, GOOD.replace("writer:       {model: default}", "writer: {model: \"writer-model:3b\"}"))
    client = FakeChat("Dear hiring team,\n\nI would love to work with you.\n\nKind regards,\nEmma")
    monkeypatch.setattr(llm, "get_client", lambda: client)
    with db.connect() as conn:
        db.upsert_job(conn, {"id": "t:1", "source": "t", "external_id": "1", "title": "Waiter", "language": "en"})
        db.save_profile(conn, {"name": "Emma", "languages": ["English (native)"], "skills": [], "education": [],
                               "experience": [], "interests": [], "summary": ""})
    assert call_tool("draft_cover_letter", {"job_id": "t:1"})["ok"]
    assert client.calls[0]["model"] == "writer-model:3b"


def test_warm_up_primes_the_same_model_and_tools_as_the_chat_agent(isolated, monkeypatch):
    from app import warmup
    write(isolated, GOOD)
    client = FakeChat("x")
    monkeypatch.setattr(llm, "get_client", lambda: client)
    warmup.prime_chat()
    call = client.calls[0]
    assert call["model"] == "chat-model:1b" and call["options"]["num_ctx"] == 4096
    assert {t["function"]["name"] for t in call["tools"]} == {"search_jobs", "match_cv"}


# ------------------------------------------------------------------------------ API
@pytest.fixture
def client(isolated, monkeypatch):
    write(isolated, GOOD)
    monkeypatch.setattr(main, "_agent", None)
    monkeypatch.setattr(main, "installed_models", lambda: ["chat-model:1b", "cv-model:7b", "default-model:7b", "other:2b"])
    monkeypatch.setattr(main, "_ollama_get", lambda path: {"version": "x", "models": []})
    return TestClient(main.app, raise_server_exceptions=False)


def test_agents_endpoint_describes_teams_and_resolved_models(client):
    data = client.get("/api/agents").json()
    assert data["active"] == "mix" and {t["name"] for t in data["teams"]} == {"mix", "plain"}
    assert [r["id"] for r in data["roles"]] == ["orchestrator", "cv_analyst", "writer"]
    assert data["resolved"]["orchestrator"]["model"] == "chat-model:1b" and data["resolved"]["orchestrator"]["tools"] == ["search_jobs", "match_cv"]
    assert data["resolved"]["writer"] == {"model": "default-model:7b", "source": "default model",
                                          "num_ctx": get_settings().num_ctx, "think": get_settings().think, "tools": "all"}
    assert data["problems"] == [] and "other:2b" in data["installed"]


def test_switching_team_and_overriding_a_role(client):
    assert client.put("/api/agents", json={"profile": "plain"}).json()["active"] == "plain"
    res = client.put("/api/agents", json={"overrides": {"writer": "other:2b"}}).json()
    assert res["resolved"]["writer"]["model"] == "other:2b" and res["resolved"]["writer"]["source"] == "override"
    assert client.put("/api/agents", json={"overrides": {"writer": None}}).json()["overrides"] == {}
    client.put("/api/agents", json={"overrides": {"writer": "other:2b", "cv_analyst": "other:2b"}})
    assert client.put("/api/agents", json={"clear_overrides": True}).json()["overrides"] == {}


def test_a_change_is_all_or_nothing(client):
    before = client.get("/api/agents").json()
    bad = client.put("/api/agents", json={"profile": "plain", "overrides": {"writer": "missing:9b"}})
    assert bad.status_code == 400 and "ollama pull missing:9b" in bad.json()["error"]
    after = client.get("/api/agents").json()
    assert after["active"] == before["active"] and after["overrides"] == before["overrides"]


def test_agents_endpoint_validation(client, monkeypatch):
    assert client.put("/api/agents", json={"profile": "ghost"}).status_code == 400
    assert client.put("/api/agents", json={"overrides": {"janitor": "x"}}).status_code == 400
    monkeypatch.setattr(main, "installed_models", lambda: None)
    res = client.put("/api/agents", json={"profile": "plain"})
    assert res.status_code == 503 and client.get("/api/agents").json()["active"] == "mix"        # reverted


def test_a_change_warms_the_chat_agent_model(client, monkeypatch):
    seen = []
    monkeypatch.setattr(main._warmer, "trigger", lambda: seen.append(1))
    client.put("/api/agents", json={"profile": "plain"})
    assert seen == [1]


def test_health_reports_the_team(client):
    agents = client.get("/api/health").json()["agents"]
    assert agents["active"] == "mix" and agents["models"]["cv_analyst"] == "cv-model:7b" and agents["problems"] == []


def test_choosing_the_default_model_unpins_the_chat_agent(client, monkeypatch):
    monkeypatch.setattr(main, "_agent", Agent(settings=get_settings(), client=FakeChat("x"), model="pinned:1b"))
    for name in ("ollama_model", "num_ctx", "think"):
        monkeypatch.setattr(get_settings(), name, getattr(get_settings(), name))
    client.put("/api/agents", json={"profile": "plain"})
    client.put("/api/model", json={"model": "other:2b"})
    assert main.get_agent().model == "other:2b"                                   # follows team 'plain' -> default model
