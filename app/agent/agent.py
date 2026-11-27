"""The agent loop.

The idea is the classic tool-calling loop:

    user message -> ask the LLM
        -> LLM answers with text            => done
        -> LLM asks for tool(s)             => we run them, add the results, ask again
    (repeat, at most MAX_ITERATIONS rounds of tool calls)

`Agent.run()` is a generator that yields small event dicts, so the CLI and the web UI
(Server-Sent Events) can show tokens and tool calls live:

    {"type": "token",       "text": "..."}                       a piece of the answer
    {"type": "tool_call",   "name": ..., "arguments": {...}}     the LLM decided to call a tool
    {"type": "tool_result", "name": ..., "ok": bool, "error": ..., "duration_ms": ..., "data": ...}
    {"type": "error",       "message": "..."}                    friendly, never a stack trace
    {"type": "done",        "text": "...", "stats": {...}}

Robustness rules (the demo must not crash):
  * Ollama down / model missing / timeout  -> friendly error event, one retry first.
  * Unknown tool or bad arguments          -> the error goes back to the LLM so it can fix
                                              the call once; after 2 failures that tool is
                                              switched off and the LLM must apologise.
  * Same call repeated / too many rounds   -> we stop calling tools and force an answer.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import Any, Iterator

import httpx
import ollama

from app import db
from app.agent.prompts import build_system_prompt
from app.config import Settings, get_settings
from app.tools import TOOLS, call_tool, tool_schemas

MAX_ITERATIONS = 6           # max rounds of tool calls per user message
MAX_TOOL_FAILURES = 2        # after this many failures a tool is switched off for the turn
MAX_TOOL_RESULT_CHARS = 6000


class AgentError(Exception):
    """Something went wrong talking to Ollama. The message is meant for the user."""


# --------------------------------------------------------------------------- helpers
def parse_text_tool_calls(text: str, known: set[str]) -> list[dict]:
    """Some small models write the tool call as TEXT instead of using the tool-call field,
    e.g.  {"name": "search_jobs", "arguments": {...}}  or  <tool_call>{...}</tool_call>.
    Recognise that so the agent still works."""
    candidates = re.findall(r"<tool_call>(.*?)</tool_call>", text, flags=re.S) or [text]
    calls: list[dict] = []
    for raw in candidates:
        raw = raw.strip().strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:]
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        for item in obj if isinstance(obj, list) else [obj]:
            if isinstance(item, dict) and item.get("name") in known:
                args = item.get("arguments", item.get("parameters", {}))
                calls.append({"name": item["name"], "arguments": args if isinstance(args, (dict, str)) else {}})
    return calls


def _looks_like_text_tool_call(text: str) -> bool:
    t = text.lstrip()
    return t.startswith("{") or t.startswith("[") or t.startswith("<tool_call>") or t.startswith("`")


_URL = re.compile(r"https?://[^\s)\]>\"']+")
_JOB_ID = re.compile(r"\b(?:forem|adzuna):\w+")


_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+", re.MULTILINE)
_BRACKETED_ID = re.compile(r"\(\s*(?:id\s*[:#]?\s*)?([A-Za-z_]*:?[A-Za-z_]*\d{2,}[\w:.-]*)\s*\)", re.IGNORECASE)
_YEAR = re.compile(r"(?:19|20)\d{2}$")
_SCORE = re.compile(r"\b(\d{1,3})\s*/\s*100\b")


def ungrounded_claims(answer: str, evidence: str) -> list[str]:
    """Links / vacancy ids in the answer that NO tool result contained = invented by the LLM.

    Three checks: (1) links, (2) ids like forem:123, (3) in a LIST of items, a bracketed
    id-like number such as "(123)" that no tool returned (small models invent these)."""
    claims = {m.rstrip(".,;:") for m in _URL.findall(answer)} | set(_JOB_ID.findall(answer))
    if len(_LIST_ITEM.findall(answer)) >= 2:
        claims |= {c for c in _BRACKETED_ID.findall(answer) if not _YEAR.match(c)}
    # A match score ("85/100") must come from a match_cv result, not from the model's imagination.
    for number in _SCORE.findall(answer):
        if not re.search(rf'"score":\s*{number}\b', evidence):
            claims.add(f"{number}/100")
    # whole-token match, so the invented "(123)" is not "found" inside a real id like forem:2123456
    return sorted(c for c in claims if not re.search(rf"(?<![\w:]){re.escape(c)}(?![\w])", evidence))


def history_form(message: dict) -> dict:
    """Shrink a tool result before it goes into the long-term history: keep only what a
    follow-up question needs (id, title, company, city, link)."""
    if message.get("role") != "tool":
        return message
    try:
        result = json.loads(message["content"])
        jobs = result["data"]["jobs"]
        slim = {"ok": True, "data": {
            "count": len(jobs), "data_source": result["data"].get("data_source"),
            "jobs": [{k: j.get(k) for k in ("id", "title", "company", "city", "url")} for j in jobs]}}
        return {**message, "content": json.dumps(slim, ensure_ascii=False)}
    except Exception:
        return {**message, "content": message["content"][:1200]}


def _compact(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= MAX_TOOL_RESULT_CHARS else text[:MAX_TOOL_RESULT_CHARS] + '..."(truncated)'


def load_stored_profile() -> dict | None:
    """The profile saved from the user's CV (None if there is none or the DB is unreadable)."""
    try:
        with db.connect() as conn:
            return db.load_profile(conn)
    except Exception:
        return None


