"""Keep a local copy of the Le Forem dataset.

Why: the open-data portal allows only 10 000 anonymous requests per day per address, and we
already hit that limit while developing. One bulk download (~25 000 current offers, a single
request) is enough to answer every search locally - instantly, offline, and without
bothering the portal.

    python -m app.providers.forem_mirror          # download / refresh now
The server also refreshes the copy in the background every few hours.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone

import httpx

from app import db
from app.config import get_settings
from app.providers.base import ProviderError
from app.providers.forem import DATASET_URL

EXPORT_URL = DATASET_URL.replace("/records", "/exports/json")   # the "download everything" endpoint
MIN_PLAUSIBLE_OFFERS = 500           # a download with fewer offers is an error page, not the dataset
STALE_AFTER_HOURS = 6


def age_hours(db_path=None) -> float | None:
    """Hours since the last successful download, or None if there never was one."""
    with db.connect(db_path) as conn:
        state = db.forem_mirror_state(conn)
    if not state:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(state["refreshed_at"])).total_seconds() / 3600


def info(db_path=None) -> dict:
    with db.connect(db_path) as conn:
        state = db.forem_mirror_state(conn)
    age = age_hours(db_path)
    return {"offers": state["offers"] if state else 0, "refreshed_at": state["refreshed_at"] if state else None,
            "age_hours": round(age, 1) if age is not None else None}


def refresh(client: httpx.Client | None = None, db_path=None, today: date | None = None) -> int:
    """Download all current offers and replace the local copy. Returns the number of offers.

    On ANY failure the old copy stays untouched (a stale list is better than none)."""
    settings = get_settings()
    own = client is None
    client = client or httpx.Client(timeout=180)
    try:
        response = client.get(
            EXPORT_URL, params={"where": f"datefindiffusion >= date'{(today or date.today()).isoformat()}'"},
            headers={"User-Agent": settings.user_agent})
    except httpx.HTTPError as e:
        raise ProviderError(f"Le Forem open data is not reachable ({type(e).__name__})") from None
    finally:
        if own:
            client.close()
    if response.status_code == 429:
        raise ProviderError("Le Forem open data daily request limit reached; the saved copy is kept.")
    if response.status_code != 200:
        raise ProviderError(f"Le Forem open data returned HTTP {response.status_code}")
    try:
        records = response.json()
    except ValueError:
        raise ProviderError("Le Forem open data returned something that is not JSON") from None
    if isinstance(records, dict):                                   # tolerate {"results": [...]}
        records = records.get("results", [])
    if not isinstance(records, list) or len(records) < MIN_PLAUSIBLE_OFFERS:
        raise ProviderError("Le Forem open data returned an unexpectedly small answer; the saved copy is kept.")
    with db.connect(db_path) as conn:
        return db.forem_replace_all(conn, records)


def refresh_if_stale(max_age_hours: float = STALE_AFTER_HOURS, db_path=None) -> str:
    """Refresh when the copy is missing or old. Returns a one-line status (never raises)."""
    age = age_hours(db_path)
    if age is not None and age < max_age_hours:
        return f"Le Forem copy is fresh ({age:.1f} h old)"
    try:
        return f"Le Forem copy refreshed: {refresh(db_path=db_path)} offers"
    except ProviderError as e:
        return f"Le Forem copy NOT refreshed: {e}"
    except Exception as e:                                          # never let a background job crash
        return f"Le Forem copy NOT refreshed ({type(e).__name__})"


class BackgroundRefresher:
    """A daemon thread that keeps the copy fresh while the server runs."""

    def __init__(self, every_hours: float = 1, max_age_hours: float = STALE_AFTER_HOURS):
        self._stop = threading.Event()
        self._every, self._max_age = every_hours * 3600, max_age_hours
        self.last_message = "not started"
        self._thread = threading.Thread(target=self._loop, name="forem-refresh", daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.last_message = refresh_if_stale(self._max_age)
            print(f"[forem] {self.last_message}")
            self._stop.wait(self._every)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    print(refresh_if_stale(max_age_hours=0))
