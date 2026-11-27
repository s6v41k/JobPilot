"""Two tiny text helpers for the agent (plain word lists, no machine learning).

* guess_language: which language is the user writing in? Works on SHORT messages ("Zoek een stage in Antwerpen"),
  where statistical language detectors fail. Used to tell the model which language to answer in:
  a 7B model often answers in English even when asked in Dutch.
* looks_like_job_request: is this a question about vacancies / internships / student jobs? Used to make sure the
  model calls the search tool instead of answering from memory.
"""
from __future__ import annotations

import re

from app.geo import normalize

_WORDS = {
    "nl": {"zoek", "zoeken", "een", "ik", "naar", "voor", "het", "de", "van", "met", "wil", "kan", "mijn", "welke",
           "stage", "stages", "baan", "banen", "vacature", "vacatures", "studentenjob", "studentenjobs", "werk",
           "in", "bij", "graag", "jij", "u", "geef", "toon", "past", "beste", "wat", "hoe", "niet", "ook"},
    "fr": {"chercher", "cherche", "cherchez", "un", "une", "je", "pour", "des", "les", "avec", "dans", "mon", "ma",
           "mes", "quel", "quelle", "quels", "emploi", "emplois", "stage", "stages", "offre", "offres", "boulot",
           "étudiant", "etudiant", "à", "a", "au", "du", "trouve", "trouver", "veux", "peux", "mieux", "quoi"},
    "en": {"find", "search", "the", "me", "for", "and", "with", "which", "what", "is", "are", "there", "any", "my",
           "a", "an", "to", "in", "of", "jobs", "job", "student", "write", "show", "tell", "best", "fits", "can",
           "i", "you", "about", "please", "how", "do", "does"},
}
_NAMES = {"nl": "Dutch", "fr": "French", "ru": "Russian", "en": "English"}

_JOB_WORDS = re.compile(
    r"\b(jobs?|vacanc\w*|vacatures?|stages?|stagiair\w*|stagiaire\w*|internships?|banen|baan|studentenjob\w*|"
    r"jobstudent\w*|emplois?|offres?|boulots?|jobistes?|hiring|opening|openings|career|carriere|carri\w+re)\b"
    r"|работ\w*|ваканс\w*|стаж\w*|подработ\w*",
    re.IGNORECASE)


def guess_language(text: str) -> str | None:
    """'nl' | 'fr' | 'en' | 'ru' | None (unsure)."""
    if re.search(r"[Ѐ-ӿ]", text or ""):
        return "ru"
    words = re.findall(r"[\w']+", (text or "").lower())
    if not words:
        return None
    scores = {lang: sum(w in vocabulary for w in words) for lang, vocabulary in _WORDS.items()}
    # words shared by several languages ("a", "in", "stage") must not decide alone
    best = max(scores, key=lambda k: (scores[k], k == "en"))
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] < 2 or (len(ranked) > 1 and ranked[0] == ranked[1] and best != "en"):
        return None
    return best


def language_name(code: str | None) -> str | None:
    return _NAMES.get(code or "")


def reply_hint(user_text: str) -> str:
    """Text appended to the question the MODEL sees (not shown in the UI) when the user does not write English."""
    code = guess_language(user_text)
    if code in ("nl", "fr", "ru"):
        return f"\n\n(Reply in {_NAMES[code]}.)"
    return ""


def looks_like_job_request(text: str) -> bool:
    return bool(_JOB_WORDS.search(normalize(text or "")) or _JOB_WORDS.search(text or ""))
