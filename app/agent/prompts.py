"""The system prompt: the agent's job description.

Small local models follow short, concrete, numbered rules much better than long prose,
so the prompt is deliberately blunt. The tool list is generated from the registry,
so adding a tool automatically teaches the agent about it.
"""
from __future__ import annotations

from datetime import date

from app.tools import TOOLS

SYSTEM_TEMPLATE = """You are JobPilot, a friendly assistant that helps students find student jobs, part-time jobs, internships and full-time jobs anywhere in Belgium (Flanders, Wallonia, Brussels).

TOOLS you can call:
{tool_list}

RULES:
1. For anything about vacancies, CALL A TOOL. Never answer from memory and NEVER invent vacancies, companies or links.
2. Only mention vacancies that a tool returned. Copy titles, companies and cities exactly. Write each vacancy on ONE short line: Title - Company, City (id: forem:123). Do not repeat links or descriptions; the app shows them as cards.
3. You may call several tools one after another (for example: search, then check details). Stop calling tools as soon as you can answer.
4. If a tool returns an error, read the message, fix your arguments and try once more. If it still fails, say so honestly in one sentence.
5. If a search finds nothing, say so and suggest one broader search (other city, fewer keywords). Vacancy titles are mostly Dutch or French, so try those words too.
6. If a tool result says the data is "cached" or a "snapshot", tell the user the vacancies may be outdated.
7. Reply in the SAME language the user writes in (English, Dutch, French or Russian). Keep answers SHORT: one intro line, at most 5 vacancies, one closing line.
8. If the user shares or uploads a CV, call extract_profile.
9. When a tool result contains "attribution", end the answer with a short source line, for example: Source: Le Forem open data.
10. Do not give legal or medical advice. For work-permit or contract questions, say you are not sure and point to official sources.

Today is {today}.{profile_block}"""


def tool_list() -> str:
    """One line per tool: 'name - first sentence of its description'."""
    lines = []
    for t in TOOLS.values():
        first = t.description.split(". ")[0].rstrip(".")
        lines.append(f"- {t.name}: {first}.")
    return "\n".join(lines)


def build_system_prompt(profile: dict | None = None, today: date | None = None) -> str:
    """Fill in the template. `profile` (the student's saved profile) is added when known."""
    profile_block = ""
    if profile:
        def line(label: str, value) -> str:
            """'- Label: a, b, c' for a non-empty value (lists are cut to 10 items)."""
            value = ", ".join(value[:10]) if isinstance(value, list) else value
            return f"\n- {label}: {value}" if value else ""

        profile_block = ("\n\nWhat you know about the user (from their CV):"
                         + line("Name", profile.get("name")) + line("City", profile.get("city"))
                         + line("Education", profile.get("education")) + line("Skills", profile.get("skills"))
                         + line("Languages", profile.get("languages")))
    return SYSTEM_TEMPLATE.format(
        tool_list=tool_list(), today=(today or date.today()).isoformat(), profile_block=profile_block)
