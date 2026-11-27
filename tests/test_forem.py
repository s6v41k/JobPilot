"""Forem provider tests (no network). The fixture is a trimmed REAL ODWB response (2026-11-27)."""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import db
from app.providers.base import JobQuery, ProviderError
from app.providers.forem import ForemProvider
from app.providers.ratelimit import RateLimiter

FIXTURE = Path(__file__).parent / "fixtures" / "forem_real_sample.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ForemProvider(client=client, db_path=tmp_path / "t.db",
                         limiter=RateLimiter(0, sleep=lambda s: None), today=date(2026, 11, 27))


def test_parse_real_records(tmp_path, raw):
    jobs = make(tmp_path, lambda r: httpx.Response(200, json=raw)).parse(raw)
    assert len(jobs) == len(raw["results"])
    assert all(j.id.startswith("forem:") and j.url and "(H/F" not in j.title for j in jobs)
    assert {"student", "internship"} <= {j.job_type for j in jobs}
    assert {"wallonia", "flanders"} <= {j.region for j in jobs}
    assert all("Contract:" in j.description for j in jobs if j.description)


def test_internship_on_student_contract_stays_internship(tmp_path):
    item = {"numerooffreforem": "1", "titreoffre": "Stagiaire en marketing (H/F/X)",
            "typecontrat": "Etudiant", "regimetravail": "Temps plein"}
    job = make(tmp_path, None).parse({"results": [item]})[0]
    assert job.job_type == "internship" and job.title == "Stagiaire en marketing"


def test_student_contract_is_student_job(tmp_path):
    item = {"numerooffreforem": "2", "titreoffre": "Vendeur boulangerie", "typecontrat": "Etudiant"}
    assert make(tmp_path, None).parse({"results": [item]})[0].job_type == "student"


def test_where_clause(tmp_path):
    p = make(tmp_path, None)
    where = p.build_where(JobQuery(keywords="vendeur", city="Namur", region="wallonia",
                                   job_type="internship", language="fr"))
    assert "datefindiffusion >= date'2026-11-27'" in where      # expired offers are hidden
    assert 'search(titreoffre, "vendeur")' in where
    assert 'search(lieuxtravaillocalite, "Namur")' in where
    assert 'search(lieuxtravailregionnuts, "BE3")' in where
    assert 'search(titreoffre, "stage")' in where
    assert 'search(languescodeiso2, "FR")' in where


def test_user_text_cannot_break_the_query(tmp_path):
    where = make(tmp_path, None).build_where(JobQuery(keywords='a" or 1=1 --', city='x"y'))
    assert '""' not in where and 'x"y' not in where


def test_search_uses_cache_second_time(tmp_path, raw):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=raw)

    p = make(tmp_path, handler)
    q = JobQuery(job_type="student", limit=5)
    assert p.search(q) == p.search(q)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500])
def test_http_errors(tmp_path, status):
    with pytest.raises(ProviderError):
        make(tmp_path, lambda r: httpx.Response(status)).search(JobQuery())


def test_network_error(tmp_path):
    def boom(request):
        raise httpx.ConnectError("down")
    with pytest.raises(ProviderError, match="not reachable"):
        make(tmp_path, boom).search(JobQuery())


def test_rate_limiter_waits_between_calls_to_same_domain():
    now, slept = [0.0], []
    limiter = RateLimiter(2.5, clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    assert limiter.wait("a.be") == 0
    now[0] += 1.0                       # only 1 s passed -> must wait 1.5 s more
    assert limiter.wait("a.be") == pytest.approx(1.5)
    assert limiter.wait("b.be") == 0    # other domain is independent
    assert slept == [pytest.approx(1.5)]
