"""SQLite storage.

One small file database holds everything: vacancies, HTTP cache, the user's profile,
applications, saved searches and the Adzuna call budget. We use plain `sqlite3`
(no ORM) so every query is easy to read and explain.

Usage:
    with connect() as conn:
        upsert_job(conn, {...})
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings

APPLICATION_STATUSES = ("interested", "applied", "interview", "offer", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,      -- "<source>:<external_id>"
    source           TEXT NOT NULL,         -- e.g. "jobat", "adzuna"
    external_id      TEXT NOT NULL,
    title            TEXT NOT NULL,
    company          TEXT,
    location         TEXT,
    city             TEXT,
    region           TEXT,                  -- flanders | wallonia | brussels
    lat              REAL,
    lon              REAL,
    description      TEXT,
    url              TEXT,
    job_type         TEXT,                  -- student | part-time | internship | full-time
    language         TEXT,                  -- nl | fr | en
    experience_level TEXT,                  -- none | junior | medior | senior
    salary_min       REAL,
    salary_max       REAL,
    posted_at        TEXT,
    fetched_at       TEXT NOT NULL,
    is_snapshot      INTEGER NOT NULL DEFAULT 0   -- 1 = loaded from a saved real snapshot
);
CREATE INDEX IF NOT EXISTS idx_jobs_city ON jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);

-- Raw HTTP responses, so we never ask the same thing twice (rate limits!).
CREATE TABLE IF NOT EXISTS http_cache (
    cache_key   TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    body        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

-- Exactly one row (id = 1): the structured profile extracted from the CV.
CREATE TABLE IF NOT EXISTS profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    data        TEXT NOT NULL,              -- JSON
    cv_text     TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'interested',
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Watchlist: saved searches, and which vacancies each one has already reported.
CREATE TABLE IF NOT EXISTS searches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    params          TEXT NOT NULL,          -- JSON of search_jobs arguments
    created_at      TEXT NOT NULL,
    last_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS seen_jobs (
    search_id   INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    job_id      TEXT NOT NULL,
    PRIMARY KEY (search_id, job_id)
);

-- Text embeddings (vectors), cached so the same text is never embedded twice.
CREATE TABLE IF NOT EXISTS embeddings (
    cache_key   TEXT PRIMARY KEY,           -- sha256 of model + text
    vector      BLOB NOT NULL,              -- float32 numbers
    created_at  TEXT NOT NULL
);

-- How many paid/limited API calls we made per month (Adzuna free plan: 25/month).
CREATE TABLE IF NOT EXISTS api_budget (
    source  TEXT NOT NULL,
    month   TEXT NOT NULL,                  -- "YYYY-MM"
    calls   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, month)
);
"""

JOB_COLUMNS = (
    "id", "source", "external_id", "title", "company", "location", "city", "region",
    "lat", "lon", "description", "url", "job_type", "language", "experience_level",
    "salary_min", "salary_max", "posted_at", "fetched_at", "is_snapshot",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open the database (creating tables if needed); commit on success, close always.

    A fresh connection per call keeps things safe when FastAPI uses several threads.
    """
    conn = _open(path or get_settings().db_file)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- jobs
def upsert_job(conn: sqlite3.Connection, job: dict[str, Any]) -> None:
    """Insert a vacancy or update it if we already know it (same id)."""
    row = {c: job.get(c) for c in JOB_COLUMNS}
    row["fetched_at"] = row["fetched_at"] or now_iso()
    row["is_snapshot"] = int(bool(row["is_snapshot"]))
    cols = ", ".join(JOB_COLUMNS)
    marks = ", ".join(f":{c}" for c in JOB_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in JOB_COLUMNS if c != "id")
    conn.execute(
        f"INSERT INTO jobs ({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}",
        row,
    )


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def query_jobs(
    conn: sqlite3.Connection,
    keywords: str | None = None,
    job_type: str | None = None,
    region: str | None = None,
    city: str | None = None,
    language: str | None = None,
    experience_level: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Filter vacancies stored locally (no network, no LLM)."""
    where, args = [], []
    for word in (keywords or "").split():
        where.append("(title LIKE ? OR description LIKE ? OR company LIKE ?)")
        args += [f"%{word}%"] * 3
    for column, value in (
        ("job_type", job_type), ("region", region), ("language", language),
        ("experience_level", experience_level),
    ):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    if city:
        where.append("(city LIKE ? OR location LIKE ?)")
        args += [f"%{city}%"] * 2
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY posted_at DESC, fetched_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 200)))
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# --------------------------------------------------------------------------- cache
def cache_get(conn: sqlite3.Connection, key: str, max_age_seconds: float | None = None) -> str | None:
    """Return a cached response body, or None if missing / older than max_age_seconds."""
    row = conn.execute("SELECT body, fetched_at FROM http_cache WHERE cache_key = ?", (key,)).fetchone()
    if not row:
        return None
    if max_age_seconds is not None:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
        if age > max_age_seconds:
            return None
    return row["body"]


