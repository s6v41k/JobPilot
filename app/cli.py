"""Terminal chat with the JobPilot agent.

Run:  python -m app.cli
Commands:  /reset  forget the conversation   /model NAME  switch model   /quit  leave
"""
from __future__ import annotations

import sys

from app.agent.agent import Agent
from app.config import get_settings

DIM, YELLOW, RED, RESET = "\033[2m", "\033[33m", "\033[31m", "\033[0m"


def show_event(event: dict) -> None:
    """Print one agent event: tool calls dimmed, answer tokens as they stream."""
    kind = event["type"]
    if kind == "token":
        print(event["text"], end="", flush=True)
    elif kind == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in event["arguments"].items())
        print(f"\n{DIM}-> tool: {event['name']}({args}){RESET}", flush=True)
    elif kind == "tool_result":
        status = "ok" if event["ok"] else f"{RED}failed: {event['error']}{DIM}"
        count = ""
        if event["ok"] and isinstance(event["data"], dict) and "count" in event["data"]:
            count = f", {event['data']['count']} results, source: {event['data'].get('data_source')}"
        print(f"{DIM}   {status} ({event['duration_ms']} ms{count}){RESET}", flush=True)
    elif kind == "retract":
        print(f"\n{RED}[discarded the answer above: {event['reason']} Trying again...]{RESET}\n", flush=True)
    elif kind == "error":
        print(f"\n{RED}{event['message']}{RESET}", flush=True)
    elif kind == "done":
        s = event["stats"]
        speed = s.get("eval_count", 0) / (s["eval_duration"] / 1e9) if s.get("eval_duration") else 0
        print(f"\n{DIM}[{s['model']} | {s['elapsed_s']}s | {s['tool_runs']} tool calls | "
              f"{speed:.1f} tokens/s | prompt {s.get('prompt_eval_count', 0)} tokens]{RESET}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")        # Windows consoles: accents and Cyrillic
    agent = Agent()
    print(f"{YELLOW}JobPilot{RESET} - find jobs in Belgium. Model: {agent.model}. "
          "Type /quit to leave, /reset to start over.")
    while True:
        try:
            user = input(f"\n{YELLOW}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/quit", "/exit"):
            break
        if user == "/reset":
            agent.reset()
            print("Conversation cleared.")
            continue
        if user.startswith("/model"):
            name = user.split(maxsplit=1)[1:] or [""]
            if name[0]:
                agent.model = name[0]
                get_settings().update(ollama_model=name[0])
            print(f"Model: {agent.model}")
            continue
        print(f"{YELLOW}jobpilot>{RESET} ", end="", flush=True)
        for event in agent.run(user):
            show_event(event)


if __name__ == "__main__":
    main()
