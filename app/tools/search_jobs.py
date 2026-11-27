"""Tool 1: search_jobs - find real vacancies in Belgium."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.providers.aggregator import search_all
from app.providers.base import JobQuery
from app.tools import tool


class SearchJobsArgs(BaseModel):
    keywords: str = Field(
        default="",
        description="1-3 short words from the job title, e.g. 'barista', 'developer', 'marketing'. "
                    "Leave empty to browse the newest vacancies. Do NOT put the city or job type here.")
    job_type: Literal["student", "part-time", "internship", "full-time"] | None = Field(
        default=None, description="Kind of work: student job, part-time, internship or full-time.")
    region: Literal["flanders", "wallonia", "brussels"] | None = Field(
        default=None, description="Belgian region.")
    city: str | None = Field(default=None, description="City or municipality, e.g. 'Antwerp', 'Leuven'.")
    radius_km: int | None = Field(default=None, ge=1, le=200,
                                  description="Search radius around the city in km. Leave empty normally.")
    language: Literal["nl", "fr", "en"] | None = Field(
        default=None, description="Language the vacancy text is written in. Leave EMPTY unless the user "
                                  "explicitly asks for ads in one language. Do NOT set it to the language "
                                  "the user writes in.")
    experience_level: Literal["none", "junior", "medior", "senior"] | None = Field(
        default=None, description="Required experience.")
    limit: int = Field(default=5, ge=1, le=20, description="How many vacancies to return (default 5, max 20).")


    @field_validator("radius_km", mode="before")
    @classmethod
    def zero_means_not_set(cls, value):
        """Small models fill every optional field; 0 km / 0 results really mean 'not set'."""
        return None if value in (0, "0") else value


def _short(text: str | None, n: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


@tool(
    "search_jobs",
    """Search REAL, current vacancies in Belgium (student jobs, part-time, internships, full-time).
    Use this whenever the user wants to find or browse jobs. Returns a list of vacancies with an id;
    pass that id to other tools (get_job_details, match_cv, draft_cover_letter).
    Only report vacancies that this tool returned - never invent any.""",
    SearchJobsArgs,
)
def search_jobs(args: SearchJobsArgs) -> dict:
    query = JobQuery(**args.model_dump())
    outcome = search_all(query)
    return {
        "count": len(outcome.jobs),
        "data_source": outcome.data_source,       # live | cached | snapshot
        "snapshot_date": outcome.snapshot_date,
        "notes": outcome.notes,                   # e.g. "live sources unavailable"
        "attribution": outcome.attribution,       # licence/terms: must be shown to the user
        "jobs": [
            {
                "id": j.id, "title": j.title, "company": j.company, "city": j.city,
                "region": j.region, "job_type": j.job_type, "language": j.language,
                "posted_at": j.posted_at, "url": j.url, "summary": _short(j.description),
            }
            for j in outcome.jobs
        ],
    }
