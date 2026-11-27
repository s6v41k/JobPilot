"""The smallest possible JobPilot agent (~50 lines): a safe fallback for the live demo.

Same idea as the full agent in app/agent/agent.py, without streaming, memory or retries:
ask the LLM -> if it wants a tool, run it -> give the result back -> repeat.

Run:  python -m app.agent_minimal
"""
import ollama

from app.config import get_settings
from app.tools import call_tool, tool_schemas
import json

settings = get_settings()
client = ollama.Client(host=settings.ollama_host, timeout=settings.request_timeout)

SYSTEM = ("You are JobPilot, an assistant that finds real vacancies in Belgium. "
          "Use the search_jobs tool for every job question and never invent vacancies. "
          "Mention each vacancy id in brackets. Answer in the user's language, briefly.")


def ask(messages: list) -> str:
    """One user turn: loop until the model answers without asking for a tool."""
    for _ in range(6):                                          # max 6 tool rounds
        reply = client.chat(model=settings.ollama_model, messages=messages,
                            tools=tool_schemas(), options={"num_ctx": settings.num_ctx}).message
        messages.append(reply)                                  # keep the model's turn
        if not reply.tool_calls:
            return reply.content                                # plain answer: done
        for call in reply.tool_calls:                           # the model wants tools
            result = call_tool(call.function.name, dict(call.function.arguments))
            print(f"  [tool] {call.function.name}({dict(call.function.arguments)}) -> ok={result['ok']}")
            messages.append({"role": "tool", "tool_name": call.function.name,
                             "content": json.dumps(result, ensure_ascii=False)[:6000]})
    return "Sorry, I could not finish that search."


if __name__ == "__main__":
    chat = [{"role": "system", "content": SYSTEM}]
    while (text := input("you> ").strip()) not in ("/quit", ""):
        chat.append({"role": "user", "content": text})
        try:
            print("jobpilot>", ask(chat))
        except Exception as e:                                  # Ollama down, model missing...
            print("Sorry, something went wrong:", e)
