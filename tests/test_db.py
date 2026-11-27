"""DB tests. The vacancy rows below are TEST DATA, not real vacancies."""
import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


def make_job(n=1, **over):
    job = {
        "id": f"test:{n}", "source": "test", "external_id": str(n),
        "title": f"Test job {n}", "company": "TestCo", "city": "Antwerp",
        "region": "flanders", "job_type": "internship", "description": "python and sql",
    }
    job.update(over)
    return job


def test_upsert_and_get(conn):
    db.upsert_job(conn, make_job(1))
    db.upsert_job(conn, make_job(1, title="Renamed"))  # same id -> update, not duplicate
    job = db.get_job(conn, "test:1")
    assert job["title"] == "Renamed"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert db.get_job(conn, "nope") is None


def test_query_filters(conn):
    db.upsert_job(conn, make_job(1))
    db.upsert_job(conn, make_job(2, city="Ghent", job_type="student", description="bar work"))
    assert [j["id"] for j in db.query_jobs(conn, city="ghent")] == ["test:2"]
    assert [j["id"] for j in db.query_jobs(conn, job_type="internship")] == ["test:1"]
    assert [j["id"] for j in db.query_jobs(conn, keywords="python")] == ["test:1"]
    assert len(db.query_jobs(conn)) == 2


def test_cache_roundtrip_and_expiry(conn):
    assert db.cache_get(conn, "k") is None
    db.cache_set(conn, "k", "test", "body")
    assert db.cache_get(conn, "k") == "body"
    assert db.cache_get(conn, "k", max_age_seconds=-1) is None  # too old


def test_budget_counts_calls(conn):
    assert db.budget_used(conn, "adzuna") == 0
    assert db.budget_add(conn, "adzuna") == 1
    assert db.budget_add(conn, "adzuna", 2) == 3
    assert db.budget_used(conn, "other") == 0


def test_profile_keeps_cv_text_when_only_profile_changes(conn):
    assert db.load_profile(conn) is None
    db.save_profile(conn, {"skills": ["python"]}, cv_text="my cv")
    db.save_profile(conn, {"skills": ["python", "sql"]})
    assert db.load_profile(conn)["skills"] == ["python", "sql"]
    assert conn.execute("SELECT cv_text FROM profile").fetchone()[0] == "my cv"


def test_applications_flow(conn):
    db.upsert_job(conn, make_job(1))
    app = db.save_application(conn, "test:1")
    assert app["status"] == "interested"
    assert db.update_application_status(conn, "test:1", "applied")["status"] == "applied"
    assert db.update_application_status(conn, "missing", "applied") is None
    with pytest.raises(ValueError):
        db.update_application_status(conn, "test:1", "hired")
    rows = db.list_applications(conn, status="applied")
    assert rows[0]["title"] == "Test job 1"
    assert db.list_applications(conn, status="offer") == []


def test_delete_user_data_keeps_public_jobs(conn):
    db.upsert_job(conn, make_job(1))
    db.save_profile(conn, {"skills": []})
    db.save_application(conn, "test:1")
    db.delete_user_data(conn)
    assert db.load_profile(conn) is None
    assert db.list_applications(conn) == []
    assert db.get_job(conn, "test:1") is not None
