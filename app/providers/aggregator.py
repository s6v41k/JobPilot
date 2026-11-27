"""Combine all sources into one search, with a clear fallback order.

    1. Le Forem open data   (free, live)                      -> data_source "live"
    2. Adzuna               (only if step 1 gave too few; limited budget)
    3. every live source failed -> vacancies saved earlier in SQLite -> "cached"
    4. nothing there either     -> real snapshot files               -> "snapshot"

Search keywords are the only thing that ever leaves the machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import db
from app.providers import adzuna as adzuna_module
from app.providers import forem as forem_module
from app.providers import forem_mirror
from app.providers.adzuna import AdzunaProvider
from app.providers.base import Job, JobProvider, JobQuery, ProviderError, localize
from app.providers.forem import ForemProvider
from app.providers.keywords import expand_keywords
from app.providers.snapshot import SnapshotProvider

# Adzuna has a tiny free quota (25 calls/month), so it is used ONLY when Le Forem found nothing at all
# (or failed). A Jobs-tab click or a chat question must never spend it just because results were few.
MIN_RESULTS_BEFORE_ADZUNA = 1
STALE_AFTER_HOURS_FOR_LABEL = 24      # a local copy older than this is labelled "cached", not "live"


@dataclass
class SearchOutcome:
    jobs: list[Job] = field(default_factory=list)
    data_source: str = "live"            # live | cached | snapshot
    notes: list[str] = field(default_factory=list)
    attribution: list[dict] = field(default_factory=list)
    snapshot_date: str | None = None


def _dedupe(jobs: list[Job]) -> list[Job]:
    """Same title + company + city = same vacancy (agencies post duplicates)."""
    seen, unique = set(), []
    for job in jobs:
        key = ((job.title or "").lower(), (job.company or "").lower(), (job.city or "").lower())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def search_all(
    query: JobQuery,
    forem: JobProvider | None = None,
    adzuna: JobProvider | None = None,
    snapshot: SnapshotProvider | None = None,
    db_path=None,
    min_results: int = MIN_RESULTS_BEFORE_ADZUNA,
) -> SearchOutcome:
    forem = forem or ForemProvider()
    adzuna = adzuna or AdzunaProvider()
    snapshot = snapshot or SnapshotProvider()
    out = SearchOutcome()
    attempted = failed = 0

    local = localize(query)        # "Brussels" -> the whole Brussels region

    # 1. Le Forem (also searches NL/FR translations of simple English keywords)
    attempted += 1
    try:
        found: list[Job] = []
        for variant in expand_keywords(local.keywords):
            found += forem.search(local.model_copy(update={"keywords": variant}))
        if not found and local.language:
            # Small models often pass the USER's language ("en") as the language of the ad, which hides every
            # Dutch/French vacancy. Retry without that filter (before spending any Adzuna call) and say so.
            for variant in expand_keywords(local.keywords):
                found += forem.search(local.model_copy(update={"keywords": variant, "language": None}))
            if found:
                out.notes.append(f"No vacancies are written in '{local.language}'; showing vacancies in any language.")
        out.jobs = _dedupe(found)
        out.attribution.append(forem_module.ATTRIBUTION)
        age = forem_mirror.age_hours(db_path)
        if age is not None and age > STALE_AFTER_HOURS_FOR_LABEL:     # old local copy: say so honestly
            out.data_source = "cached"
            out.notes.append(f"The vacancy list was last refreshed {age:.0f} hours ago, so some offers may be gone.")
    except ProviderError as e:
        failed += 1
        out.notes.append(str(e))

    # 2. Adzuna - only when we still have too little (it has a tiny monthly budget)
    if len(out.jobs) < min_results and getattr(getattr(adzuna, "settings", None), "adzuna_configured", True):
        attempted += 1
        try:
            more = adzuna.search(query)
            out.jobs = _dedupe(out.jobs + more)
            if more:
                out.attribution.append(adzuna_module.ATTRIBUTION)
        except ProviderError as e:
            failed += 1
            out.notes.append(str(e))

    out.jobs = out.jobs[: query.limit]

    # Save live results locally (powers get_job_details, the Jobs tab and the cache fallback).
    if out.jobs:
        with db.connect(db_path) as conn:
            for job in out.jobs:
                db.upsert_job(conn, job.to_row())
        return out

    # 3./4. Fall back only when the live sources FAILED (an honest empty result stays empty).
    if failed < attempted:
        return out
    with db.connect(db_path) as conn:
        rows = db.query_jobs(conn, keywords=local.keywords, job_type=local.job_type,
                             region=local.region, city=local.city, language=local.language,
                             experience_level=query.experience_level, limit=query.limit)
    if rows:
        out.jobs = [Job(**r) for r in rows]
        out.data_source = "cached"
        out.notes.append("Live sources unavailable: showing real vacancies saved earlier on this computer.")
        return out
    snap = snapshot.search(query)
    if snap:
        out.jobs = snap
        out.data_source = "snapshot"
        out.snapshot_date = snapshot.snapshot_date()
        out.notes.append(f"Live sources unavailable: showing a saved snapshot of real vacancies "
                         f"from {out.snapshot_date}. They may be outdated.")
    return out
