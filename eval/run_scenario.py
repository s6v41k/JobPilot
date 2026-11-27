"""Run ONE question through the real agent with a real model and save what happened.

This is the building block of the benchmark (and the way to reproduce any row of
docs/experiments_log.md). It uses a throw-away database seeded from the saved REAL snapshot (no internet, no Adzuna call), so your
real data is untouched. Add --live to use today's live data instead.

Examples:
    python -m eval.run_scenario --model qwen2.5:7b
    python -m eval.run_scenario --agents lean_tools                 # a team from agents.yaml (see docs)
    python -m eval.run_scenario --model llama3.2:3b --question "Zoek een stage in Antwerpen" --profile none
Result: a JSON file in eval/results/ (events, final answer, timings, guard retractions).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from app import db
from app.config import ROOT_DIR, get_settings
from app.tools.profile_tools import _split_items, sections

DEFAULT_QUESTION = "Find student jobs in Brussels and tell me which one fits my CV best"
RESULTS = ROOT_DIR / "eval" / "results"


def sample_profile(name: str) -> tuple[dict, str]:
    """A profile built from a FICTIONAL sample CV with plain rules (no LLM involved)."""
    text = (ROOT_DIR / "data" / "sample_cvs" / f"{name}.txt").read_text(encoding="utf-8")
    found = sections(text)
    profile = {"name": name.replace("_", " ").title(), "summary": " ".join(found.get("summary", [])),
               "skills": _split_items(found.get("skills", [])), "languages": _split_items(found.get("languages", [])),
               "education": found.get("education", [])[:5], "experience": found.get("experience", [])[:6]}
    return profile, text


def run(model: str, question: str, profile_name: str, think: bool = False, agents: str | None = None,
        live: bool = False, warm: bool = True) -> dict:
    from app import demo
    from app.agent.agent import Agent       # imported late: settings must point to the temp DB first

    with tempfile.TemporaryDirectory() as tmp:
        settings = get_settings()
        if live:
            settings.update(db_path=str(Path(tmp) / "scenario.db"))
            if profile_name != "none":
                profile, cv_text = sample_profile(profile_name)
                with db.connect() as conn:
                    db.save_profile(conn, profile, cv_text=cv_text)
            data_note = "live data"
        else:
            data_note = demo.offline_environment(None if profile_name == "none" else profile_name)
        settings.update(ollama_model=model, think=think, agent_profile=agents or "", role_overrides={})
        if warm:                                             # like the server does at start-up (not part of the timing)
            from app.warmup import BackgroundWarmer
            BackgroundWarmer().warm_now()
        agent = Agent() if agents else Agent(model=model)      # a team decides the chat model; --model pins it otherwise
        events, started = [], time.perf_counter()
        for event in agent.run(question):
            at = round(time.perf_counter() - started, 1)
            if event["type"] == "token":
                continue                                    # the final text is stored once, in "done"
            slim = {k: v for k, v in event.items() if k != "data"}
            if event["type"] == "tool_result" and isinstance(event.get("data"), dict):
                slim["summary"] = {k: (len(v) if isinstance(v, list) else v)
                                   for k, v in event["data"].items() if k in ("count", "data_source", "matches")}
            events.append({"at_s": at, **slim})
        wall = round(time.perf_counter() - started, 1)
    done = next((e for e in events if e["type"] == "done"), {})
    stats = done.get("stats", {})
    seconds = stats.get("eval_duration", 0) / 1e9
    return {
        "model": stats.get("model", model), "agents": agents or "(pinned model)", "data": data_note,
        "question": question, "profile": profile_name, "think": think,
        "date": datetime.now().isoformat(timespec="seconds"),
        "wall_s": wall,
        "tokens_per_s": round(stats.get("eval_count", 0) / seconds, 1) if seconds else None,
        "prompt_tokens": stats.get("prompt_eval_count"),
        "tool_calls": [e["name"] for e in events if e["type"] == "tool_call"],
        "tool_failures": sum(e["type"] == "tool_result" and not e["ok"] for e in events),
        "guard_retractions": sum(e["type"] == "retract" for e in events),
        "errors": [e["message"] for e in events if e["type"] == "error"],
        "answer": done.get("text"),
        "events": events,
    }


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=get_settings().ollama_model)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--profile", default="emma_verbeke", help="sample CV name, or 'none'")
    parser.add_argument("--think", action="store_true", help="enable the model's thinking mode")
    parser.add_argument("--agents", help="name of a team in agents.yaml (the chat model then comes from the team)")
    parser.add_argument("--live", action="store_true", help="use live data instead of the saved snapshot")
    parser.add_argument("--cold", action="store_true", help="do not warm the models first (measures the cold start)")
    args = parser.parse_args()

    result = run(args.model, args.question, args.profile, args.think, args.agents, args.live, warm=not args.cold)
    RESULTS.mkdir(parents=True, exist_ok=True)
    safe_model = args.model.replace(":", "-").replace("/", "-")
    path = RESULTS / f"scenario_{safe_model}_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"model {result['model']} | team {result['agents']} | {result['wall_s']}s | {result['tokens_per_s']} tok/s | "
          f"prompt {result['prompt_tokens']} tok | "
          f"tools {result['tool_calls']} | failures {result['tool_failures']} | "
          f"guard retractions {result['guard_retractions']}")
    print(result["answer"] or result["errors"])
    print(f"saved: {path.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