# ----------------------------------------------------------------------------- agent
class Agent:
    def __init__(self, settings: Settings | None = None, client: Any = None,
                 model: str | None = None, tool_names: list[str] | None = None,
                 max_iterations: int = MAX_ITERATIONS, max_turns: int = 6,
                 profile: dict | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self.model = model or self.settings.ollama_model
        self.tool_names = tool_names
        self.max_iterations = max_iterations
        self.max_turns = max_turns
        self.profile = profile
        self.history: list[dict] = []          # last few turns, incl. (shrunk) tool calls/results
        self._no_think: set[str] = set()       # models that reject the `think` option

    # ------------------------------------------------------------------ plumbing
    @property
    def client(self):
        if self._client is None:
            self._client = ollama.Client(host=self.settings.ollama_host,
                                         timeout=self.settings.request_timeout)
        return self._client

    def reset(self) -> None:
        self.history.clear()

    def _stream(self, messages: list[dict], tools: list[dict] | None) -> Iterator[Any]:
        """One streamed call to Ollama, with ONE retry and human-readable errors."""
        for attempt in (1, 2):
            sent_anything = False
            kwargs: dict[str, Any] = dict(
                model=self.model, messages=messages, stream=True, keep_alive="30m",
                options={"num_ctx": self.settings.num_ctx},
            )
            if tools:
                kwargs["tools"] = tools
            if self.model not in self._no_think:
                kwargs["think"] = self.settings.think
            try:
                for chunk in self.client.chat(**kwargs):
                    sent_anything = True
                    yield chunk
                return
            except ollama.ResponseError as e:
                text = str(getattr(e, "error", e))
                if "thinking" in text.lower() and "think" in kwargs:
                    self._no_think.add(self.model)         # model has no thinking mode
                    continue
                if e.status_code == 404:
                    raise AgentError(f"The model '{self.model}' is not installed. "
                                     f"Install it with:  ollama pull {self.model}") from None
                if e.status_code >= 500 and attempt == 1 and not sent_anything:
                    time.sleep(1)
                    continue
                raise AgentError(f"Ollama returned an error: {text[:200]}") from None
            except (ConnectionError, httpx.ConnectError):
                if attempt == 1 and not sent_anything:
                    time.sleep(1)
                    continue
                raise AgentError("Cannot reach Ollama. Start the Ollama app and try again "
                                 f"({self.settings.ollama_host}).") from None
            except httpx.TimeoutException:
                if attempt == 1 and not sent_anything:
                    continue
                raise AgentError(f"The model did not answer within {self.settings.request_timeout:.0f}s. "
                                 "Try a smaller model or a shorter question.") from None
        raise AgentError("The model could not be reached.")

    def _turn(self, messages: list[dict], tools: list[dict] | None):
        """Ask the LLM once. Streams tokens; returns (text, tool_calls, stats)."""
        text, calls, stats = "", [], {}
        held = ""                      # text we hold back because it may be a text-form tool call
        deciding = True                # still deciding whether the text is prose or a tool call
        hold = False
        known = set(TOOLS)
        for chunk in self._stream(messages, tools):
            msg = chunk.message
            for tc in msg.tool_calls or []:
                calls.append({"name": tc.function.name, "arguments": dict(tc.function.arguments or {})})
            piece = msg.content or ""
            if piece:
                text += piece
                if deciding and text.strip():
                    hold = bool(tools) and _looks_like_text_tool_call(text)
                    deciding = False
                    piece = text                     # flush everything seen so far
                if hold:
                    held = text
                elif not deciding:
                    yield {"type": "token", "text": piece}
            if getattr(chunk, "done", False):
                stats = {"eval_count": chunk.eval_count or 0, "eval_duration": chunk.eval_duration or 0,
                         "prompt_eval_count": chunk.prompt_eval_count or 0,
                         "total_duration": chunk.total_duration or 0}
        if not calls and hold:
            calls = parse_text_tool_calls(held, known)
            if calls:
                text = ""                            # it was a tool call, not an answer
            else:
                yield {"type": "token", "text": held}  # it was just prose that starts with "{"
        return text, calls, stats

    # --------------------------------------------------------------------- main loop
    def _trim_history(self) -> None:
        """Keep only the last `max_turns` user turns (a turn = user msg + tool calls + answer)."""
        starts = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(starts) > self.max_turns:
            del self.history[: starts[-self.max_turns]]

    def run(self, user_text: str) -> Iterator[dict]:
        started = time.perf_counter()
        profile = self.profile if self.profile is not None else load_stored_profile()
        system = {"role": "system", "content": build_system_prompt(profile)}
        messages = [system] + self.history + [{"role": "user", "content": user_text}]
        turn_start = len(messages) - 1            # where this turn begins (for saving history)
        synthetic: list[dict] = []                # our own nudges: never saved to the history
        schemas = tool_schemas(self.tool_names)
        blocked: set[str] = set()
        failures: Counter = Counter()
        seen_calls: set[str] = set()
        totals = Counter()
        rounds = tool_runs = 0
        final_text = ""
        nudged = retried = False

        try:
            while True:
                active = [s for s in schemas if s["function"]["name"] not in blocked]
                use_tools = bool(active) and rounds < self.max_iterations
                text, calls, stats = yield from self._turn(messages, active if use_tools else None)
                totals.update({k: v for k, v in stats.items() if isinstance(v, int)})

                if not calls:                                   # the LLM answered in prose
                    final_text = text.strip()
                    if not final_text and not nudged:           # empty answer: ask once more
                        nudged = True
                        nudge = {"role": "user", "content": "Please answer the question now, "
                                 "using the tool results above."}
                        messages.append(nudge)
                        synthetic.append(nudge)
                        continue
                    # Grounding check: links/ids that no tool returned were invented.
                    evidence = " ".join(m["content"] for m in messages if m["role"] == "tool")
                    invented = ungrounded_claims(final_text, evidence)
                    if invented:
                        yield {"type": "retract", "reason": "The answer contained vacancies that no tool returned."}
                        if retried or not use_tools:
                            final_text = ("Sorry, I could not verify any vacancies for that request, so I will not "
                                          "guess. Please try again or rephrase your question.")
                            yield {"type": "token", "text": final_text}
                            break
                        retried = True
                        fix = {"role": "user", "content": "STOP: your answer mentioned vacancies or links that no "
                               "tool returned. Never invent vacancies. Call the search_jobs tool now and answer "
                               "ONLY from its results."}
                        messages.append({"role": "assistant", "content": final_text})
                        messages.append(fix)
                        synthetic += [messages[-2], fix]
                        continue
                    break

                rounds += 1
                messages.append({"role": "assistant", "content": text,
                                 "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]
                                                              if isinstance(c["arguments"], dict) else {}}}
                                                for c in calls]})
                for call in calls:
                    name, args = call["name"], call["arguments"]
                    yield {"type": "tool_call", "name": name, "arguments": args}
                    key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                    t0 = time.perf_counter()
                    if key in seen_calls:                       # loop guard: same call again
                        result = {"ok": False, "data": None, "error":
                                  "You already made exactly this call. Use the earlier result and answer the user."}
                        blocked.add(name)
                    else:
                        seen_calls.add(key)
                        result = call_tool(name, args)
                        tool_runs += 1
                    if not result["ok"]:
                        failures[name] += 1
                        if failures[name] >= MAX_TOOL_FAILURES:
                            blocked.add(name)
                            result["error"] += f" (The tool '{name}' failed twice; do not use it again. " \
                                               "Apologise briefly and explain what went wrong.)"
                    yield {"type": "tool_result", "name": name, "ok": result["ok"], "error": result["error"],
                           "duration_ms": round((time.perf_counter() - t0) * 1000), "data": result["data"]}
                    messages.append({"role": "tool", "tool_name": name, "content": _compact(result)})
        except AgentError as e:
            yield {"type": "error", "message": str(e)}          # history untouched: failed turn is forgotten
            return
        except Exception as e:                                  # last line of defence: never crash
            yield {"type": "error", "message": f"Something went wrong ({type(e).__name__}). Please try again."}
            return

        if not final_text:
            final_text = "Sorry, I could not put together an answer. Please try rephrasing your question."
            yield {"type": "token", "text": final_text}
        # Remember this turn (without our nudges) so follow-ups like "tell me more about #2" work,
        # and so the model keeps seeing that it answers vacancy questions with tools.
        ours = {id(m) for m in synthetic}
        turn = [history_form(m) for m in messages[turn_start:] if id(m) not in ours]
        if not turn or turn[-1].get("content") != final_text or turn[-1]["role"] != "assistant":
            turn.append({"role": "assistant", "content": final_text})
        self.history += turn
        self._trim_history()
        yield {"type": "done", "text": final_text, "stats": {
            **dict(totals), "tool_rounds": rounds, "tool_runs": tool_runs,
            "elapsed_s": round(time.perf_counter() - started, 2), "model": self.model}}

    def chat(self, user_text: str) -> str:
        """Convenience: run to completion and return the final text (or the error message)."""
        text = ""
        for event in self.run(user_text):
            if event["type"] in ("done", "error"):
                text = event.get("text") or event.get("message", "")
        return text
