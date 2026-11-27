"""Local copy of the Le Forem dataset. Records come from the REAL snapshot (108 offers, 2026-11-27)."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app import db
from app.providers import forem_mirror
from app.providers.aggregator import search_all
from app.providers.base import JobQuery, ProviderError
from app.providers.forem import ForemProvider
from app.providers.ratelimit import RateLimiter
from app.providers.snapshot import SnapshotProvider

SNAPSHOT = json.loads((Path(__file__).parent.parent / "data" / "snapshots" / "forem_2026-11-27.json")
                      .read_text(encoding="utf-8"))["results"]
TODAY = date(2026, 11, 27)


@pytest.fixture(autouse=True)
def small_dataset_is_fine(monkeypatch):
    monkeypatch.setattr(forem_mirror, "MIN_PLAUSIBLE_OFFERS", 10)       # our test dataset has 108 offers, not 25 000


@pytest.fixture
def dbp(tmp_path):
    return tmp_path / "m.db"


REAL_CLIENT = httpx.Client          # tests replace httpx.Client; keep the real class for our fakes


def export_client(body=SNAPSHOT, status=200, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body)
    return REAL_CLIENT(transport=httpx.MockTransport(handler))


def filled(dbp):
    forem_mirror.refresh(client=export_client(), db_path=dbp, today=TODAY)
    return ForemProvider(db_path=dbp, today=TODAY, limiter=RateLimiter(0))


# ------------------------------------------------------------------- downloading
def test_refresh_stores_everything_and_reports_state(dbp):
    assert forem_mirror.refresh(client=export_client(), db_path=dbp, today=TODAY) == len(SNAPSHOT)
    info = forem_mirror.info(dbp)
    assert info["offers"] == len(SNAPSHOT) and info["age_hours"] < 0.1


def test_refresh_asks_only_for_current_offers_in_one_request(dbp):
    calls = []
    forem_mirror.refresh(client=export_client(calls=calls), db_path=dbp, today=TODAY)
    assert len(calls) == 1 and "exports/json" in str(calls[0].url)
    assert "datefindiffusion" in calls[0].url.params["where"] and "2026-11-27" in calls[0].url.params["where"]


@pytest.mark.parametrize("client_args,message", [
    ({"status": 429, "body": {"error": "quota"}}, "daily request limit"),
    ({"status": 500, "body": {}}, "HTTP 500"),
    ({"body": []}, "unexpectedly small"),
    ({"body": {"error": "x"}}, "unexpectedly small"),
])
def test_failed_refresh_keeps_the_old_copy(dbp, client_args, message):
    provider = filled(dbp)
    before = forem_mirror.info(dbp)["offers"]
    with pytest.raises(ProviderError, match=message):
        forem_mirror.refresh(client=export_client(**client_args), db_path=dbp, today=TODAY)
    assert forem_mirror.info(dbp)["offers"] == before
    assert provider.search(JobQuery(job_type="student", limit=3))             # still searchable


def test_network_error_and_non_json(dbp):
    def boom(request):
        raise httpx.ConnectError("offline")
    with pytest.raises(ProviderError, match="not reachable"):
        forem_mirror.refresh(client=httpx.Client(transport=httpx.MockTransport(boom)), db_path=dbp)
    html = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>nope</html>")))
    with pytest.raises(ProviderError, match="not JSON"):
        forem_mirror.refresh(client=html, db_path=dbp)


def test_refresh_replaces_instead_of_adding(dbp):
    filled(dbp)
    forem_mirror.refresh(client=export_client(SNAPSHOT[:20]), db_path=dbp, today=TODAY)
    assert forem_mirror.info(dbp)["offers"] == 20
    with db.connect(dbp) as conn:
        assert conn.execute("SELECT COUNT(*) FROM forem_offers").fetchone()[0] == 20


def test_refresh_if_stale(dbp, monkeypatch):
    calls = []
    monkeypatch.setattr(forem_mirror.httpx, "Client", lambda **kw: export_client(calls=calls))
    assert "refreshed: 108" in forem_mirror.refresh_if_stale(db_path=dbp)             # nothing yet -> download
    assert "is fresh" in forem_mirror.refresh_if_stale(db_path=dbp) and len(calls) == 1   # fresh -> no request
    with db.connect(dbp) as conn:
        conn.execute("UPDATE mirror_state SET refreshed_at = ?", ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),))
    forem_mirror.refresh_if_stale(db_path=dbp)
    assert len(calls) == 2                                                             # 7 h old -> download again


def test_refresh_if_stale_never_raises(dbp, monkeypatch):
    monkeypatch.setattr(forem_mirror.httpx, "Client", lambda **kw: export_client(status=429, body={}))
    assert "NOT refreshed" in forem_mirror.refresh_if_stale(db_path=dbp)
    monkeypatch.setattr(forem_mirror, "refresh", lambda **kw: 1 / 0)
    assert "NOT refreshed" in forem_mirror.refresh_if_stale(db_path=dbp)


# ------------------------------------------------------------------ local search
def test_provider_uses_the_local_copy_and_not_the_network(dbp):
    provider = filled(dbp)
    calls = []
    provider._client = export_client(calls=calls)           # would be used by the live path
    jobs = provider.search(JobQuery(job_type="student", region="brussels", limit=10))
    assert jobs and calls == []
    assert all(j.region == "brussels" and j.job_type == "student" and j.source == "forem" for j in jobs)


def test_without_a_copy_the_provider_still_uses_the_live_portal(dbp):
    calls = []
    provider = ForemProvider(client=export_client({"results": SNAPSHOT[:5]}, calls=calls), db_path=dbp,
                             today=TODAY, limiter=RateLimiter(0))
    assert provider.search(JobQuery(limit=3)) and len(calls) == 1


def test_keywords_match_word_starts_in_title_or_occupation(dbp):
    provider = filled(dbp)
    found = provider.search(JobQuery(keywords="serveur", limit=20))
    assert found and all("serveur" in (j.title + (j.description or "")).lower() or "serveuse" in j.title.lower()
                         or "serveur" in j.title.lower() for j in found)
    assert provider.search(JobQuery(keywords="erveur")) == []          # middle of a word does not count
    assert provider.search(JobQuery(keywords="serveur zzzzqq")) == []   # all words must match


def test_accents_and_case_do_not_matter(dbp):
    provider = filled(dbp)
    with_accent = provider.search(JobQuery(keywords="ÉTUDIANT", limit=50))
    without = provider.search(JobQuery(keywords="etudiant", limit=50))
    assert with_accent and [j.id for j in with_accent] == [j.id for j in without]


def test_city_names_in_any_language(dbp):
    provider = filled(dbp)
    liege = provider.search(JobQuery(city="Liège", limit=50))
    luik = provider.search(JobQuery(city="Luik", limit=50))
    assert liege and [j.id for j in liege] == [j.id for j in luik]


def test_filters_region_type_language(dbp):
    provider = filled(dbp)
    assert all(j.region == "flanders" for j in provider.search(JobQuery(region="flanders", limit=50)))
    assert all(j.job_type == "internship" for j in provider.search(JobQuery(job_type="internship", limit=50)))
    assert all(j.job_type == "part-time" for j in provider.search(JobQuery(job_type="part-time", limit=50)))
    assert all(j.language == "nl" for j in provider.search(JobQuery(language="nl", limit=50)))


def test_expired_offers_are_never_returned(dbp):
    provider = filled(dbp)
    assert provider.search(JobQuery(limit=5))
    provider._today = date(2030, 1, 1)
    assert provider.search(JobQuery(limit=5)) == []


def test_newest_first_and_limit(dbp):
    jobs = filled(dbp).search(JobQuery(limit=7))
    assert len(jobs) == 7 and [j.posted_at for j in jobs] == sorted((j.posted_at for j in jobs), reverse=True)


def test_search_text_cannot_act_as_a_sql_pattern(dbp):
    provider = filled(dbp)
    for text in ("%", "_", "100%", "a' OR '1'='1", "\\", "x" * 100):
        assert isinstance(provider.search(JobQuery(keywords=text, city=text)), list)   # no crash, no injection


# ----------------------------------------------------------- freshness labelling
def test_old_copy_is_labelled_cached_with_a_note(dbp):
    filled(dbp)
    with db.connect(dbp) as conn:
        conn.execute("UPDATE mirror_state SET refreshed_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),))
    out = search_all(JobQuery(limit=3), ForemProvider(db_path=dbp, today=TODAY), db_path=dbp,
                     adzuna=type("A", (), {"settings": type("S", (), {"adzuna_configured": False})()})(),
                     snapshot=SnapshotProvider())
    assert out.jobs and out.data_source == "cached" and any("30 hours ago" in n for n in out.notes)


def test_fresh_copy_is_labelled_live(dbp):
    filled(dbp)
    out = search_all(JobQuery(limit=3), ForemProvider(db_path=dbp, today=TODAY), db_path=dbp,
                     adzuna=type("A", (), {"settings": type("S", (), {"adzuna_configured": False})()})())
    assert out.jobs and out.data_source == "live" and out.notes == []


# ------------------------------------------------------------------- background
def test_background_refresher_runs_once_and_stops(monkeypatch):
    seen = []
    monkeypatch.setattr(forem_mirror, "refresh_if_stale", lambda *a, **k: seen.append(1) or "ok")
    refresher = forem_mirror.BackgroundRefresher(every_hours=1)
    refresher.start()
    refresher.stop()
    refresher._thread.join(timeout=3)
    assert seen == [1] and refresher.last_message == "ok" and not refresher._thread.is_alive()
