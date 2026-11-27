"""Small helper for tools that need ONE structured answer from the LLM
(extract a profile, score a match, write a letter) - no tool loop, no streaming.

`chat_json()` asks Ollama for JSON that follows a schema (Ollama's `format` option),
checks it with pydantic, retries once if the model produced something invalid, and turns
every failure into a readable message.
"""
from __future__ import annotations

import json
import re
from typing import Any, TypeVar

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)

_client: Any = None


def get_client():
    """One shared Ollama client (tests replace it with a fake)."""
    global _client
    if _client is None:
        s = get_settings()
        _client = ollama.Client(host=s.ollama_host, timeout=s.request_timeout)
    return _client


class LLMError(Exception):
    """The LLM could not give a usable answer. The message is meant for the user."""


def _friendly(e: Exception, model: str) -> LLMError:
    if isinstance(e, ollama.ResponseError):
        if e.status_code == 404:
            return LLMError(f"The model '{model}' is not installed. Install it with: ollama pull {model}")
        return LLMError(f"Ollama returned an error: {str(getattr(e, 'error', e))[:200]}")
    if isinstance(e, (ConnectionError, httpx.ConnectError)):
        return LLMError("Cannot reach Ollama. Start the Ollama app and try again.")
    if isinstance(e, httpx.TimeoutException):
        return LLMError("The model took too long to answer. Try a smaller model.")
    return LLMError(f"The model call failed ({type(e).__name__}).")


def _extract_json(text: str) -> Any:
    """Parse JSON even if the model wrapped it in ```json fences or added a sentence."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def chat_json(schema: type[T], system: str, user: str, model: str | None = None,
              max_tokens: int = 700, temperature: float = 0.0) -> T:
    """Ask the LLM for an object of type `schema`. Raises LLMError on failure."""
    settings = get_settings()
    model = model or settings.ollama_model
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_problem = ""
    for attempt in (1, 2):
        kwargs: dict[str, Any] = dict(
            model=model, messages=messages, format=schema.model_json_schema(), keep_alive="30m",
            options={"num_ctx": settings.num_ctx, "temperature": temperature, "num_predict": max_tokens},
            think=False,
        )
        try:
            try:
                reply = get_client().chat(**kwargs)
            except ollama.ResponseError as e:               # model without a thinking mode
                if "thinking" not in str(getattr(e, "error", e)).lower():
                    raise
                kwargs.pop("think")
                reply = get_client().chat(**kwargs)
        except Exception as e:
            raise _friendly(e, model) from None
        try:
            return schema.model_validate(_extract_json(reply.message.content or ""))
        except (ValueError, ValidationError) as e:
            last_problem = str(e)[:150]
            messages = messages + [
                {"role": "assistant", "content": reply.message.content or ""},
                {"role": "user", "content": "That was not valid JSON for the requested schema. "
                                            "Reply again with ONLY the JSON object."}]
    raise LLMError(f"The model did not return a valid structured answer ({last_problem}).")


def embed(texts: list[str]) -> list[list[float]]:
    """Turn texts into vectors with the embedding model (bge-m3). Raises LLMError."""
    settings = get_settings()
    try:
        reply = get_client().embed(model=settings.embed_model, input=texts, keep_alive="30m")
        vectors = [list(v) for v in reply.embeddings]
    except Exception as e:
        raise _friendly(e, settings.embed_model) from None
    if len(vectors) != len(texts):
        raise LLMError("The embedding model returned an unexpected answer.")
    return vectors
