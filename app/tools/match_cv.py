"""Tool 4: match_cv - how well do one or more vacancies fit the user's profile?"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app import db
from app.matching import match_jobs
from app.tools import tool
from app.tools.job_details import find_job

MAX_JOBS = 8


class MatchCvArgs(BaseModel):
    job_ids: list[str] = Field(
        min_length=1, max_length=MAX_JOBS,
        description="One or more vacancy ids exactly as returned by search_jobs (max 8). "
                    "Pass ALL the ids you want to compare in ONE call.")

    @field_validator("job_ids", mode="before")
    @classmethod
    def accept_a_single_id_or_a_comma_separated_string(cls, value):
        """Small models often send 'forem:1' or 'forem:1, forem:2' instead of a list."""
        if isinstance(value, str):
            value = [v for v in re.split(r"[,\s;]+", value) if v]
        return value


@tool(
    "match_cv",
    """Score how well vacancies fit the user's CV profile (0-100) and list matched and missing skills
    plus a one-sentence reason. Pass several job ids at once to compare them; the result is sorted
    best first. Needs a profile: if there is none, ask the user to upload their CV first.""",
    MatchCvArgs,
    timeout=120,
)
def match_cv(args: MatchCvArgs) -> dict:
    with db.connect() as conn:
        profile = db.load_profile(conn)
    if not profile:
        raise ValueError("There is no profile yet. Ask the user to upload their CV in the Profile tab "
                         "(or paste it in the chat), then call extract_profile.")
    jobs, unknown = [], []
    for job_id in dict.fromkeys(args.job_ids):                   # drop duplicates, keep order
        try:
            jobs.append(find_job(job_id))
        except LookupError:
            unknown.append(job_id)
    if not jobs:
        raise LookupError(f"Unknown vacancy id(s): {', '.join(unknown)}. Use search_jobs first and copy the ids exactly.")
    return {"matches": match_jobs(jobs, profile), "unknown_ids": unknown}
