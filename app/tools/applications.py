"""Tool 6: the application tracker (save / update status / list).

Statuses follow a student's real process: interested -> applied -> interview -> offer | rejected.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app import db
from app.tools import tool
from app.tools.job_details import find_job

Status = Literal["interested", "applied", "interview", "offer", "rejected"]


class SaveApplicationArgs(BaseModel):
    job_id: str = Field(description="The vacancy id exactly as returned by search_jobs, e.g. 'forem:2151647'.")
    status: Status = Field(default="interested", description="interested, applied, interview, offer or rejected.")
    notes: str | None = Field(default=None, max_length=500, description="Optional short note.")


class UpdateStatusArgs(BaseModel):
    job_id: str = Field(description="The id of a vacancy that is already in the tracker.")
    status: Status = Field(description="New status: interested, applied, interview, offer or rejected.")
    notes: str | None = Field(default=None, max_length=500, description="Optional short note.")


class ListApplicationsArgs(BaseModel):
    status: Status | None = Field(default=None, description="Only show applications with this status. "
                                                           "Leave empty to show all.")


def _row(app: dict) -> dict:
    return {k: app.get(k) for k in ("job_id", "title", "company", "city", "status", "notes", "updated_at", "url")}


@tool(
    "save_application",
    """Add a vacancy to the user's application tracker (default status 'interested').
    Use when the user wants to keep, follow or note that they applied to a vacancy.
    Needs a vacancy id from search_jobs. Saving the same vacancy again updates its status.""",
    SaveApplicationArgs,
)
def save_application(args: SaveApplicationArgs) -> dict:
    job = find_job(args.job_id)                       # raises a helpful error for unknown ids
    with db.connect() as conn:
        saved = db.save_application(conn, job["id"], args.status, args.notes)
        saved.update(title=job["title"], company=job["company"], city=job["city"], url=job["url"])
    return {"saved": _row(saved)}


@tool(
    "update_application_status",
    """Change the status of a vacancy that is already in the tracker (applied, interview, offer, rejected...).
    Use when the user reports progress, e.g. 'I applied to the first one' or 'I got an interview'.""",
    UpdateStatusArgs,
)
def update_application_status(args: UpdateStatusArgs) -> dict:
    with db.connect() as conn:
        updated = db.update_application_status(conn, args.job_id.strip(), args.status, args.notes)
        if updated is None:
            raise LookupError(f"'{args.job_id}' is not in the tracker yet. Call save_application first.")
        job = db.get_job(conn, updated["job_id"]) or {}
    updated.update(title=job.get("title"), company=job.get("company"), city=job.get("city"), url=job.get("url"))
    return {"updated": _row(updated)}


@tool(
    "list_applications",
    """Show the user's tracked applications with their status. Use when the user asks what they saved,
    applied to, or which interviews they have. Optionally filter by status.""",
    ListApplicationsArgs,
)
def list_applications(args: ListApplicationsArgs) -> dict:
    with db.connect() as conn:
        rows = db.list_applications(conn, args.status)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"count": len(rows), "by_status": counts, "applications": [_row(r) for r in rows]}
