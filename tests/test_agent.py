"""Agent loop tests with a fake Ollama client (no model needed). Job data here is TEST DATA."""
from types import SimpleNamespace

import httpx
import ollama
import pytest

from app.agent.agent import Agent, parse_text_tool_calls
from app.config import Settings, get_settings
from app.providers.aggregator import SearchOutcome
from app.providers.base import Job


# ----------------------------------------------------------------- fake Ollama
def chunk(content="", calls=None, done=False):
    tool_calls = [SimpleNamespace(function=SimpleNamespace(name=n, arguments=a)) for n, a in (calls or [])]
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls or None), done=done,
        eval_count=10, eval_duration=1_000_000_000, prompt_eval_count=50, total_duration=2_000_000_000)


def says(*pieces):
    """A streamed plain answer."""
    return [chunk(p) for p in pieces] + [chunk("", done=True)]


def calls(name, args=None):
    """A streamed tool call."""
    return [chunk("", calls=[(name, args or {})]), chunk("", done=True)]


class FakeClient:
    """Plays back a script: each item is a list of chunks, or an exception to raise."""
    def __init__(self, *script):
        self.script, self.requests = list(script), []

    def chat(self, **kwargs):
        # copy the messages: the agent keeps appending to its own list after this call
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter(item)


@pytest.fixture(autouse=True)
def quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr("app.agent.agent.time.sleep", lambda s: None)
    monkeypatch.setattr("app.agent.agent.load_stored_profile", lambda: None)   # never read the real DB

    def fake_search(query):
        job = Job(id="t:1", source="t", external_id="1", title="Test barista", company="TestCo", city="Gent")
        return SearchOutcome(jobs=[job])
    monkeypatch.setattr("app.tools.search_jobs.search_all", fake_search)


def agent(*script, **kw):
    client = FakeClient(*script)
    return Agent(settings=Settings(), client=client, **kw), client


def kinds(events):
    return [e["type"] for e in events]


# ------------------------------------------------------------------------ tests
def test_plain_answer_streams_tokens():
    a, _ = agent(says("Hello ", "there!"))
    events = list(a.run("hi"))
    assert [e["text"] for e in events if e["type"] == "token"] == ["Hello ", "there!"]
    assert events[-1]["type"] == "done" and events[-1]["text"] == "Hello there!"
    assert a.history == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello there!"}]


def test_tool_call_then_answer():
    a, client = agent(calls("search_jobs", {"keywords": "barista"}), says("Found (id: t:1)."))
    events = list(a.run("find barista jobs"))
    assert kinds(events) == ["tool_call", "tool_result", "token", "done"]
    assert events[1]["ok"] and events[1]["data"]["jobs"][0]["id"] == "t:1"
    second_request = client.requests[1]["messages"]
    assert second_request[-1]["role"] == "tool" and "t:1" in second_request[-1]["content"]
    assert events[-1]["stats"]["tool_runs"] == 1


def test_tool_call_written_as_text_is_understood():
    text = '{"name": "search_jobs", "arguments": {"keywords": "waiter"}}'
    a, _ = agent(says(text), says("Done."))
    events = list(a.run("waiter jobs"))
    assert "tool_call" in kinds(events)
    assert "token" in kinds(events) and all(e.get("text") != text for e in events if e["type"] == "token")


def test_prose_that_starts_with_brace_is_still_shown():
    a, _ = agent(says("{not a tool call}"))
    events = list(a.run("x"))
    assert "".join(e["text"] for e in events if e["type"] == "token") == "{not a tool call}"


def test_bad_arguments_error_goes_back_to_the_model_which_can_fix_it():
    a, client = agent(calls("search_jobs", {"job_type": "freelance"}),
                      calls("search_jobs", {"job_type": "student"}), says("Fixed."))
    events = list(a.run("student jobs"))
    results = [e for e in events if e["type"] == "tool_result"]
    assert [r["ok"] for r in results] == [False, True]
    assert "job_type" in results[0]["error"]
    assert events[-1]["text"] == "Fixed."


def test_tool_failing_twice_is_switched_off():
    a, client = agent(calls("search_jobs", {"limit": 999}), calls("search_jobs", {"limit": 998}),
                      says("Sorry, search is not working."), tool_names=["search_jobs"])
    events = list(a.run("jobs"))
    assert [e["ok"] for e in events if e["type"] == "tool_result"] == [False, False]
    assert "do not use it again" in events[3]["error"]
    assert "tools" not in client.requests[2]            # third request: no tools offered
    assert events[-1]["type"] == "done"


def test_unknown_tool_is_reported_not_crashed():
    a, _ = agent(calls("hack_the_planet"), says("I cannot do that."))
    events = list(a.run("x"))
    result = next(e for e in events if e["type"] == "tool_result")
    assert not result["ok"] and "Unknown tool" in result["error"]
    assert events[-1]["type"] == "done"


def test_repeating_the_same_call_is_stopped():
    a, _ = agent(calls("search_jobs", {"keywords": "a"}), calls("search_jobs", {"keywords": "a"}),
                 says("Here you go."))
    events = list(a.run("x"))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["ok"] and not results[1]["ok"] and "already made" in results[1]["error"]


