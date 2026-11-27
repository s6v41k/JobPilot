"""Warm-up and pre-flight check, with a fake Ollama (no model is ever loaded in these tests)."""
import json
from types import SimpleNamespace

import httpx
import ollama
import pytest

from app import llm, preflight, warmup
from app.config import get_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.agent.agent.load_stored_profile", lambda: None)


class FakeOllama:
    def __init__(self, chat_error=None, embed_error=None):
        self.chat_error, self.embed_error, self.chats, self.embeds = chat_error, embed_error, [], []

    def chat(self, **kw):
        self.chats.append(kw)
        if self.chat_error:
            err, self.chat_error = self.chat_error, None if isinstance(self.chat_error, ollama.ResponseError) else self.chat_error
            raise err
        return SimpleNamespace(message=SimpleNamespace(content="OK"))

    def embed(self, **kw):
        self.embeds.append(kw)
        if self.embed_error:
            raise self.embed_error
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]] * len(kw["input"]))


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        client = FakeOllama(**kw)
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return client
    return install


# ------------------------------------------------------------------- warm-up
def test_priming_sends_the_real_system_prompt_and_tools(fake):
    client = fake()
    seconds = warmup.prime_chat("qwen2.5:7b")
    call = client.chats[0]
    assert seconds >= 0 and call["keep_alive"] == "30m" and call["options"]["num_predict"] == 1
    assert "You are JobPilot" in call["messages"][0]["content"]                    # the REAL system prompt
    assert {t["function"]["name"] for t in call["tools"]} >= {"search_jobs", "match_cv"}   # and the REAL tools


def test_priming_retries_without_think_for_models_without_thinking(fake):
    client = fake(chat_error=ollama.ResponseError("model does not support thinking", 400))
    warmup.prime_chat("llama3.2:3b")
    assert len(client.chats) == 2 and "think" in client.chats[0] and "think" not in client.chats[1]


def test_warm_now_success_and_state(fake):
    fake()
    warmer = warmup.BackgroundWarmer()
    state = warmer.warm_now()
    assert state["status"] == "ready" and state["chat_s"] is not None and state["embed_s"] is not None
    assert "is loaded and ready" in state["message"]


def test_warm_now_never_raises_when_the_model_is_missing(fake):
    fake(chat_error=ollama.ResponseError("model 'x' not found", 404))
    state = warmup.BackgroundWarmer().warm_now()
    assert state["status"] == "failed" and "could not load" in state["message"]


def test_a_missing_embedding_model_is_only_a_note(fake):
    fake(embed_error=ConnectionError("down"))
    state = warmup.BackgroundWarmer().warm_now()
    assert state["status"] == "ready" and "embedding model not loaded" in state["message"]


def test_two_warmups_do_not_run_at_once(fake, monkeypatch):
    fake()
    warmer = warmup.BackgroundWarmer()
    calls = []
    monkeypatch.setattr(warmup, "prime_chat", lambda m=None: calls.append(1) or 0.1)
    assert warmer._lock.acquire(blocking=False)                  # pretend one is running
    warmer.warm_now()
    warmer._lock.release()
    assert calls == []


def test_trigger_warms_in_the_background(monkeypatch):
    warmer = warmup.BackgroundWarmer()
    done = []
    monkeypatch.setattr(warmer, "warm_now", lambda: done.append(1))
    warmer.trigger()
    for _ in range(50):
        if done:
            break
        import time
        time.sleep(0.02)
    assert done == [1]


# ----------------------------------------------------------------- pre-flight
class OllamaFake:
    """Replaces the HTTP calls of the pre-flight check."""
    def __init__(self, monkeypatch, models=("qwen2.5:7b", "bge-m3:latest"), capabilities=("completion", "tools"), up=True):
        self.models, self.caps, self.up = models, list(capabilities), up
        monkeypatch.setattr(preflight.httpx, "get", self.get)
        monkeypatch.setattr(preflight.httpx, "post", self.post)

    def get(self, url, timeout=None):
        if not self.up:
            raise httpx.ConnectError("refused")
        body = {"version": "9.9"} if url.endswith("/api/version") else {"models": [{"name": m} for m in self.models]}
        return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)

    def post(self, url, json=None, timeout=None):
        return SimpleNamespace(json=lambda: {"capabilities": self.caps})


def by_name(checks):
    return {c.name: c for c in checks}


