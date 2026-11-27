"""Skills vocabulary, scoring and the get_job_details / match_cv tools. Job rows are TEST DATA."""
import numpy as np
import pytest

from app import db, llm, matching
from app.config import get_settings
from app.llm import LLMError
from app.skills import find_skills, language_levels
from app.tools import call_tool


# ----------------------------------------------------------------- skills.py
@pytest.mark.parametrize("text,expected", [
    ("Wij zoeken een kassamedewerker met klantvriendelijkheid", {"cash handling"}),
    ("Service client et caisse, temps partiel", {"customer service", "cash handling"}),
    ("We need Python and SQL, Git is a plus", {"python", "sql", "git"}),
    ("Experience with JavaScript", {"javascript"}),            # 'java' must NOT match inside 'javascript'
    ("Serveuse dans une brasserie", {"hospitality"}),
    ("Nothing relevant here", set()),
    ("", set()),
])
def test_find_skills_across_languages(text, expected):
    assert find_skills(text) == expected


def test_java_alone_is_java():
    assert find_skills("Java developer") == {"java"}


@pytest.mark.parametrize("languages,expected", [
    (["Dutch (native)", "English (fluent)", "French (intermediate)"], {"nl": 3, "en": 3, "fr": 2}),
    (["Nederlands - moedertaal", "Frans: basis"], {"nl": 3, "fr": 1}),
    (["Anglais courant", "Néerlandais"], {"en": 3, "nl": 2}),      # no level written -> intermediate
    (["Klingon"], {}),
    ([], {}),
])
def test_language_levels(languages, expected):
    assert language_levels(languages) == expected


# ------------------------------------------------------------------- scoring
PROFILE = {"summary": "Business student", "skills": ["Customer service", "Cash register", "Excel"],
           "experience": ["Waitress at a brasserie"], "education": [],
           "languages": ["Dutch (native)", "English (fluent)", "French (basic)"]}


def job(n=1, **over):
    data = {"id": f"t:{n}", "title": "Kassamedewerker", "company": "TestCo", "city": "Gent",
            "description": "Klantendienst en kassa. Excel is een plus. Ervaring met Python.", "language": "nl"}
    data.update(over)
    return data


def test_score_combines_meaning_skills_and_language():
    r = matching.score_job(job(), PROFILE, similarity=0.70)
    assert r["matched_skills"] == ["cash handling", "customer service", "excel"]
    assert r["missing_skills"] == ["python"]
    assert r["components"] == {"meaning": 100, "skills": 75, "language": 100}
    assert r["score"] == round(0.5 * 100 + 0.3 * 75 + 0.2 * 100) and r["label"] == "strong"
    assert "Dutch" in r["reason"] and r["reason"].startswith("Strong fit")


def test_language_the_cv_lacks_lowers_the_score():
    good = matching.score_job(job(), PROFILE, 0.6)["score"]
    bad = matching.score_job(job(language="fr"), PROFILE, 0.6)        # French is only 'basic'
    worse = matching.score_job(job(language="fr"), {**PROFILE, "languages": ["Dutch (native)"]}, 0.6)
    assert good > bad["score"] > worse["score"]
    assert "not in your CV" in worse["reason"]


def test_missing_signals_are_left_out_not_zero():
    r = matching.score_job(job(description="", title="Zzz", language=None), PROFILE, similarity=0.55)
    assert r["components"]["skills"] is None and r["components"]["language"] is None
    assert r["score"] <= matching.ONE_SIGNAL_CAP and "little information" in r["reason"]


def test_no_embedding_still_scores():
    r = matching.score_job(job(), PROFILE, similarity=None)
    assert r["components"]["meaning"] is None and 0 < r["score"] <= 100
    assert "unavailable" in r["reason"]


def test_meaning_score_scale():
    assert matching.meaning_score(0.2) == 0 and matching.meaning_score(0.9) == 100
    assert matching.meaning_score(0.55) == pytest.approx(50)


# ------------------------------------------------------------------- tools
@pytest.fixture
def home(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    return tmp_path


@pytest.fixture
def fake_embed(monkeypatch):
    """Deterministic 'embeddings': a bag of letters, so similar texts are close."""
    calls = []

    def embed(texts):
        calls.append(list(texts))
        out = []
        for t in texts:
            v = np.zeros(26)
            for ch in t.lower():
                if "a" <= ch <= "z":
                    v[ord(ch) - 97] += 1
            out.append(v.tolist())
        return out
    monkeypatch.setattr(llm, "embed", embed)
    return calls


def store(*jobs, profile=PROFILE):
    with db.connect() as conn:
        for j in jobs:
            db.upsert_job(conn, {"source": "t", "external_id": j["id"], **j})
        if profile:
            db.save_profile(conn, profile)


def test_get_job_details(home):
    store(job(1, source="forem", url="https://x/1", is_snapshot=1), profile=None)
    res = call_tool("get_job_details", {"job_id": " t:1 "})
    assert res["ok"] and res["data"]["title"] == "Kassamedewerker" and res["data"]["outdated_snapshot"] is True
    assert call_tool("get_job_details", {"job_id": "nope"})["error"].startswith("No vacancy with id 'nope'")
    assert not call_tool("get_job_details", {})["ok"]


def test_get_job_details_explains_source_limits(home):
    with db.connect() as conn:
        db.upsert_job(conn, {"id": "forem:9", "source": "forem", "external_id": "9", "title": "X"})
    assert "no free text" in call_tool("get_job_details", {"job_id": "forem:9"})["data"]["note"]


def test_match_cv_needs_a_profile(home):
    store(job(1), profile=None)
    res = call_tool("match_cv", {"job_ids": ["t:1"]})
    assert not res["ok"] and "upload their CV" in res["error"]


def test_match_cv_sorts_best_first_and_reports_unknown_ids(home, fake_embed):
    store(job(1, title="Kassamedewerker"), job(2, title="Elektricien", description="Chantier, installations",
                                                language="fr"))
    res = call_tool("match_cv", {"job_ids": ["t:2", "t:1", "t:404"]})
    assert res["ok"] and res["data"]["unknown_ids"] == ["t:404"]
    scores = [m["score"] for m in res["data"]["matches"]]
    assert scores == sorted(scores, reverse=True) and res["data"]["matches"][0]["job_id"] == "t:1"


@pytest.mark.parametrize("value", ["t:1", "t:1, t:2", ["t:1", "t:1"]])
def test_match_cv_accepts_sloppy_id_formats(home, fake_embed, value):
    store(job(1), job(2))
    assert call_tool("match_cv", {"job_ids": value})["ok"]


def test_match_cv_rejects_too_many_and_all_unknown(home, fake_embed):
    store(job(1))
    assert not call_tool("match_cv", {"job_ids": [f"t:{i}" for i in range(9)]})["ok"]
    res = call_tool("match_cv", {"job_ids": ["t:404"]})
    assert not res["ok"] and "Unknown vacancy id" in res["error"]


def test_embeddings_are_cached(home, fake_embed):
    store(job(1))
    call_tool("match_cv", {"job_ids": ["t:1"]})
    call_tool("match_cv", {"job_ids": ["t:1"]})
    assert len(fake_embed) == 1                                   # second time: nothing new to embed


def test_match_cv_survives_a_dead_embedding_model(home, monkeypatch):
    def broken(texts):
        raise LLMError("Cannot reach Ollama.")
    monkeypatch.setattr(llm, "embed", broken)
    store(job(1))
    res = call_tool("match_cv", {"job_ids": ["t:1"]})
    assert res["ok"] and "unavailable" in res["data"]["matches"][0]["reason"]