def test_max_iterations_then_forced_answer():
    script = [calls("search_jobs", {"keywords": f"k{i}"}) for i in range(6)] + [says("Summary.")]
    a, client = agent(*script, max_iterations=6)
    events = list(a.run("x"))
    assert sum(e["type"] == "tool_call" for e in events) == 6
    assert "tools" not in client.requests[-1]           # last request must not offer tools
    assert events[-1]["text"] == "Summary." and events[-1]["stats"]["tool_rounds"] == 6


def test_empty_answer_is_nudged_once_then_apologises():
    a, client = agent(says(""), says(""))
    events = list(a.run("x"))
    assert "Please answer" in client.requests[1]["messages"][-1]["content"]
    assert events[-1]["type"] == "done" and "Sorry" in events[-1]["text"]


def test_ollama_down_gives_friendly_error_after_one_retry():
    a, client = agent(ConnectionError("refused"), ConnectionError("refused"))
    events = list(a.run("hi"))
    assert len(client.requests) == 2                    # first try + one retry
    assert events == [{"type": "error", "message": events[0]["message"]}]
    assert "Cannot reach Ollama" in events[0]["message"]
    assert a.history == []                              # failed question is not remembered


def test_missing_model_tells_how_to_install():
    a, client = agent(ollama.ResponseError("model not found", 404))
    events = list(a.run("hi"))
    assert "ollama pull" in events[0]["message"] and len(client.requests) == 1   # no pointless retry


def test_timeout_message():
    a, _ = agent(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    assert "did not answer" in list(a.run("hi"))[0]["message"]


def test_transient_failure_is_retried_successfully():
    a, client = agent(ConnectionError("blip"), says("Back again."))
    assert list(a.run("hi"))[-1]["text"] == "Back again." and len(client.requests) == 2


def test_model_without_thinking_support_is_retried_without_think():
    a, client = agent(ollama.ResponseError("model does not support thinking", 400), says("ok"))
    assert list(a.run("hi"))[-1]["text"] == "ok"
    assert "think" in client.requests[0] and "think" not in client.requests[1]
    assert a.model in a._no_think


def test_unexpected_exception_never_escapes():
    a, _ = agent(RuntimeError("boom"))
    events = list(a.run("hi"))
    assert events[0]["type"] == "error" and "RuntimeError" in events[0]["message"]


def test_history_is_trimmed_by_turns():
    a, _ = agent(*[says("a")] * 6, max_turns=2)
    for _ in range(6):
        list(a.run("q"))
    assert [m["role"] for m in a.history] == ["user", "assistant", "user", "assistant"]


def test_history_keeps_tool_calls_in_compact_form_for_follow_ups():
    a, client = agent(calls("search_jobs", {"keywords": "barista"}), says("Found (id: t:1)."),
                      says("It is at TestCo."))
    list(a.run("find barista jobs"))
    roles = [m["role"] for m in a.history]
    assert roles == ["user", "assistant", "tool", "assistant"]           # tool call + result are kept
    assert "summary" not in a.history[2]["content"] and "t:1" in a.history[2]["content"]   # but shrunk
    list(a.run("who is the employer of the first one?"))
    assert any(m["role"] == "tool" for m in client.requests[2]["messages"])  # the model sees them again


def test_invented_vacancy_is_retracted_and_retried_with_a_tool():
    fake_link = "https://www.leforem.be/recherche-offres/offre-detail/999"
    a, client = agent(says(f"I found a job: {fake_link}"), calls("search_jobs", {"keywords": "x"}),
                      says("Here: (id: t:1)"))
    events = list(a.run("stage in Antwerpen"))
    assert kinds(events) == ["token", "retract", "tool_call", "tool_result", "token", "done"]
    assert "STOP" in client.requests[1]["messages"][-1]["content"]
    assert events[-1]["text"] == "Here: (id: t:1)"
    assert not any(fake_link in m.get("content", "") for m in a.history)   # the lie is not remembered


def test_still_inventing_after_retry_gives_safe_message():
    lie = "See (id: forem:123456)"
    a, _ = agent(says(lie), says(lie))
    events = list(a.run("x"))
    assert events[-1]["type"] == "done" and "could not verify" in events[-1]["text"]
    assert kinds(events).count("retract") == 2


def test_answer_using_only_tool_ids_is_accepted():
    a, _ = agent(calls("search_jobs", {}), says("Only one: (id: t:1)"))
    assert "retract" not in kinds(list(a.run("x")))


def test_chat_returns_text():
    a, _ = agent(says("Hi!"))
    assert a.chat("hello") == "Hi!"


# ------------------------------------------------------- text tool-call parsing
@pytest.mark.parametrize("text,expected", [
    ('{"name": "search_jobs", "arguments": {"keywords": "x"}}', 1),
    ('<tool_call>{"name": "search_jobs", "arguments": {}}</tool_call>', 1),
    ('```json\n{"name": "search_jobs", "parameters": {"city": "Gent"}}\n```', 1),
    ('[{"name": "search_jobs", "arguments": {}}, {"name": "search_jobs", "arguments": {}}]', 2),
    ('{"name": "unknown_tool", "arguments": {}}', 0),
    ("just words", 0),
    ("{broken", 0),
])
def test_parse_text_tool_calls(text, expected):
    assert len(parse_text_tool_calls(text, {"search_jobs"})) == expected
