"""CV reading, structured LLM calls and the extract_profile tool (fake Ollama, fictional CVs)."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import ollama
import pytest
from pypdf import PdfWriter

from app import db, llm
from app.config import get_settings
from app.cv import CVError, read_cv
from app.llm import LLMError
from app.tools import call_tool
from app.tools.profile_tools import Profile

SAMPLE = Path(__file__).parent.parent / "data" / "sample_cvs" / "emma_verbeke.txt"


def text_pdf(text: str) -> bytes:
    """A tiny hand-made one-page PDF containing `text` (with a correct xref table)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        "<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        f"<</Length {len(stream)}>>\nstream\n{stream}\nendstream",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets)
    out += f"trailer<</Root 1 0 R/Size {len(objects) + 1}>>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode("latin-1")


# --------------------------------------------------------------------------- cv.py
def test_read_text_cv_utf8_and_cp1252():
    assert "VERBEKE" in read_cv(SAMPLE.read_bytes(), "cv.txt")
    assert "café" in read_cv("A café owner with a very long profile text here".encode("cp1252"), "x.txt")


def test_read_text_pdf():
    text = read_cv(text_pdf("Emma Verbeke is a business student who works as a waitress in Antwerp"), "cv.pdf")
    assert "Emma Verbeke" in text and "waitress" in text


@pytest.mark.parametrize("data,name,message", [
    (b"", "a.txt", "empty"),
    (b"x" * (6 * 1024 * 1024), "a.txt", "too large"),
    (b"short", "a.txt", "too short"),
    (b"\xff\xfe\x00\x01" * 50, "a.bin", "not supported"),
])
def test_unreadable_files_give_clear_errors(data, name, message):
    with pytest.raises(CVError, match=message):
        read_cv(data, name)


def test_scanned_pdf_has_no_text():
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.write(buf)
    with pytest.raises(CVError, match="No text found"):
        read_cv(buf.getvalue(), "scan.pdf")


def test_encrypted_and_broken_pdf():
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("secret")
    writer.write(buf)
    with pytest.raises(CVError, match="password"):
        read_cv(buf.getvalue(), "locked.pdf")
    with pytest.raises(CVError, match="could not be read"):
        read_cv(b"%PDF-1.4 this is not really a pdf", "broken.pdf")


