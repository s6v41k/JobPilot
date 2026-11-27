"""Tool 2: get_job_details - everything we know about one vacancy."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import db
from app.tools import tool

SOURCE_NOTES = {
    "forem": "Le Forem open data has no free text, so the description is built from its structured fields. "
             "The full ad is at the url.",
    "adzuna": "Adzuna only provides the first ~500 characters of the ad. The full ad is at the url.",
}


class JobDetailsArgs(BaseModel):
    job_id: str = Field(description="The vacancy id exactly as returned by search_jobs, e.g. 'forem:2151647'.")


def find_job(job_id: str) -> dict:
    """Load a vacancy from the local database or raise a helpful error."""
    with db.connect() as conn:
        job = db.get_job(conn, job_id.strip())
    if not job:
        raise LookupError(f"No vacancy with id '{job_id}'. Use search_jobs first and copy the id exactly "
                          "(it looks like forem:123456).")
    return job


@tool(
    "get_job_details",
    """Get the full details of ONE vacancy by its id: description, company, place, language,
    salary and the link to apply. Use it when the user asks about a specific vacancy
    (after search_jobs returned its id).""",
    JobDetailsArgs,
)
def get_job_details(args: JobDetailsArgs) -> dict:
    job = find_job(args.job_id)
    salary = None
    if job.get("salary_min") or job.get("salary_max"):
        salary = f"{job.get('salary_min') or '?'} - {job.get('salary_max') or '?'} EUR per year"
    return {
        "id": job["id"], "title": job["title"], "company": job["company"], "location": job["location"],
        "city": job["city"], "region": job["region"], "job_type": job["job_type"], "language": job["language"],
        "experience_level": job["experience_level"], "salary": salary, "posted_at": job["posted_at"],
        "url": job["url"], "source": job["source"], "description": (job["description"] or "")[:1500],
        "note": SOURCE_NOTES.get(job["source"]),
        "outdated_snapshot": bool(job["is_snapshot"]),
    }
