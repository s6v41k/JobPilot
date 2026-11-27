"""Adzuna provider (https://developer.adzuna.com), country "be".

Our free plan allows only ~25 calls per month, so this provider is a LAST RESORT:
  1. look in the response cache first (free),
  2. check the monthly budget stored in SQLite,
  3. only then make one HTTP call.
Only search keywords are sent - never a CV or personal data.
The Adzuna terms require attribution: show ATTRIBUTION wherever these jobs appear.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app import db
from app.config import Settings, get_settings
from app.geo import region_from_area
from app.providers.base import (
    BudgetExhausted, Job, JobProvider, JobQuery, ProviderError,
    detect_language, infer_experience, infer_job_type, matches,
)
from app.providers.cache import make_key

BASE_URL = "https://api.adzuna.com/v1/api/jobs/be/search/1"
ATTRIBUTION = {"text": "Jobs by Adzuna", "url": "https://www.adzuna.com"}
CACHE_TTL_SECONDS = 24 * 3600


class AdzunaProvider(JobProvider):
    name = "adzuna"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None,
                 db_path=None):
        self.settings = settings or get_settings()
        self._client = client          # tests inject a fake client (no real network)
        self._db_path = db_path

    # ------------------------------------------------------------------ status
    def available(self) -> tuple[bool, str]:
        if not self.settings.adzuna_configured:
            return False, "Adzuna keys are not set in .env"
        with db.connect(self._db_path) as conn:
            used = db.budget_used(conn, self.name)
        left = self.settings.adzuna_monthly_budget - used
        if left <= 0:
            return False, f"Adzuna monthly budget used up ({used}/{self.settings.adzuna_monthly_budget})"
        return True, f"{left} Adzuna calls left this month"

    # ------------------------------------------------------------------ search
    def build_params(self, q: JobQuery) -> dict[str, Any]:
        """Translate our query into Adzuna parameters (no secrets here)."""
        what = q.keywords.strip()
        if q.job_type == "internship":
            what = f"{what} stage internship".strip()
        elif q.job_type == "student":
            what = f"{what} student".strip()
        params: dict[str, Any] = {
            "results_per_page": min(q.limit, 50),
            "content-type": "application/json",
            "sort_by": "date",
        }
        if what:
            params["what"] = what
        if q.city:
            params["where"] = q.city
            if q.radius_km:
                params["distance"] = q.radius_km
        if q.job_type == "part-time":
            params["part_time"] = 1
        elif q.job_type == "full-time":
            params["full_time"] = 1
        return params

    def search(self, query: JobQuery) -> list[Job]:
        ok, reason = self.available()
        params = self.build_params(query)
        key = make_key(self.name, BASE_URL, params)

        with db.connect(self._db_path) as conn:
            body = db.cache_get(conn, key, CACHE_TTL_SECONDS)          # 1. cache (free)
        if body is None:
            if not ok:                                                  # 2. budget / keys
                raise BudgetExhausted(reason) if "budget" in reason else ProviderError(reason)
            body = self._call(params, key)                              # 3. real call
        jobs = self.parse(json.loads(body))
        return [j for j in jobs if matches(j, query)][: query.limit]

    def _call(self, params: dict[str, Any], key: str) -> str:
        """One real HTTP call. Counted in the budget; response cached."""
        auth = {"app_id": self.settings.adzuna_app_id, "app_key": self.settings.adzuna_app_key}
        client = self._client or httpx.Client(timeout=self.settings.request_timeout)
        try:
            resp = client.get(BASE_URL, params={**auth, **params})
        except httpx.HTTPError as e:
            # Don't include the exception text: it contains the URL with our key.
            raise ProviderError(f"Adzuna is not reachable ({type(e).__name__})") from None
        finally:
            if self._client is None:
                client.close()

        with db.connect(self._db_path) as conn:
            db.budget_add(conn, self.name)       # the server answered, so a call was used
            if resp.status_code == 200:
                db.cache_set(conn, key, self.name, resp.text)
        if resp.status_code in (401, 403):
            raise ProviderError("Adzuna rejected our keys (check ADZUNA_APP_ID / ADZUNA_APP_KEY)")
        if resp.status_code == 429:
            raise ProviderError("Adzuna rate limit reached, try again later")
        if resp.status_code != 200:
            raise ProviderError(f"Adzuna returned HTTP {resp.status_code}")
        return resp.text

    # ----------------------------------------------------------------- parsing
    def parse(self, data: dict[str, Any]) -> list[Job]:
        """Convert an Adzuna response into Job objects (skips malformed items)."""
        jobs: list[Job] = []
        for item in data.get("results", []):
            try:
                title = (item.get("title") or "").strip()
                if not title or not item.get("id"):
                    continue
                description = (item.get("description") or "").strip()
                loc = item.get("location") or {}
                area = loc.get("area") or []
                # area = [country, province, (district), municipality]; a bare country means
                # the advert gave no location at all.
                city = area[-1] if len(area) > 1 else None
                jobs.append(Job(
                    id=f"{self.name}:{item['id']}",
                    source=self.name,
                    external_id=str(item["id"]),
                    title=title,
                    company=(item.get("company") or {}).get("display_name"),
                    location=loc.get("display_name"),
                    city=city,
                    region=region_from_area(area),
                    lat=item.get("latitude"),
                    lon=item.get("longitude"),
                    description=description,
                    url=item.get("redirect_url"),
                    job_type=infer_job_type(title, description, item.get("contract_time")),
                    language=detect_language(f"{title}. {description}"),
                    experience_level=infer_experience(title, description),
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                    posted_at=item.get("created"),
                ))
            except Exception:
                continue   # one broken item must never break the whole search
        return jobs