# --------------------------------------------------------------------------- llm.py
class FakeOllama:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def chat(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(message=SimpleNamespace(content=reply))


GOOD = json.dumps({"name": "Emma", "skills": ["Excel"], "languages": ["Dutch (native)"]})


@pytest.fixture
def fake(monkeypatch):
    def install(*replies):
        client = FakeOllama(*replies)
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return client
    return install


def test_chat_json_valid_and_fenced(fake):
    client = fake(GOOD, f"Sure! ```json\n{GOOD}\n```")
    assert llm.chat_json(Profile, "sys", "user").name == "Emma"
    assert llm.chat_json(Profile, "sys", "user").skills == ["Excel"]
    assert client.calls[0]["format"]["type"] == "object" and client.calls[0]["options"]["temperature"] == 0


def test_chat_json_retries_once_on_invalid_output(fake):
    client = fake("not json at all", GOOD)
    assert llm.chat_json(Profile, "s", "u").name == "Emma"
    assert "not valid JSON" in client.calls[1]["messages"][-1]["content"]


def test_chat_json_gives_up_after_two_bad_answers(fake):
    fake("nope", '{"skills": "not a list"}')
    with pytest.raises(LLMError, match="valid structured answer"):
        llm.chat_json(Profile, "s", "u")


@pytest.mark.parametrize("error,message", [
    (ollama.ResponseError("not found", 404), "ollama pull"),
    (ConnectionError("x"), "Cannot reach Ollama"),
    (httpx.ReadTimeout("slow"), "too long"),
    (RuntimeError("weird"), "RuntimeError"),
])
def test_chat_json_friendly_errors(fake, error, message):
    fake(error)
    with pytest.raises(LLMError, match=message):
        llm.chat_json(Profile, "s", "u")


def test_chat_json_drops_think_for_models_without_thinking(fake):
    client = fake(ollama.ResponseError("model does not support thinking", 400), GOOD)
    llm.chat_json(Profile, "s", "u")
    assert "think" in client.calls[0] and "think" not in client.calls[1]


# ------------------------------------------------------------------ extract_profile
@pytest.fixture
def home(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    return tmp_path


def test_extract_profile_without_any_cv(home):
    res = call_tool("extract_profile", {})
    assert not res["ok"] and "upload" in res["error"]


def test_extract_profile_uses_uploaded_cv_and_saves_profile(home, fake):
    client = fake(GOOD)
    with db.connect() as conn:
        db.save_cv_text(conn, SAMPLE.read_text(encoding="utf-8"))
    res = call_tool("extract_profile", {})
    assert res["ok"] and res["data"]["profile"]["name"] == "Emma"
    assert "EMMA VERBEKE" in client.calls[0]["messages"][1]["content"]
    with db.connect() as conn:
        assert db.load_profile(conn)["skills"] == ["Excel"]
        assert db.load_cv_text(conn).startswith("[FICTIONAL")


def test_extract_profile_with_text_argument(home, fake):
    fake(GOOD)
    res = call_tool("extract_profile", {"cv_text": "I am Emma, I know Excel and I speak Dutch natively."})
    assert res["ok"]
    with db.connect() as conn:
        assert "Emma, I know Excel" in db.load_cv_text(conn)


def test_extract_profile_reports_llm_problems(home, fake):
    fake(ConnectionError("down"))
    res = call_tool("extract_profile", {"cv_text": "A reasonably long CV text that is long enough."})
    assert not res["ok"] and "Cannot reach Ollama" in res["error"]


def test_uploaded_cv_without_profile_is_not_a_profile(home):
    with db.connect() as conn:
        db.save_cv_text(conn, "some cv")
        assert db.load_profile(conn) is None and db.load_cv_text(conn) == "some cv"


def test_sections_are_found_in_the_sample_cv():
    from app.tools.profile_tools import sections
    found = sections(SAMPLE.read_text(encoding="utf-8"))
    assert found["skills"][0].startswith("Customer service")
    assert found["languages"] == ["Dutch (native), English (fluent), French (intermediate)"]
    assert found["summary"][0].startswith("Second-year Business")


def test_empty_lists_from_a_small_model_are_filled_from_the_cv_sections(home, fake):
    fake(json.dumps({"name": "EMMA VERBEKE", "skills": [], "languages": [], "summary": ""}))
    with db.connect() as conn:
        db.save_cv_text(conn, SAMPLE.read_text(encoding="utf-8"))
    profile = call_tool("extract_profile", {})["data"]["profile"]
    assert profile["name"] == "Emma Verbeke"
    assert "Excel (basic)" in profile["skills"] and "teamwork" in profile["skills"]
    assert profile["languages"] == ["Dutch (native)", "English (fluent)", "French (intermediate)"]
    assert profile["summary"].startswith("Second-year Business Management student")


def test_llm_values_are_not_overwritten_by_the_fallback(home, fake):
    fake(json.dumps({"skills": ["Only this"]}))
    with db.connect() as conn:
        db.save_cv_text(conn, SAMPLE.read_text(encoding="utf-8"))
    assert call_tool("extract_profile", {})["data"]["profile"]["skills"] == ["Only this"]


# ------------------------------------------------------------- profile in the prompt
def test_profile_goes_into_the_system_prompt():
    from app.agent.prompts import build_system_prompt
    prompt = build_system_prompt({"name": "Emma", "skills": ["Excel", "Teamwork"], "education": ["BSc Business"]})
    assert "Emma" in prompt and "Excel, Teamwork" in prompt and "BSc Business" in prompt
    assert "What you know about the user" not in build_system_prompt(None)
