"""Le Forem provider (Wallonia) - OFFICIAL open data, no scraping.

Le Forem publishes all the vacancies it distributes on the Open Data Wallonie-Bruxelles
portal (dataset "offres-d-emploi-forem", licence CC BY-SA 4.0, updated continuously).
We query it through the standard Opendatasoft "ODSQL" API.

Good to know:
  * the dataset also contains EXPIRED offers, so we always filter on the end date;
  * it has no free-text description, so we build one from the structured fields
    (occupation, sector, contract, languages...). The offer page has the full text.
Attribution (licence requirement): show ATTRIBUTION wherever these jobs appear.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx

from app import db
from app.config import Settings, get_settings
from app.geo import city_variants, region_from_area
from app.providers.base import (
    Job, JobProvider, JobQuery, ProviderError,
    detect_language, infer_experience, infer_job_type, matches,
)
from app.providers.cache import make_key
from app.providers.ratelimit import RateLimiter

DATASET_URL = "https://www.odwb.be/api/explore/v2.1/catalog/datasets/offres-d-emploi-forem/records"
ATTRIBUTION = {
    "text": "Le Forem - Open Data Wallonie-Bruxelles (CC BY-SA 4.0)",
    "url": "https://www.odwb.be/explore/dataset/offres-d-emploi-forem/",
}
CACHE_TTL_SECONDS = 3600          # offers change during the day, but 1 h is plenty
_GENDER_SUFFIX = re.compile(r"\s*\((?:h|m)/(?:f|h)(?:/x)?\)\s*$", re.IGNORECASE)


_NUTS = {"brussels": "BE1", "flanders": "BE2", "wallonia": "BE3"}


def _q(text: str) -> str:
    """Make user text safe inside an ODSQL string literal (drops quotes/backslashes)."""
    return re.sub(r'["\\]', "", text).strip()


class ForemProvider(JobProvider):
    name = "forem"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None,
                 db_path=None, limiter: RateLimiter | None = None, today: date | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._db_path = db_path
        self._limiter = limiter or RateLimiter(0.5)
        self._today = today

    def available(self) -> tuple[bool, str]:
        return True, "open data, no key needed"

    # ------------------------------------------------------------------ query
    def build_where(self, q: JobQuery) -> str:
        """Translate a JobQuery into an ODSQL `where` clause."""
        today = (self._today or date.today()).isoformat()
        parts = [f"datefindiffusion >= date'{today}'"]          # hide expired offers
        for word in q.keywords.split():
            w = _q(word)
            if w:
                parts.append(f'(search(titreoffre, "{w}") or search(metier, "{w}"))')
        if q.city:
            names = [_q(c) for c in city_variants(q.city) if _q(c)]
            parts.append("(" + " or ".join(f'search(lieuxtravaillocalite, "{n}")' for n in names) + ")")
        if q.region:                                            # NUTS-1: BE1/BE2/BE3
            parts.append(f'search(lieuxtravailregionnuts, "{_NUTS[q.region]}")')
        if q.job_type == "student":
            parts.append('(typecontrat = "Etudiant" or search(titreoffre, "etudiant") '
                         'or search(titreoffre, "student"))')
        elif q.job_type == "internship":
            parts.append('(search(titreoffre, "stage") or search(titreoffre, "stagiaire"))')
        elif q.job_type == "part-time":
            parts.append('regimetravail = "Temps partiel"')
        elif q.job_type == "full-time":
            parts.append('regimetravail = "Temps plein"')
        if q.language:
            parts.append(f'search(languescodeiso2, "{q.language.upper()}")')
        return " and ".join(parts)

    def search(self, query: JobQuery) -> list[Job]:
        params = {
            "where": self.build_where(query),
            "order_by": "datedebutdiffusion desc",
            "limit": min(query.limit * 2, 100),     # fetch extra: local filters may drop some
        }
        key = make_key(self.name, DATASET_URL, params)
        with db.connect(self._db_path) as conn:
            body = db.cache_get(conn, key, CACHE_TTL_SECONDS)
        if body is None:
            body = self._call(params, key)
        jobs = self.parse(json.loads(body))
        return [j for j in jobs if matches(j, query)][: query.limit]

    def _call(self, params: dict[str, Any], key: str) -> str:
        self._limiter.wait("odwb.be")
        client = self._client or httpx.Client(timeout=min(self.settings.request_timeout, 30))
        try:
            resp = client.get(DATASET_URL, params=params,
                              headers={"User-Agent": self.settings.user_agent})
        except httpx.HTTPError as e:
            raise ProviderError(f"Le Forem open data is not reachable ({type(e).__name__})") from None
        finally:
            if self._client is None:
                client.close()
        if resp.status_code == 429:
            raise ProviderError("Le Forem open data rate limit reached, try again later")
        if resp.status_code != 200:
            raise ProviderError(f"Le Forem open data returned HTTP {resp.status_code}")
        with db.connect(self._db_path) as conn:
            db.cache_set(conn, key, self.name, resp.text)
        return resp.text

    # ---------------------------------------------------------------- parsing
    @staticmethod
    def _join(value: Any) -> str | None:
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if v)
        return value or None

    def parse(self, data: dict[str, Any]) -> list[Job]:
        jobs: list[Job] = []
        for item in data.get("results", []):
            try:
                number = item.get("numerooffreforem")
                title = _GENDER_SUFFIX.sub("", (item.get("titreoffre") or "").strip())
                if not number or not title:
                    continue
                cities = item.get("lieuxtravaillocalite") or []
                city = cities[0].title() if cities else None
                geo = (item.get("lieuxtravailgeo") or [{}])[0] or {}
                langs = item.get("languescodeiso2") or []
                part_time = item.get("regimetravail") == "Temps partiel"
                job_type = infer_job_type(title, "", "part_time" if part_time else None)
                # Belgian "student contract" -> student job, but internships are also
                # signed on that contract, so an internship stays an internship.
                if item.get("typecontrat") == "Etudiant" and job_type != "internship":
                    job_type = "student"
                facts = [
                    ("Occupation", item.get("metier")),
                    ("Sector", self._join(item.get("secteurs"))),
                    ("Contract", item.get("typecontrat")),
                    ("Working time", item.get("regimetravail")),
                    ("Education level", self._join(item.get("niveauxetudes"))),
                    ("Languages", self._join(item.get("langues"))),
                    ("Experience", item.get("experiencerequise")),
                    ("Source", item.get("source")),
                ]
                description = ". ".join(f"{k}: {v}" for k, v in facts if v)
                jobs.append(Job(
                    id=f"{self.name}:{number}",
                    source=self.name,
                    external_id=str(number),
                    title=title,
                    company=item.get("nomemployeur"),
                    location=", ".join(c.title() for c in cities) or None,
                    city=city,
                    region=region_from_area(item.get("lieuxtravailregion")),
                    lat=geo.get("lat"),
                    lon=geo.get("lon"),
                    description=description,
                    url=item.get("url"),
                    job_type=job_type,
                    language=langs[0].lower() if langs else detect_language(title),
                    experience_level=infer_experience(title),
                    posted_at=item.get("datedebutdiffusion"),
                ))
            except Exception:
                continue    # one broken record must never break the whole search
        return jobs
