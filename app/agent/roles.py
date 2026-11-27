"""Agent teams: which model does which job (see agents.yaml).

A language model is used in three roles only:
    orchestrator  the chat agent (tool calling)       cv_analyst  CV text -> structured profile
    writer        the motivation letters
`role_config(role)` answers "which model, how much context, which tools?" for one role.

The answer is built from three layers (first one that has a value wins):
    1. a model chosen for that role at runtime in the UI   (settings.role_overrides)
    2. the active team in agents.yaml
    3. the default model                                    (settings.ollama_model)
A broken agents.yaml never crashes the app: we fall back to the built-in "default" team and
report the problem (shown in /api/health and the pre-flight check).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.config import ROOT_DIR, Settings, get_settings

DEFAULT = "default"          # in agents.yaml: "the default model"


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    description: str


ROLES: dict[str, Role] = {
    "orchestrator": Role("orchestrator", "Chat agent",
                         "Talks to the student and decides which tool to call (the tool-calling loop)."),
    "cv_analyst": Role("cv_analyst", "CV reader", "Turns the CV text into a structured profile (JSON output)."),
    "writer": Role("writer", "Letter writer", "Writes the short motivation letters."),
}

BUILTIN_TEAMS: dict[str, dict] = {
    DEFAULT: {"description": "One model for everything.",
              "roles": {r: {"model": DEFAULT} for r in ROLES}},
}


@dataclass(frozen=True)
class RoleConfig:
    role: str
    model: str                       # resolved: never "default"
    num_ctx: int
    think: bool
    tools: list[str] | None          # None = all tools
    source: str                      # "override" | "team" | "default model": where the model came from


# ------------------------------------------------------------------ loading
_cache: dict[str, Any] = {"key": None, "teams": None, "active": None, "problems": []}


def _path(settings: Settings) -> Path:
    p = Path(settings.agents_file)
    return p if p.is_absolute() else ROOT_DIR / p


def _validate(raw: Any) -> tuple[dict[str, dict], str, list[str]]:
    """Check the parsed YAML. Returns (teams, active team name, problems). Never raises."""
    from app.tools import TOOLS
    problems: list[str] = []
    teams: dict[str, dict] = {}
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
        return dict(BUILTIN_TEAMS), DEFAULT, ["agents.yaml has no 'profiles' section: using the built-in default team"]
    for name, body in raw["profiles"].items():
        if not isinstance(body, dict):
            problems.append(f"team '{name}' is not a mapping: ignored")
            continue
        roles: dict[str, dict] = {}
        for role, spec in (body.get("roles") or {}).items():
            if role not in ROLES:
                problems.append(f"team '{name}': unknown role '{role}' (known: {', '.join(ROLES)}): ignored")
                continue
            spec = spec if isinstance(spec, dict) else {}
            clean: dict[str, Any] = {"model": str(spec.get("model") or DEFAULT)}
            if isinstance(spec.get("num_ctx"), int) and 1024 <= spec["num_ctx"] <= 32768:
                clean["num_ctx"] = spec["num_ctx"]
            elif "num_ctx" in spec:
                problems.append(f"team '{name}', role '{role}': num_ctx must be a number between 1024 and 32768: ignored")
            if isinstance(spec.get("think"), bool):
                clean["think"] = spec["think"]
            if "tools" in spec:
                wanted = spec["tools"] if isinstance(spec["tools"], list) else []
                unknown = [t for t in wanted if t not in TOOLS]
                if unknown:
                    problems.append(f"team '{name}', role '{role}': unknown tools {unknown} ignored")
                tools = [t for t in wanted if t in TOOLS]
                if role == "orchestrator" and tools:
                    clean["tools"] = tools
                elif role != "orchestrator":
                    problems.append(f"team '{name}': only the orchestrator uses tools; 'tools' on '{role}' ignored")
            roles[role] = clean
        for role in ROLES:                                  # a missing role simply uses the default model
            roles.setdefault(role, {"model": DEFAULT})
        teams[name] = {"description": str(body.get("description") or ""), "roles": roles}
    if not teams:
        return dict(BUILTIN_TEAMS), DEFAULT, problems + ["agents.yaml defines no usable team: using the built-in default team"]
    active = str(raw.get("active") or DEFAULT)
    if active not in teams:
        problems.append(f"active team '{active}' does not exist: using '{next(iter(teams))}'")
        active = next(iter(teams))
    return teams, active, problems


def load(settings: Settings | None = None) -> tuple[dict[str, dict], str, list[str]]:
    """(teams, active team name from the file, problems). Re-read when the file changes."""
    settings = settings or get_settings()
    path = _path(settings)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        key = (str(path), None)
    if _cache["key"] != key:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) if key[1] is not None else None
            if key[1] is None:
                teams, active, problems = dict(BUILTIN_TEAMS), DEFAULT, [f"{path.name} not found: using the built-in default team"]
            else:
                teams, active, problems = _validate(raw)
        except Exception as e:                              # YAML syntax error, unreadable file, ...
            teams, active, problems = dict(BUILTIN_TEAMS), DEFAULT, [f"{path.name} could not be read ({type(e).__name__}): using the built-in default team"]
        _cache.update(key=key, teams=teams, active=active, problems=problems)
    return _cache["teams"], _cache["active"], list(_cache["problems"])


def teams(settings: Settings | None = None) -> dict[str, dict]:
    return load(settings)[0]


def problems(settings: Settings | None = None) -> list[str]:
    return load(settings)[2]


def active_name(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    all_teams, file_active, _ = load(settings)
    return settings.agent_profile if settings.agent_profile in all_teams else file_active


# --------------------------------------------------------------- resolving
def role_config(role: str, settings: Settings | None = None) -> RoleConfig:
    """Model, context, thinking and tools for one role, under the active team."""
    if role not in ROLES:
        raise KeyError(f"Unknown role '{role}'")
    settings = settings or get_settings()
    spec = teams(settings)[active_name(settings)]["roles"].get(role, {"model": DEFAULT})
    override = (settings.role_overrides or {}).get(role)
    if override:
        model, source = override, "override"
    elif spec.get("model", DEFAULT) != DEFAULT:
        model, source = spec["model"], "team"
    else:
        model, source = settings.ollama_model, "default model"
    return RoleConfig(
        role=role, model=model, num_ctx=spec.get("num_ctx") or settings.num_ctx,
        think=spec["think"] if "think" in spec else settings.think,
        tools=spec.get("tools"), source=source)


def model_for(role: str, settings: Settings | None = None) -> str:
    return role_config(role, settings).model


def models_in_use(settings: Settings | None = None) -> list[str]:
    """The distinct models the active team needs (to check they are installed, or to warm them)."""
    seen: list[str] = []
    for role in ROLES:
        m = model_for(role, settings)
        if m not in seen:
            seen.append(m)
    return seen


def describe(settings: Settings | None = None) -> dict:
    """Everything the UI needs to show and edit the teams."""
    settings = settings or get_settings()
    all_teams, _, issues = load(settings)
    resolved = {}
    for role in ROLES:
        rc = role_config(role, settings)
        resolved[role] = {"model": rc.model, "source": rc.source, "num_ctx": rc.num_ctx, "think": rc.think,
                          "tools": rc.tools if rc.tools is not None else "all"}
    return {
        "active": active_name(settings),
        "teams": [{"name": n, "description": t["description"], "roles": t["roles"]} for n, t in all_teams.items()],
        "roles": [vars(r) for r in ROLES.values()],
        "overrides": dict(settings.role_overrides or {}),
        "resolved": resolved,
        "default_model": settings.ollama_model,
        "problems": issues,
    }
