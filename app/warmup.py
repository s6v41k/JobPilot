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
from app.agent import roles
from app.agent.prompts import build_system_prompt
from app.config import get_settings
from app.tools import tool_schemas

KEEP_ALIVE = "30m"
PING_EVERY_MINUTES = 15


def prime_chat(model: str | None = None) -> float:
    """Load the chat model and make it read the real system prompt + tools. Returns seconds taken."""
    from app.agent.agent import load_stored_profile          # late import: the agent imports a lot
    s = get_settings()
    role = roles.role_config("orchestrator", s)             # same model, tools and options as the real agent
    model = model or role.model
    messages = [{"role": "system", "content": build_system_prompt(load_stored_profile(), tools=role.tools)},
                {"role": "user", "content": "Hello"}]
    kwargs: dict[str, Any] = dict(model=model, messages=messages, tools=tool_schemas(role.tools), keep_alive=KEEP_ALIVE,
                                  options={"num_ctx": role.num_ctx, "num_predict": 1}, think=role.think)
    started = time.perf_counter()
    try:
        llm.get_client().chat(**kwargs)
    except ollama.ResponseError as e:
        if "thinking" not in str(getattr(e, "error", e)).lower():
            raise
        kwargs.pop("think")                                   # model without a thinking mode
        llm.get_client().chat(**kwargs)
    return time.perf_counter() - started


def load_model(model: str) -> float:
    """Just load a model into memory (no prompt). Used for the roles that are not the chat agent:
    their first real job (reading a CV, writing a letter) should not wait for the model to load."""
    started = time.perf_counter()
    llm.get_client().chat(model=model, messages=[{"role": "user", "content": "Hi"}], keep_alive=KEEP_ALIVE,
                          options={"num_predict": 1})
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
            model = roles.model_for("orchestrator")
            self.state.update(status="warming", message=f"loading {model}...", model=model)
            try:
                self.state["chat_s"] = round(prime_chat(model), 1)
            except Exception as e:
                self.state.update(status="failed", message=f"could not load {model}: {_short(e)}")
                return self.state
            note = ""
            for other in [m for m in roles.models_in_use() if m != model]:       # the rest of the team
                try:
                    self.state.update(message=f"loading {other}...")
                    self.state.setdefault("others_s", {})[other] = round(load_model(other), 1)
                except Exception as e:
                    note += f" (could not load {other}: {_short(e)})"
            try:
                self.state["embed_s"] = round(prime_embeddings(), 1)
            except Exception as e:
                self.state["embed_s"] = None
                note += f" (embedding model not loaded: {_short(e)})"
            team = roles.models_in_use()
            self.state.update(status="ready", message=f"{', '.join(team)} loaded and ready{note}" if len(team) > 1
                              else f"{model} is loaded and ready{note}")
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
