"""Tool registry: the list of things the agent is allowed to do.

Each tool is a normal Python function plus a pydantic model describing its arguments.
From that model we generate the JSON schema the LLM sees, and we use the same model to
validate what the LLM sends back.

`call_tool()` is the ONE door every tool call goes through. It never raises:
it always returns {"ok": bool, "data": ..., "error": "human readable message"}.
Every call is also logged (name, args, duration, ok) to logs/tool_calls.jsonl.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.config import get_settings

TOOL_TIMEOUT_SECONDS = 90


@dataclass
class Tool:
    name: str
    description: str                       # the LLM reads this: be clear and concrete
    args_model: type[BaseModel]
    func: Callable[[Any], Any]             # receives the validated args model
    timeout: float = TOOL_TIMEOUT_SECONDS  # slow tools (LLM calls) ask for more

    def schema(self) -> dict:
        """The tool definition in the format the Ollama library expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": simple_schema(self.args_model),
            },
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, args_model: type[BaseModel], timeout: float = TOOL_TIMEOUT_SECONDS):
    """Decorator that registers a function as an agent tool."""
    def register(func: Callable[[Any], Any]):
        TOOLS[name] = Tool(name, " ".join(description.split()), args_model, func, timeout)
        return func
    return register


def simple_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema, flattened so small models can read it.

    Pydantic writes optional fields as {"anyOf": [{"type": "string"}, {"type": "null"}]};
    we turn that into plain {"type": "string"} and drop noise like titles.
    """
    raw = model.model_json_schema()
    props: dict[str, dict] = {}
    for key, spec in raw.get("properties", {}).items():
        if "anyOf" in spec:
            options = [o for o in spec["anyOf"] if o.get("type") != "null"]
            spec = {**{k: v for k, v in spec.items() if k != "anyOf"}, **(options[0] if options else {})}
        props[key] = {k: v for k, v in spec.items() if k in ("type", "description", "enum", "minimum", "maximum", "items")}
    return {"type": "object", "properties": props, "required": raw.get("required", [])}


def tool_schemas(names: list[str] | None = None) -> list[dict]:
    """Schemas to pass to ollama.chat(tools=...). Optionally only some tools."""
    return [t.schema() for n, t in TOOLS.items() if names is None or n in names]


def _clean_arguments(arguments: Any) -> dict:
    """Models sometimes send a JSON string, '' or null for optional fields: tidy that up."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    return {k: v for k, v in arguments.items() if v not in (None, "", [], "null", "None")}


def _explain(error: ValidationError) -> str:
    """Turn a pydantic error into a sentence the model can use to correct itself."""
    problems = []
    for e in error.errors():
        field = ".".join(str(p) for p in e["loc"]) or "arguments"
        problems.append(f"'{field}': {e['msg']}")
    return "Invalid arguments - " + "; ".join(problems)


def _log(name: str, arguments: Any, started: float, ok: bool, error: str | None) -> None:
    """Append one line per call to logs/tool_calls.jsonl (long values are truncated)."""
    try:
        short = {k: (v[:120] + "..." if isinstance(v, str) and len(v) > 120 else v)
                 for k, v in (arguments.items() if isinstance(arguments, dict) else [])}
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": name,
                 "args": short, "duration_ms": round((time.perf_counter() - started) * 1000),
                 "ok": ok, "error": error}
        folder = get_settings().log_path
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "tool_calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass        # logging must never break a tool call


def call_tool(name: str, arguments: Any = None) -> dict:
    """Run a tool safely. ALWAYS returns {"ok", "data", "error"} - never raises."""
    started = time.perf_counter()
    args: Any = arguments
    result: dict
    try:
        if name not in TOOLS:
            raise LookupError(f"Unknown tool '{name}'. Available tools: {', '.join(TOOLS)}")
        args = _clean_arguments(arguments)
        try:
            parsed = TOOLS[name].args_model(**args)
        except ValidationError as e:
            raise ValueError(_explain(e)) from None
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(TOOLS[name].func, parsed)
            try:
                data = future.result(timeout=TOOLS[name].timeout)
            except FutureTimeout:
                raise TimeoutError(f"Tool '{name}' took longer than {TOOLS[name].timeout:.0f}s") from None
        result = {"ok": True, "data": data, "error": None}
    except Exception as e:                                  # ANY failure becomes a normal result
        result = {"ok": False, "data": None, "error": str(e) or type(e).__name__}
    _log(name, args, started, result["ok"], result["error"])
    return result


# Importing the modules registers their tools (keep at the bottom: they import `tool`).
from app.tools import search_jobs, job_details, profile_tools, match_cv  # noqa: E402,F401