def cache_set(conn: sqlite3.Connection, key: str, source: str, body: str) -> None:
    conn.execute(
        "INSERT INTO http_cache (cache_key, source, body, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET body=excluded.body, fetched_at=excluded.fetched_at",
        (key, source, body, now_iso()),
    )


# --------------------------------------------------------------------- embeddings
def embedding_get(conn: sqlite3.Connection, key: str) -> bytes | None:
    row = conn.execute("SELECT vector FROM embeddings WHERE cache_key = ?", (key,)).fetchone()
    return bytes(row["vector"]) if row else None


def embedding_set(conn: sqlite3.Connection, key: str, vector: bytes) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (cache_key, vector, created_at) VALUES (?, ?, ?)",
        (key, vector, now_iso()),
    )


# ------------------------------------------------------------------- API budget
def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def budget_used(conn: sqlite3.Connection, source: str) -> int:
    row = conn.execute(
        "SELECT calls FROM api_budget WHERE source = ? AND month = ?", (source, _month())
    ).fetchone()
    return row["calls"] if row else 0


def budget_add(conn: sqlite3.Connection, source: str, n: int = 1) -> int:
    """Record n API calls for this month and return the new total."""
    conn.execute(
        "INSERT INTO api_budget (source, month, calls) VALUES (?, ?, ?) "
        "ON CONFLICT(source, month) DO UPDATE SET calls = calls + excluded.calls",
        (source, _month(), n),
    )
    return budget_used(conn, source)


# ---------------------------------------------------------------------- profile
def save_profile(conn: sqlite3.Connection, data: dict[str, Any], cv_text: str | None = None) -> None:
    if cv_text is None:  # keep the previously stored CV text when only the profile is edited
        old = conn.execute("SELECT cv_text FROM profile WHERE id = 1").fetchone()
        cv_text = old["cv_text"] if old else None
    conn.execute(
        "INSERT INTO profile (id, data, cv_text, updated_at) VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET data=excluded.data, cv_text=excluded.cv_text, "
        "updated_at=excluded.updated_at",
        (json.dumps(data, ensure_ascii=False), cv_text, now_iso()),
    )


def load_profile(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The structured profile, or None if no CV has been analysed yet."""
    row = conn.execute("SELECT data FROM profile WHERE id = 1").fetchone()
    data = json.loads(row["data"]) if row else None
    return data or None          # an uploaded-but-not-yet-analysed CV stores {}


def save_cv_text(conn: sqlite3.Connection, cv_text: str) -> None:
    """Store the raw CV text (e.g. right after upload) without touching the profile."""
    conn.execute(
        "INSERT INTO profile (id, data, cv_text, updated_at) VALUES (1, '{}', ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET cv_text=excluded.cv_text, updated_at=excluded.updated_at",
        (cv_text, now_iso()),
    )


def load_cv_text(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT cv_text FROM profile WHERE id = 1").fetchone()
    return row["cv_text"] if row and row["cv_text"] else None


# ----------------------------------------------------------------- applications
def save_application(conn: sqlite3.Connection, job_id: str, status: str = "interested",
                     notes: str | None = None) -> dict[str, Any]:
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"status must be one of {', '.join(APPLICATION_STATUSES)}")
    now = now_iso()
    conn.execute(
        "INSERT INTO applications (job_id, status, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
        "status=excluded.status, notes=COALESCE(excluded.notes, notes), updated_at=excluded.updated_at",
        (job_id, status, notes, now, now),
    )
    return dict(conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone())


def update_application_status(conn: sqlite3.Connection, job_id: str, status: str,
                              notes: str | None = None) -> dict[str, Any] | None:
    """Change the status of an existing application; returns None if it does not exist."""
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"status must be one of {', '.join(APPLICATION_STATUSES)}")
    cur = conn.execute(
        "UPDATE applications SET status = ?, notes = COALESCE(?, notes), updated_at = ? WHERE job_id = ?",
        (status, notes, now_iso(), job_id),
    )
    if cur.rowcount == 0:
        return None
    return dict(conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone())


def list_applications(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    """Applications joined with job title/company, newest first."""
    sql = (
        "SELECT a.*, j.title, j.company, j.city, j.url FROM applications a "
        "LEFT JOIN jobs j ON j.id = a.job_id"
    )
    args: list[Any] = []
    if status:
        sql += " WHERE a.status = ?"
        args.append(status)
    sql += " ORDER BY a.updated_at DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# --------------------------------------------------------------------- privacy
def delete_user_data(conn: sqlite3.Connection) -> None:
    """GDPR 'delete my data': remove profile, applications and saved searches.

    Vacancies and the HTTP cache are public data, so they stay.
    """
    for table in ("profile", "applications", "seen_jobs", "searches"):
        conn.execute(f"DELETE FROM {table}")
