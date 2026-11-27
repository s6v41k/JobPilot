"""Tool 5: draft_cover_letter - a SHORT motivation letter for one vacancy."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app import db
from app.llm import LLMError, chat_text
from app.skills import language_levels
from app.tools import tool
from app.tools.job_details import find_job

MAX_WORDS = 150            # the brief: short, about motivation and personal qualities
HARD_LIMIT_WORDS = 175     # above this we ask for a shorter version / cut at a sentence

LANGUAGE_NAMES = {"nl": "Dutch", "fr": "French", "en": "English"}
GREETINGS = {"nl": "Beste team,", "fr": "Madame, Monsieur,", "en": "Dear hiring team,"}
TONES = {
    "friendly": "warm, natural and polite, like a motivated student writing to a real person",
    "professional": "formal, correct and concise",
    "enthusiastic": "energetic and positive, but still sincere and not exaggerated",
}

LETTER_SYSTEM = """You write short motivation letters for students applying to jobs in Belgium.
Rules:
- Write in {language}. Tone: {tone}.
- At most {max_words} words in total, 3 short paragraphs: why this job and company, which personal qualities fit (reliable, friendly, eager to learn...), and a polite closing.
- Focus on MOTIVATION and PERSONAL QUALITIES. Do NOT list technologies, tools or a skills inventory. No life story.
- Use ONLY facts from the PROFILE. Never invent experience, diplomas or numbers.
- The letter is written BY the candidate TO the hiring team of the company. Start with exactly this greeting: "{greeting}" Never greet the candidate.
- Do not name any school, university or employer that is not in the PROFILE.
- Never use [square brackets] or placeholders. Do not invent a recipient name.
- End with a short sign-off and the candidate's name (if known).
- Output only the letter, no explanations."""


class CoverLetterArgs(BaseModel):
    job_id: str = Field(description="The vacancy id exactly as returned by search_jobs, e.g. 'forem:2151647'.")
    language: Literal["nl", "fr", "en"] | None = Field(
        default=None, description="Language of the letter. Leave empty to use the language of the vacancy "
                                  "(or English if the user does not speak it).")
    tone: Literal["friendly", "professional", "enthusiastic"] = Field(
        default="friendly", description="Tone of the letter.")


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def shorten(text: str, limit: int = HARD_LIMIT_WORDS) -> str:
    """Cut a too-long letter at the last full sentence that still fits (never mid-sentence)."""
    if word_count(text) <= limit:
        return text
    paragraphs: list[list[str]] = []
    total = 0
    for block in text.split("\n\n"):
        sentences: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", block.strip()):
            n = word_count(sentence)
            if total + n > limit:
                if sentences:
                    paragraphs.append(sentences)
                return "\n\n".join(" ".join(p) for p in paragraphs) or sentence
            sentences.append(sentence)
            total += n
        paragraphs.append(sentences)
    return text


def choose_language(requested: str | None, job: dict, profile: dict) -> str:
    if requested:
        return requested
    levels = language_levels(profile.get("languages") or [])
    job_language = job.get("language")
    return job_language if job_language and levels.get(job_language, 0) >= 2 else "en"


def profile_facts(profile: dict) -> str:
    lines = []
    for label, key in (("Name", "name"), ("Summary", "summary")):
        if profile.get(key):
            lines.append(f"{label}: {profile[key]}")
    for label, key in (("Education", "education"), ("Experience", "experience"), ("Languages", "languages"),
                       ("Interests", "interests")):
        if profile.get(key):
            lines.append(f"{label}: " + "; ".join(profile[key][:5]))
    return "\n".join(lines)


_PLACEHOLDER = re.compile(r"\s*\[[^\]]{1,60}\]")


_INSTITUTION = re.compile(
    r"\b(?:Universit\w+|Hogeschool|Hautes?\s+[ÉE]cole\w*|KU\s+Leuven|UGent|VUB|ULB|UCLouvain|ULi[eè]ge)\b[^.,;\n]{0,30}")
_GENERIC = {"universit", "universiteit", "université", "university", "hogeschool", "haute", "hautes", "école", "ecole"}


def invented_institutions(letter: str, facts: str) -> list[str]:
    """Schools/universities named in the letter that the profile never mentions."""
    facts = facts.lower()
    wrong = []
    for match in _INSTITUTION.finditer(letter):
        text = match.group(0).strip()
        words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 3 and w not in _GENERIC]
        known = any(w in facts for w in words) if words else ("universit" in facts)
        if not known:
            wrong.append(text)
    return wrong


def problems_in(letter: str, profile: dict, language: str) -> list[str]:
    """Things a student would be embarrassed by, with the instruction that fixes each."""
    found = []
    candidate_name = profile.get("name")
    wrong_school = invented_institutions(letter, profile_facts(profile))
    if wrong_school:
        found.append(f"It named '{wrong_school[0]}', which is not in the PROFILE. Remove every school or "
                     "employer that is not in the PROFILE.")
    if word_count(letter) > HARD_LIMIT_WORDS:
        found.append(f"It had {word_count(letter)} words. Write it again in at most {MAX_WORDS - 20} words.")
    if _PLACEHOLDER.search(letter):
        found.append("It contained [placeholders]. Never use square brackets: greet 'Dear hiring team,' "
                     "(or 'Beste,' / 'Madame, Monsieur,').")
    first_line = letter.strip().splitlines()[0] if letter.strip() else ""
    if candidate_name and candidate_name.split()[0].lower() in first_line.lower():
        found.append("It greeted the candidate. The letter is written BY the candidate TO the hiring team.")
    return found


def write_letter(job: dict, profile: dict, language: str, tone: str, model: str | None = None) -> str:
    system = LETTER_SYSTEM.format(language=LANGUAGE_NAMES[language], tone=TONES[tone], max_words=MAX_WORDS,
                                  greeting=GREETINGS[language])
    user = (f"VACANCY\nTitle: {job['title']}\nCompany: {job.get('company') or 'unknown'}\n"
            f"Place: {job.get('city') or job.get('location') or 'Belgium'}\n"
            f"Details: {(job.get('description') or '')[:500]}\n\nPROFILE\n{profile_facts(profile)}")
    letter = chat_text(system, user, model=model, max_tokens=420)
    issues = problems_in(letter, profile, language)
    if issues:                                                 # one polite retry with precise feedback
        letter = chat_text(system, user + "\n\nYour previous letter had problems: " + " ".join(issues),
                           model=model, max_tokens=420)
    letter = _PLACEHOLDER.sub("", letter)                      # last resort: drop leftover [placeholders]
    return fix_greeting(shorten(letter), language)


def fix_greeting(letter: str, language: str) -> str:
    """Models often copy the English example greeting into a Dutch/French letter: replace it."""
    first, _, rest = letter.partition("\n")
    if language != "en" and re.match(r"\s*dear\b", first, re.IGNORECASE):
        return GREETINGS[language] + ("\n" + rest if rest else "")
    return letter


@tool(
    "draft_cover_letter",
    """Write a SHORT motivation letter (about 120-150 words) for one vacancy, based on the user's CV profile.
    Use it when the user asks for a cover letter or motivation letter. Needs a vacancy id from
    search_jobs and a profile (the user's CV). Show the letter to the user exactly as returned.""",
    CoverLetterArgs,
    timeout=300,
)
def draft_cover_letter(args: CoverLetterArgs) -> dict:
    with db.connect() as conn:
        profile = db.load_profile(conn)
    if not profile:
        raise ValueError("There is no profile yet. Ask the user to upload their CV in the Profile tab first.")
    job = find_job(args.job_id)
    language = choose_language(args.language, job, profile)
    try:
        letter = write_letter(job, profile, language, args.tone)
    except LLMError as e:
        raise RuntimeError(str(e)) from None
    return {"letter": letter, "word_count": word_count(letter), "language": language, "tone": args.tone,
            "job": {"id": job["id"], "title": job["title"], "company": job["company"]}}
