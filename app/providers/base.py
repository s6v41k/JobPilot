"""Common building blocks for every job source (Adzuna, scrapers, snapshot).

A provider takes a `JobQuery` and returns a list of `Job`. Everything that touches the
network lives in a provider, so the rest of the app never needs to know where a
vacancy came from.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.geo import is_brussels, normalize

JobType = Literal["student", "part-time", "internship", "full-time"]


class ProviderError(Exception):
    """A source failed. The message is human readable and never contains secrets."""


class BudgetExhausted(ProviderError):
    """The monthly call budget of a limited API (Adzuna) is used up."""


class JobQuery(BaseModel):
    """What the user is looking for. Mirrors the arguments of the search_jobs tool."""
    keywords: str = ""
    job_type: JobType | None = None
    region: Literal["flanders", "wallonia", "brussels"] | None = None
    city: str | None = None
    radius_km: int | None = Field(default=None, ge=1, le=200)
    language: Literal["nl", "fr", "en"] | None = None
    experience_level: Literal["none", "junior", "medior", "senior"] | None = None
    limit: int = Field(default=20, ge=1, le=50)


def localize(query: JobQuery) -> JobQuery:
    """Adapt a query to how Belgian sources work: "Brussels" means the whole region."""
    if is_brussels(query.city):
        return query.model_copy(update={"city": None, "region": query.region or "brussels"})
    return query


class Job(BaseModel):
    """One vacancy. Field names match the `jobs` table in app/db.py."""
    id: str                       # "<source>:<external_id>"
    source: str
    external_id: str
    title: str
    company: str | None = None
    location: str | None = None
    city: str | None = None
    region: str | None = None
    lat: float | None = None
    lon: float | None = None
    description: str | None = None
    url: str | None = None
    job_type: str | None = None
    language: str | None = None
    experience_level: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    posted_at: str | None = None
    fetched_at: str | None = None
    is_snapshot: bool = False

    def to_row(self) -> dict[str, Any]:
        return self.model_dump()


class JobProvider(ABC):
    """Interface every source implements."""
    name: str = "base"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(is it usable right now, short human reason)."""

    @abstractmethod
    def search(self, query: JobQuery) -> list[Job]:
        """Return vacancies for the query. Raise ProviderError on failure."""


# ------------------------------------------------------------ light-weight inference
# Job sites rarely give a clean job type / language, so we infer them from the text.
# These are simple keyword rules (easy to explain), not machine learning.
_INTERNSHIP = re.compile(r"\b(stage|stagiair|stagiaire|internship|intern|praktikum|afstudeer\w*)\b")
# Words that make a job a student job when they appear in the TITLE...
_STUDENT_TITLE = ("student", "etudiant", "jobiste", "jobstudent")
# ...and phrases that count when they appear early in the description.
_STUDENT_TEXT = ("studentenjob", "student job", "jobstudent", "job etudiant", "studentenwerk",
                 "jobiste", "studentencontract", "contrat etudiant")
_LEVELS = (("senior", "senior"), ("medior", "medior"), ("junior", "junior"),
           ("starter", "junior"), ("débutant", "junior"), ("debutant", "junior"),
           ("graduate", "junior"), ("trainee", "junior"))

_STOPWORDS = {
    "nl": {"de", "het", "een", "en", "van", "voor", "met", "je", "wij", "bij", "als", "ervaring", "functie"},
    "fr": {"le", "la", "les", "des", "une", "et", "pour", "avec", "vous", "nous", "dans", "expérience", "poste"},
    "en": {"the", "and", "for", "with", "you", "our", "will", "experience", "to", "of", "is", "are"},
}


def infer_job_type(title: str, description: str = "", contract_time: str | None = None) -> str:
    title_n = normalize(title)
    text = normalize(f"{title} {description[:400]}")
    if _INTERNSHIP.search(text):
        return "internship"
    if any(w in title_n for w in _STUDENT_TITLE) or any(w in text for w in _STUDENT_TEXT):
        return "student"
    if contract_time and "part" in contract_time:
        return "part-time"
    return "full-time"


def infer_experience(title: str, description: str = "") -> str | None:
    text = normalize(f"{title} {description[:300]}")
    for word, level in _LEVELS:
        if re.search(rf"\b{normalize(word)}\b", text):
            return level
    return None


def detect_language(text: str) -> str | None:
    """Guess nl / fr / en by counting common words. None if the text is too short."""
    words = re.findall(r"[a-zà-ÿ']+", (text or "").lower())
    if len(words) < 8:
        return None
    scores = {lang: sum(w in stop for w in words) for lang, stop in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else None


def matches(job: Job, q: JobQuery) -> bool:
    """Apply the filters a source could not apply itself (region, language, ...)."""
    # Unknown region never matches a region filter: we do not guess locations.
    if q.region and job.region != q.region:
        return False
    if q.job_type and job.job_type != q.job_type:
        # A student job can legitimately be part-time work, so accept that overlap.
        if not (q.job_type == "student" and job.job_type == "part-time"):
            return False
    if q.language and job.language and job.language != q.language:
        return False
    if q.experience_level and job.experience_level and job.experience_level != q.experience_level:
        return False
    return True
