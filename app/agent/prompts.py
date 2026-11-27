"""The system prompt: the agent's job description.

Small local models follow short, concrete, numbered rules much better than long prose,
so the prompt is deliberately blunt. The tool list is generated from the registry, so adding a tool
automatically teaches the agent about it. A rule that talks about a tool is only included when the
agent may use that tool (an agent role can be limited to a few tools, see agents.yaml): a rule about a
tool the model cannot call would only confuse it.
"""
from __future__ import annotations

from datetime import date

from app.tools import TOOLS

INTRO = ("You are JobPilot, a friendly assistant that helps students find student jobs, part-time jobs, "
         "internships and full-time jobs anywhere in Belgium (Flanders, Wallonia, Brussels).")

# (tool the rule needs or None, text). Numbered automatically, in this order.
RULES: list[tuple[str | None, str]] = [
    (None, "For anything about vacancies, CALL A TOOL. Never answer from memory and NEVER invent vacancies, "
           "companies or links."),
    (None, "Only mention vacancies that a tool returned. Copy titles, companies and cities exactly. Write each "
           "vacancy on ONE short line: Title - Company, City (id: forem:123). Do not repeat links or descriptions; "
           "the app shows them as cards."),
    (None, "You may call several tools one after another. If you still need a tool, call it IMMEDIATELY - do not "
           "write a list or an introduction first. Stop calling tools as soon as you can answer."),
    ("match_cv", "To compare or rank vacancies, call match_cv ONCE with ALL the ids returned by the search."),
    (None, "If a tool returns an error, read the message, fix your arguments and try once more. If it still fails, "
           "say so honestly in one sentence."),
    (None, "If a search finds nothing, say so and suggest one broader search (other city, fewer keywords). Vacancy "
           "titles are mostly Dutch or French, so try those words too."),
    (None, 'If a tool result says the data is "cached" or a "snapshot", tell the user the vacancies may be outdated.'),
    (None, "Reply in the SAME language the user writes in (English, Dutch, French or Russian). Keep answers SHORT: "
           "one intro line, at most 5 vacancies, one closing line."),
    ("extract_profile", 'Call extract_profile ONLY when the user just uploaded or pasted a CV. If "What you know '
                        'about the user" appears below, the profile already exists: do NOT call extract_profile again.'),
    (None, 'When a tool result contains "attribution", end the answer with a short source line, for example: '
           'Source: Le Forem open data.'),
    (None, "Do not give legal or medical advice. For work-permit or contract questions, say you are not sure and "
           "point to official sources."),
]


def tool_list(names: list[str] | None = None) -> str:
    """One line per tool: 'name - first sentence of its description'. `names` limits the list."""
    lines = []
    for t in TOOLS.values():
        if names is not None and t.name not in names:
            continue
        first = t.description.split(". ")[0].rstrip(".")
        lines.append(f"- {t.name}: {first}.")
    return "\n".join(lines)


def rules_text(names: list[str] | None = None) -> str:
    kept = [text for tool, text in RULES if tool is None or names is None or tool in names]
    return "\n".join(f"{i}. {text}" for i, text in enumerate(kept, start=1))


def build_system_prompt(profile: dict | None = None, today: date | None = None,
                        tools: list[str] | None = None) -> str:
    """Fill in the prompt. `profile` (the student's saved profile) is added when known;
    `tools` limits the tools the agent is told about (None = all)."""
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
    return (f"{INTRO}\n\nTOOLS you can call:\n{tool_list(tools)}\n\nRULES:\n{rules_text(tools)}\n\n"
            f"Today is {(today or date.today()).isoformat()}.{profile_block}")
