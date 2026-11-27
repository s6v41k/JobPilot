"""Snapshot provider: the offline safety net.

`data/snapshots/` holds REAL responses we saved earlier from Le Forem and Adzuna.
When the internet or every live source is down (e.g. bad Wi-Fi during the demo), the
agent still has real vacancies - clearly labelled "cached real data" with the date.
Nothing here is invented: it only re-reads files we downloaded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import ROOT_DIR
from app.geo import normalize
from app.providers.adzuna import AdzunaProvider
from app.providers.base import Job, JobProvider, JobQuery, matches
from app.providers.forem import ForemProvider

SNAPSHOT_DIR = ROOT_DIR / "data" / "snapshots"
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class SnapshotProvider(JobProvider):
    name = "snapshot"

    def __init__(self, directory: Path | None = None):
        self.directory = directory or SNAPSHOT_DIR

    def _files(self) -> list[Path]:
        return sorted(self.directory.glob("*.json")) if self.directory.exists() else []

    def available(self) -> tuple[bool, str]:
        files = self._files()
        return bool(files), f"{len(files)} snapshot file(s)" if files else "no snapshot files"

    def snapshot_date(self) -> str | None:
        """Date of the newest snapshot file (taken from its file name)."""
        dates = [m.group(1) for f in self._files() if (m := _DATE.search(f.name))]
        return max(dates) if dates else None

    def load_all(self) -> list[Job]:
        jobs: list[Job] = []
        for file in self._files():
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                parser = ForemProvider().parse if file.name.startswith("forem") else AdzunaProvider().parse
                for job in parser(data):
                    job.is_snapshot = True
                    jobs.append(job)
            except Exception:
                continue        # a broken snapshot file must not break the app
        return jobs

    def search(self, query: JobQuery) -> list[Job]:
        words = [normalize(w) for w in query.keywords.split()]
        found = []
        for job in self.load_all():
            haystack = normalize(f"{job.title} {job.company or ''} {job.description or ''}")
            if all(w in haystack for w in words) and matches(job, query):
                if query.city and normalize(query.city) not in normalize(f"{job.city or ''} {job.location or ''}"):
                    continue
                found.append(job)
        return found[: query.limit]
