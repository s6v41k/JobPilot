"""How well does a vacancy fit a profile? (score 0-100)

Three simple, explainable signals - no LLM call, so it is fast and always gives the same
answer for the same input:

  1. meaning   - cosine similarity of bge-m3 embeddings (profile text vs vacancy text)
  2. skills    - which skills the vacancy mentions, and which of them the CV also shows
  3. language  - is the vacancy written in a language the person speaks? (key in Belgium)

If a signal is unknown (no embeddings, no recognisable skills, no languages in the CV),
it is left out and the others are re-weighted, so the score never pretends to know more.
"""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from app import db, llm
from app.skills import find_skills, language_levels

WEIGHTS = {"meaning": 0.5, "skills": 0.3, "language": 0.2}
# Measured on 108 real vacancies: bge-m3 cosines of a CV vs a vacancy run from ~0.42 (unrelated)
# to ~0.69 (clearly related). Stretch 0.40..0.70 to 0..100.
COS_LOW, COS_HIGH = 0.40, 0.70
ONE_SIGNAL_CAP = 65     # with a single known signal we never claim a 'strong' fit
LANGUAGE_NAMES = {"nl": "Dutch", "fr": "French", "en": "English"}


# ----------------------------------------------------------------- texts
def profile_text(profile: dict[str, Any]) -> str:
    parts = [profile.get("summary") or ""]
    for key in ("education", "experience", "skills", "interests"):
        parts.append(", ".join(profile.get(key) or []))
    return " | ".join(p for p in parts if p)[:1500]


def job_text(job: dict[str, Any]) -> str:
    return f"{job.get('title') or ''}. {job.get('company') or ''}. {job.get('description') or ''}"[:1500]


# ------------------------------------------------------------ embeddings
def _key(text: str) -> str:
    model = llm.get_settings().embed_model
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()


def embed_cached(texts: list[str]) -> list[np.ndarray]:
    """Vectors for the texts; only texts we never saw go to the embedding model."""
    keys = [_key(t) for t in texts]
    vectors: dict[str, np.ndarray] = {}
    with db.connect() as conn:
        for k in set(keys):
            blob = db.embedding_get(conn, k)
            if blob is not None:
                vectors[k] = np.frombuffer(blob, dtype=np.float32)
    missing = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in vectors]
    if missing:
        fresh = llm.embed([t for _, t in missing])
        with db.connect() as conn:
            for (k, _), vec in zip(missing, fresh):
                arr = np.asarray(vec, dtype=np.float32)
                vectors[k] = arr
                db.embedding_set(conn, k, arr.tobytes())
    return [vectors[k] for k in keys]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0


def meaning_score(similarity: float) -> float:
    """Cosine similarity -> 0..100."""
    return max(0.0, min(100.0, (similarity - COS_LOW) / (COS_HIGH - COS_LOW) * 100))


# ---------------------------------------------------------------- scoring
def language_fit(job_language: str | None, profile: dict[str, Any]) -> tuple[float | None, str | None]:
    """(score or None, readable note) for the vacancy language vs the languages in the CV."""
    levels = language_levels(profile.get("languages") or [])
    if not job_language or not levels:
        return None, None
    name = LANGUAGE_NAMES.get(job_language, job_language)
    level = levels.get(job_language, 0)
    if level >= 3:
        return 100.0, f"the ad is in {name}, which you speak well"
    if level == 2:
        return 70.0, f"the ad is in {name}, which you speak at intermediate level"
    if level == 1:
        return 35.0, f"the ad is in {name}, and your level looks basic"
    return 0.0, f"the ad is in {name}, which is not in your CV"


def label_for(score: float) -> str:
    return "strong" if score >= 75 else "good" if score >= 55 else "partial" if score >= 35 else "weak"


def score_job(job: dict[str, Any], profile: dict[str, Any], similarity: float | None) -> dict[str, Any]:
    """Combine the signals into one result dict (pure function: easy to test)."""
    job_skills = find_skills(job_text(job))
    have = find_skills(profile_text(profile) + " " + " ".join(profile.get("skills") or []))
    matched, missing = sorted(job_skills & have), sorted(job_skills - have)

    parts: dict[str, float | None] = {
        "meaning": meaning_score(similarity) if similarity is not None else None,
        "skills": 100.0 * len(matched) / len(job_skills) if job_skills else None,
        "language": None,
    }
    parts["language"], lang_note = language_fit(job.get("language"), profile)
    known = {k: v for k, v in parts.items() if v is not None}
    total = sum(WEIGHTS[k] for k in known)
    score = round(sum(WEIGHTS[k] * v for k, v in known.items()) / total) if total else 0
    if len(known) == 1:
        score = min(score, ONE_SIGNAL_CAP)

    notes = []
    if matched:
        notes.append(f"it asks for {len(job_skills)} skill(s) and your CV shows {len(matched)} ({', '.join(matched[:3])})")
    elif job_skills:
        notes.append(f"it mentions skills your CV does not show ({', '.join(missing[:3])})")
    if lang_note:
        notes.append(lang_note)
    if similarity is None:
        notes.append("the text-similarity check was unavailable")
    if len(known) <= 1:
        notes.append("there is little information in the ad to compare")
    reason = (f"{label_for(score).capitalize()} fit: " + "; ".join(notes) + "." if notes
              else f"{label_for(score).capitalize()} fit.")
    return {
        "job_id": job["id"], "title": job.get("title"), "company": job.get("company"), "city": job.get("city"),
        "score": score, "label": label_for(score), "matched_skills": matched, "missing_skills": missing,
        "reason": reason,
        "components": {k: (round(v) if v is not None else None) for k, v in parts.items()},
    }


def match_jobs(jobs: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Score several vacancies against one profile, best first."""
    similarities: list[float | None] = [None] * len(jobs)
    try:
        vectors = embed_cached([profile_text(profile)] + [job_text(j) for j in jobs])
        similarities = [cosine(vectors[0], v) for v in vectors[1:]]
    except llm.LLMError:
        pass            # no embeddings: skills + language still give a (clearly noted) score
    results = [score_job(j, profile, s) for j, s in zip(jobs, similarities)]
    return sorted(results, key=lambda r: r["score"], reverse=True)
