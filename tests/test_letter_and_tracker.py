"""draft_cover_letter and the application tracker. Job rows and the LLM are TEST doubles."""
from types import SimpleNamespace

import pytest

from app import db, llm
from app.config import get_settings
from app.tools import call_tool
from app.tools.cover_letter import choose_language, shorten, word_count

PROFILE = {"name": "Emma Verbeke", "summary": "Business student who likes people", "languages":
           ["Dutch (native)", "English (fluent)", "French (basic)"], "education": ["Bachelor Business"],
           "experience": ["Waitress at a brasserie"], "skills": ["Excel"], "interests": []}


@pytest.fixture
def home(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    return tmp_path


def store(profile=PROFILE):
    with db.connect() as conn:
        db.upsert_job(conn, {"id": "t:1", "source": "t", "external_id": "1", "title": "Serveur", "company": "TestCo",
                             "city": "Gent", "language": "nl", "description": "Bediening in een brasserie",
                             "url": "https://example.test/1"})
        db.upsert_job(conn, {"id": "t:2", "source": "t", "external_id": "2", "title": "Vendeur", "language": "fr"})
        if profile:
            db.save_profile(conn, profile)


class FakeChat:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def chat(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        item = self.texts.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(message=SimpleNamespace(content=item))


@pytest.fixture
def fake(monkeypatch):
    def install(*texts):
        client = FakeChat(*texts)
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return client
    return install


def words(n, sentence="word " * 9 + "end."):
    """About n words made of 10-word sentences."""
    return " ".join([sentence] * (n // 10))


# ----------------------------------------------------------------- helpers
def test_shorten_cuts_at_a_sentence_and_keeps_paragraphs():
    text = words(100) + "\n\n" + words(100)
    out = shorten(text, limit=150)
    assert word_count(out) <= 150 and out.endswith("end.") and "\n\n" in out
    assert shorten("Short letter.", 150) == "Short letter."


def test_choose_language():
    nl_job, fr_job = {"language": "nl"}, {"language": "fr"}
    assert choose_language("en", nl_job, PROFILE) == "en"                 # explicit request wins
    assert choose_language(None, nl_job, PROFILE) == "nl"                 # speaks Dutch natively
    assert choose_language(None, fr_job, PROFILE) == "en"                 # French only basic -> English
    assert choose_language(None, {}, PROFILE) == "en"


# -------------------------------------------------------------------- letter
def test_letter_needs_profile_and_known_job(home, fake):
    store(profile=None)
    assert "upload their CV" in call_tool("draft_cover_letter", {"job_id": "t:1"})["error"]
    store()
    assert "No vacancy with id" in call_tool("draft_cover_letter", {"job_id": "nope"})["error"]


def test_letter_is_written_in_the_job_language_with_profile_facts(home, fake):
    client = fake("Beste,\n\nIk wil graag bij TestCo werken.\n\nMet vriendelijke groet,\nEmma Verbeke")
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1", "tone": "professional"})
    assert res["ok"] and res["data"]["language"] == "nl" and res["data"]["job"]["company"] == "TestCo"
    assert res["data"]["word_count"] == word_count(res["data"]["letter"])
    system, user = (m["content"] for m in client.calls[0]["messages"])
    assert "Dutch" in system and "formal" in system and "Do NOT list technologies" in system
    assert "Title: Serveur" in user and "Waitress at a brasserie" in user


def test_too_long_letter_gets_one_retry(home, fake):
    client = fake(words(300), "Short and sweet letter, Emma.")
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1"})
    assert res["data"]["letter"] == "Short and sweet letter, Emma." and len(client.calls) == 2
    assert "300 words" in client.calls[1]["messages"][1]["content"]


def test_letter_still_too_long_after_retry_is_cut_not_failed(home, fake):
    fake(words(300), words(250))
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1"})
    assert res["ok"] and res["data"]["word_count"] <= 175


def test_placeholder_triggers_one_retry_and_leftovers_are_removed(home, fake):
    client = fake("Cher M. [Nom du responsable],\n\nBonjour.\n\nEmma",
                  "Madame, Monsieur [Nom],\n\nMerci beaucoup.\n\nEmma Verbeke")
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1", "language": "fr"})
    assert len(client.calls) == 2 and "square brackets" in client.calls[1]["messages"][1]["content"]
    assert "[" not in res["data"]["letter"] and res["data"]["letter"].startswith("Madame, Monsieur,")


def test_letter_addressed_to_the_candidate_is_corrected(home, fake):
    client = fake("Dear Emma Verbeke,\n\nI am excited.\n\nEmma", "Dear hiring team,\n\nI am excited.\n\nEmma")
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1", "language": "en"})
    assert "greeted the candidate" in client.calls[1]["messages"][1]["content"]
    assert res["data"]["letter"].startswith("Dear hiring team")


def test_invented_university_is_caught_but_real_one_is_fine(home, fake):
    from app.tools.cover_letter import invented_institutions
    facts = "Education: Bachelor Business, Thomas More Hogeschool Mechelen"
    assert invented_institutions("Ik studeer aan de Universite de Liège.", facts) == ["Universite de Liège"]
    assert invented_institutions("I study at Hogeschool Thomas More.", facts) == []
    assert invented_institutions("No school mentioned here.", facts) == []
    client = fake("Madame, Monsieur,\n\nJe suis étudiante à l'Université de Liège.\n\nEmma",
                  "Madame, Monsieur,\n\nJe suis motivée.\n\nEmma")
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1", "language": "fr"})
    assert "Université" not in res["data"]["letter"] and len(client.calls) == 2
    assert "not in the PROFILE" in client.calls[1]["messages"][1]["content"]


def test_english_greeting_in_a_french_letter_is_replaced(home, fake):
    fake("Dear hiring team,\n\nJe suis très motivée.\n\nCordialement,\nEmma")
    store()
    letter = call_tool("draft_cover_letter", {"job_id": "t:1", "language": "fr"})["data"]["letter"]
    assert letter.startswith("Madame, Monsieur,\n\nJe suis très motivée")


def test_clean_letter_needs_no_retry(home, fake):
    client = fake("Dear hiring team,\n\nI would love to work with you.\n\nKind regards,\nEmma Verbeke")
    store()
    assert call_tool("draft_cover_letter", {"job_id": "t:1", "language": "en"})["ok"] and len(client.calls) == 1


def test_letter_reports_llm_problems(home, fake):
    fake(ConnectionError("down"))
    store()
    res = call_tool("draft_cover_letter", {"job_id": "t:1"})
    assert not res["ok"] and "Cannot reach Ollama" in res["error"]


def test_letter_rejects_bad_tone(home, fake):
    store()
    assert not call_tool("draft_cover_letter", {"job_id": "t:1", "tone": "rude"})["ok"]


# ------------------------------------------------------------------- tracker
def test_save_update_list_flow(home):
    store()
    saved = call_tool("save_application", {"job_id": "t:1", "notes": "nice place"})
    assert saved["ok"] and saved["data"]["saved"]["status"] == "interested"
    assert saved["data"]["saved"]["title"] == "Serveur"

    updated = call_tool("update_application_status", {"job_id": "t:1", "status": "applied"})
    assert updated["data"]["updated"]["status"] == "applied" and updated["data"]["updated"]["notes"] == "nice place"

    call_tool("save_application", {"job_id": "t:2", "status": "interview"})
    everything = call_tool("list_applications", {})["data"]
    assert everything["count"] == 2 and everything["by_status"] == {"applied": 1, "interview": 1}
    only = call_tool("list_applications", {"status": "interview"})["data"]
    assert [a["job_id"] for a in only["applications"]] == ["t:2"]


def test_tracker_errors_are_helpful(home):
    store()
    assert "No vacancy with id" in call_tool("save_application", {"job_id": "nope"})["error"]
    assert "not in the tracker" in call_tool("update_application_status", {"job_id": "t:1", "status": "applied"})["error"]
    assert "status" in call_tool("save_application", {"job_id": "t:1", "status": "hired"})["error"]
    assert call_tool("list_applications", {})["data"] == {"count": 0, "by_status": {}, "applications": []}


def test_saving_twice_updates_instead_of_duplicating(home):
    store()
    call_tool("save_application", {"job_id": "t:1"})
    call_tool("save_application", {"job_id": "t:1", "status": "offer"})
    data = call_tool("list_applications", {})["data"]
    assert data["count"] == 1 and data["applications"][0]["status"] == "offer"