def test_ollama_down_fails_one_check_and_skips_the_rest(monkeypatch):
    OllamaFake(monkeypatch, up=False)
    checks = by_name(preflight.run_checks())
    assert checks["Ollama"].status == "fail" and "Start the Ollama app" in checks["Ollama"].fix
    assert all(checks[n].status == "skip" for n in ("Chat model", "Tool calling", "Embedding model"))
    assert checks["Database"].status == "ok" and checks["Web files"].status == "ok"      # non-LLM parts still checked


def test_missing_chat_model_is_a_failure_with_the_fix(monkeypatch):
    OllamaFake(monkeypatch, models=("bge-m3:latest",))
    check = by_name(preflight.run_checks())["Chat model"]
    assert check.status == "fail" and check.fix == f"ollama pull {get_settings().ollama_model}"


def test_missing_embedding_model_is_only_a_warning(monkeypatch):
    OllamaFake(monkeypatch, models=("qwen2.5:7b",))
    assert by_name(preflight.run_checks())["Embedding model"].status == "warn"


def test_a_model_without_tools_fails(monkeypatch):
    OllamaFake(monkeypatch, capabilities=("completion",))
    check = by_name(preflight.run_checks())["Tool calling"]
    assert check.status == "fail" and "does not support tool calling" in check.detail


def test_everything_fine_is_ready(monkeypatch, fake):
    OllamaFake(monkeypatch)
    fake()
    monkeypatch.setattr(preflight, "check_vacancies", lambda: ("ok", "fine"))
    monkeypatch.setattr(preflight, "check_search_tool", lambda: ("ok", "fine"))
    monkeypatch.setattr(preflight, "check_profile", lambda: ("ok", "fine"))
    monkeypatch.setattr(preflight, "check_port", lambda: ("ok", "fine"))
    monkeypatch.setattr(preflight.get_settings(), "adzuna_app_id", "x")
    monkeypatch.setattr(preflight.get_settings(), "adzuna_app_key", "y")
    summary = preflight.summarize(preflight.run_checks(chat=True))
    assert summary["ready"] and summary["counts"]["fail"] == 0


def test_chat_call_measures_the_cold_start(monkeypatch, fake):
    OllamaFake(monkeypatch)
    fake()
    checks = by_name(preflight.run_checks(chat=True))
    assert checks["Chat call"].status == "ok" and "answered" in checks["Chat call"].detail
    assert checks["Embedding call"].status == "ok"


def test_chat_call_is_skipped_by_default(monkeypatch):
    OllamaFake(monkeypatch)
    assert by_name(preflight.run_checks())["Chat call"].status == "skip"


def test_a_crashing_check_becomes_a_red_line_not_a_crash(monkeypatch):
    OllamaFake(monkeypatch)
    monkeypatch.setattr(preflight, "check_database", lambda: 1 / 0)
    check = by_name(preflight.run_checks())["Database"]
    assert check.status == "fail" and "crashed" in check.detail


def test_vacancy_check_levels(tmp_path):
    from app import db
    from app.providers import forem_mirror
    assert preflight.check_vacancies()[0] == "warn"                           # no local list, snapshot exists
    with db.connect() as conn:
        db.forem_replace_all(conn, [{"numerooffreforem": "1", "titreoffre": "Serveur", "typecontrat": "Etudiant",
                                     "datefindiffusion": "2999-01-01", "lieuxtravailregionnuts": ["BE1"]}])
    status, detail = preflight.check_vacancies()[:2]
    assert status == "ok" and "1 Le Forem offers" in detail
    assert preflight.check_search_tool()[0] == "ok"


def test_summary_verdicts():
    ok, warn, fail = preflight.Check("a", "ok", ""), preflight.Check("b", "warn", ""), preflight.Check("c", "fail", "")
    assert preflight.summarize([ok])["verdict"] == "READY"
    assert preflight.summarize([ok, warn])["verdict"] == "READY (with warnings)"
    assert preflight.summarize([ok, warn, fail]) == {"verdict": "NOT READY", "ready": False,
                                                     "counts": {"ok": 1, "warn": 1, "fail": 1, "skip": 0}}


def test_command_line_exit_codes_and_json(monkeypatch, capsys):
    OllamaFake(monkeypatch, up=False)
    assert preflight.main(["--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "NOT READY" and any(c["name"] == "Ollama" and c["status"] == "fail" for c in data["checks"])
    OllamaFake(monkeypatch)
    assert preflight.main([]) in (0, 1)
    assert "pre-flight check" in capsys.readouterr().out
