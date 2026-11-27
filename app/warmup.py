"""Keep the model warm.

Loading a 7B model into memory takes tens of seconds, and reading our ~900-token system prompt +
tool list takes more. If the first question of the demo pays for both, the audience stares at a spinner.
So when the server starts we (1) load the chat model, (2) let it read the REAL system prompt and tool
list once - Ollama keeps that prefix cached, so the first real question skips it - and (3) load the
embedding model. A light ping every few minutes keeps everything loaded (Ollama would unload it
after `keep_alive` otherwise).
"""
from __future__ import annotations

import threading
import time
from typing import Any

import ollama

from app import llm
from app.agent.prompts import build_system_prompt
from app.config import get_settings
from app.tools import tool_schemas

KEEP_ALIVE = "30m"
PING_EVERY_MINUTES = 15


def prime_chat(model: str | None = None) -> float:
    """Load the chat model and make it read the real system prompt + tools. Returns seconds taken."""
    from app.agent.agent import load_stored_profile          # late import: the agent imports a lot
    s = get_settings()
    model = model or s.ollama_model
    messages = [{"role": "system", "content": build_system_prompt(load_stored_profile())},
                {"role": "user", "content": "Hello"}]
    kwargs: dict[str, Any] = dict(model=model, messages=messages, tools=tool_schemas(), keep_alive=KEEP_ALIVE,
                                  options={"num_ctx": s.num_ctx, "num_predict": 1}, think=s.think)
    started = time.perf_counter()
    try:
        llm.get_client().chat(**kwargs)
    except ollama.ResponseError as e:
        if "thinking" not in str(getattr(e, "error", e)).lower():
            raise
        kwargs.pop("think")                                   # model without a thinking mode
        llm.get_client().chat(**kwargs)
    return time.perf_counter() - started


def prime_embeddings() -> float:
    started = time.perf_counter()
    llm.embed(["warm-up"])
    return time.perf_counter() - started


class BackgroundWarmer:
    """Warms the models in a daemon thread and reports its progress (shown in /api/health)."""

    def __init__(self, ping_every_minutes: float = PING_EVERY_MINUTES):
        self.state: dict[str, Any] = {"status": "idle", "message": "not started", "chat_s": None, "embed_s": None,
                                      "model": None}
        self._every = ping_every_minutes * 60
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def warm_now(self) -> dict:
        """Warm both models (blocking). Never raises: problems are written into the state."""
        if not self._lock.acquire(blocking=False):             # another warm-up is running
            return self.state
        try:
            model = get_settings().ollama_model
            self.state.update(status="warming", message=f"loading {model}...", model=model)
            try:
                self.state["chat_s"] = round(prime_chat(model), 1)
            except Exception as e:
                self.state.update(status="failed", message=f"could not load {model}: {_short(e)}")
                return self.state
            try:
                self.state["embed_s"] = round(prime_embeddings(), 1)
                note = ""
            except Exception as e:
                self.state["embed_s"] = None
                note = f" (embedding model not loaded: {_short(e)})"
            self.state.update(status="ready", message=f"{model} is loaded and ready{note}")
        finally:
            self._lock.release()
        return self.state

    def trigger(self) -> None:
        """Warm in the background right now (e.g. after the user switched model)."""
        threading.Thread(target=self.warm_now, name="warm-once", daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.warm_now()
            print(f"[warm-up] {self.state['message']}")
            self._stop.wait(self._every)

    def start(self) -> None:
        threading.Thread(target=self._loop, name="warm-up", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def _short(e: Exception) -> str:
    return (str(getattr(e, "error", e)) or type(e).__name__)[:120]
