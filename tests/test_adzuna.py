"""Adzuna provider tests. No real network and no real Adzuna calls (budget is precious).

tests/fixtures/adzuna_be_real_sample.json is a trimmed REAL Adzuna response
(fetched 2026-11-27); the HTTP layer is replaced by httpx.MockTransport.
"""
import json
from pathlib import Path

import httpx
import pytest

from app import db
from app.config import Settings
from app.providers.adzuna import AdzunaProvider
from app.providers.base import BudgetExhausted, JobQuery, ProviderError

FIXTURE = Path(__file__).parent / "fixtures" / "adzuna_be_real_sample.json"
SECRET = "TEST-SECRET-KEY"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_provider(tmp_path, handler, budget=25, keys=True):
    settings = Settings(
        adzuna_app_id="id123" if keys else "", adzuna_app_key=SECRET if keys else "",
        adzuna_monthly_budget=budget,
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AdzunaProvider(settings, client=client, db_path=tmp_path / "t.db")


def ok_handler(raw, calls):
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=raw)
    return handler


def test_parse_real_response(tmp_path, raw):
    p = make_provider(tmp_path, ok_handler(raw, []))
    jobs = p.parse(raw)
    assert len(jobs) == 12
    assert all(j.id.startswith("adzuna:") and j.title for j in jobs)
    # Adverts with no location: region and city stay unknown, never guessed.
    nowhere = [j for j in jobs if j.city is None]
    assert nowhere and all(j.region is None for j in nowhere)
    assert any(j.region == "flanders" for j in jobs)
    # "Dringend studenten gezocht" used to be misclassified as full-time.
    urgent = [j for j in jobs if j.title.startswith("Dringend studenten")]
    assert urgent and urgent[0].job_type == "student"


def test_search_calls_once_then_uses_cache(tmp_path, raw):
    calls = []
    p = make_provider(tmp_path, ok_handler(raw, calls))
    q = JobQuery(keywords="student", limit=50)
    first = p.search(q)
    second = p.search(q)
    assert len(calls) == 1                    # second search came from the cache
    assert [j.id for j in first] == [j.id for j in second]
    with db.connect(tmp_path / "t.db") as conn:
        assert db.budget_used(conn, "adzuna") == 1
        # the secret must never be stored in the cache
        assert SECRET not in "".join(r["cache_key"] + r["body"] for r in conn.execute("SELECT * FROM http_cache"))


def test_request_carries_keys_and_keywords_only(tmp_path, raw):
    calls = []
    p = make_provider(tmp_path, ok_handler(raw, calls))
    p.search(JobQuery(keywords="barista", city="Gent", radius_km=10, job_type="part-time"))
    params = dict(calls[0].url.params)
    assert params["what"] == "barista" and params["where"] == "Gent"
    assert params["distance"] == "10" and params["part_time"] == "1"
    assert params["app_key"] == SECRET


def test_budget_exhausted_blocks_network(tmp_path, raw):
    calls = []
    p = make_provider(tmp_path, ok_handler(raw, calls), budget=1)
    p.search(JobQuery(keywords="a"))
    with pytest.raises(BudgetExhausted):
        p.search(JobQuery(keywords="b"))      # different query -> not cached -> blocked
    assert len(calls) == 1


def test_cached_query_still_works_when_budget_is_gone(tmp_path, raw):
    p = make_provider(tmp_path, ok_handler(raw, []), budget=1)
    p.search(JobQuery(keywords="a"))
    assert p.available()[0] is False
    assert p.search(JobQuery(keywords="a"))   # served from cache, no error


def test_missing_keys(tmp_path, raw):
    p = make_provider(tmp_path, ok_handler(raw, []), keys=False)
    assert p.available()[0] is False
    with pytest.raises(ProviderError):
        p.search(JobQuery(keywords="a"))


@pytest.mark.parametrize("status,text", [(401, "rejected our keys"), (429, "rate limit"), (500, "HTTP 500")])
def test_http_errors_are_human_readable(tmp_path, status, text):
    p = make_provider(tmp_path, lambda r: httpx.Response(status))
    with pytest.raises(ProviderError, match=text):
        p.search(JobQuery(keywords="a"))
    with db.connect(tmp_path / "t.db") as conn:
        assert db.budget_used(conn, "adzuna") == 1     # the server answered -> counted


def test_network_error_does_not_leak_key(tmp_path):
    def boom(request):
        raise httpx.ConnectError("failed for " + str(request.url))
    p = make_provider(tmp_path, boom)
    with pytest.raises(ProviderError) as err:
        p.search(JobQuery(keywords="a"))
    assert SECRET not in str(err.value)
    with db.connect(tmp_path / "t.db") as conn:
        assert db.budget_used(conn, "adzuna") == 0     # no answer -> not counted


def test_region_filter_excludes_unknown_locations(tmp_path, raw):
    p = make_provider(tmp_path, ok_handler(raw, []))
    jobs = p.search(JobQuery(keywords="student", region="flanders", limit=50))
    assert jobs and all(j.region == "flanders" for j in jobs)
    assert p.search(JobQuery(keywords="student", region="wallonia", limit=50)) == []
