"""Aggregator tests with fake providers. Job rows are TEST DATA, not real vacancies."""
import pytest

from app import db
from app.providers.aggregator import search_all
from app.providers.base import Job, JobProvider, JobQuery, ProviderError
from app.providers.keywords import expand_keywords
from app.providers.snapshot import SnapshotProvider


def job(n, **over):
    data = dict(id=f"fake:{n}", source="fake", external_id=str(n), title=f"Test job {n}",
                company=f"Co{n}", city="Gent", region="flanders", job_type="student")
    data.update(over)
    return Job(**data)


class Fake(JobProvider):
    def __init__(self, jobs=None, error=None, name="fake"):
        self.name, self._jobs, self._error, self.queries = name, jobs or [], error, []
        self.settings = type("S", (), {"adzuna_configured": True})()

    def available(self):
        return True, "ok"

    def search(self, query):
        self.queries.append(query)
        if self._error:
            raise ProviderError(self._error)
        return self._jobs


@pytest.fixture
def dbp(tmp_path):
    return tmp_path / "t.db"


def test_enough_results_means_adzuna_is_not_used(dbp):
    forem, adzuna = Fake([job(i) for i in range(6)]), Fake([job(99)])
    out = search_all(JobQuery(), forem, adzuna, SnapshotProvider(dbp.parent), db_path=dbp)
    assert len(out.jobs) == 6 and adzuna.queries == []
    assert out.data_source == "live"
    with db.connect(dbp) as conn:                       # live results are stored locally
        assert db.get_job(conn, "fake:1") is not None


def test_a_few_results_do_not_spend_the_adzuna_budget(dbp):
    adzuna = Fake([job(9)])
    out = search_all(JobQuery(), Fake([job(1), job(2)]), adzuna, SnapshotProvider(dbp.parent), db_path=dbp)
    assert len(out.jobs) == 2 and adzuna.queries == []


def test_adzuna_is_used_when_forem_finds_nothing_or_fails(dbp):
    adzuna = Fake([job(9)])
    assert [j.id for j in search_all(JobQuery(), Fake([]), adzuna, SnapshotProvider(dbp.parent), db_path=dbp).jobs] == ["fake:9"]
    adzuna = Fake([job(8)])
    out = search_all(JobQuery(), Fake(error="429"), adzuna, SnapshotProvider(dbp.parent), db_path=dbp)
    assert [j.id for j in out.jobs] == ["fake:8"] and "429" in out.notes


def test_results_are_merged_and_deduplicated_when_adzuna_is_allowed(dbp):
    forem = Fake([job(1), job(2)])
    adzuna = Fake([job(2, id="fake:x"), job(3)])        # job 2 again, same title/company/city
    out = search_all(JobQuery(), forem, adzuna, SnapshotProvider(dbp.parent), db_path=dbp, min_results=5)
    assert [j.title for j in out.jobs] == ["Test job 1", "Test job 2", "Test job 3"]
    assert len(adzuna.queries) == 1


def test_adzuna_failure_is_only_a_note(dbp):
    out = search_all(JobQuery(), Fake([]), Fake(error="budget used up"),
                     SnapshotProvider(dbp.parent), db_path=dbp)
    assert out.jobs == [] and out.data_source == "live"       # Forem answered (nothing found): no fallback needed
    assert "budget used up" in out.notes


def test_all_live_sources_down_uses_saved_jobs(dbp):
    with db.connect(dbp) as conn:
        db.upsert_job(conn, job(7, title="Saved barista").to_row())
    out = search_all(JobQuery(keywords="barista"), Fake(error="down"), Fake(error="down"),
                     SnapshotProvider(dbp.parent), db_path=dbp)
    assert out.data_source == "cached" and out.jobs[0].title == "Saved barista"
    assert any("unavailable" in n for n in out.notes)


def test_everything_down_uses_real_snapshot_files(dbp):
    out = search_all(JobQuery(job_type="student", limit=5), Fake(error="down"), Fake(error="down"), db_path=dbp)
    assert out.data_source == "snapshot" and out.jobs
    assert all(j.is_snapshot for j in out.jobs)
    assert out.snapshot_date and out.snapshot_date in " ".join(out.notes)


def test_genuinely_empty_result_does_not_trigger_fallback(dbp):
    out = search_all(JobQuery(keywords="zzzz"), Fake([]), Fake([]), db_path=dbp)
    assert out.jobs == [] and out.data_source == "live"


def test_english_keyword_is_also_searched_in_dutch_and_french(dbp):
    forem = Fake([job(i) for i in range(6)])
    search_all(JobQuery(keywords="waiter"), forem, Fake(), db_path=dbp)
    assert [q.keywords for q in forem.queries] == ["waiter", "ober", "serveur"]


def test_expand_keywords():
    assert expand_keywords("") == [""]
    assert expand_keywords("Barista")[0] == "Barista"
    assert expand_keywords("data analyst") == ["data analyst"]      # phrases stay as typed


def test_brussels_becomes_the_whole_region(dbp):
    forem = Fake([job(i) for i in range(6)])
    search_all(JobQuery(keywords="x", city="Brussels"), forem, Fake(), db_path=dbp)
    assert forem.queries[0].city is None and forem.queries[0].region == "brussels"


def test_snapshot_understands_city_names_and_brussels():
    snap = SnapshotProvider()
    assert snap.search(JobQuery(city="Brussels", limit=50))        # real snapshot has Brussels offers
    assert all(j.region == "brussels" for j in snap.search(JobQuery(city="Brussels", limit=50)))


def test_a_language_filter_that_hides_everything_is_dropped_before_adzuna_is_tried(dbp):
    """qwen3:4b passed language="en" (the user's language) and got 0 results: now it retries without it."""
    class LanguageStrict(Fake):
        def search(self, query):
            self.queries.append(query)
            return [] if query.language else self._jobs

    forem, adzuna = LanguageStrict([job(1), job(2)]), Fake([job(9)])
    out = search_all(JobQuery(keywords="student", language="en"), forem, adzuna, db_path=dbp)
    assert [j.id for j in out.jobs] == ["fake:1", "fake:2"] and adzuna.queries == []            # Adzuna budget untouched
    assert any("No vacancies are written in 'en'" in n for n in out.notes)
    assert [q.language for q in forem.queries[-1:]] == [None]


def test_a_language_filter_with_results_is_left_alone(dbp):
    forem = Fake([job(1)])
    out = search_all(JobQuery(language="nl"), forem, Fake(), db_path=dbp)
    assert out.notes == [] and all(q.language == "nl" for q in forem.queries)
