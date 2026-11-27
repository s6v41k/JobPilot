"""Reading a CV file (PDF or plain text) into clean text.

Only text-based PDFs work: a scanned image has no text layer, and we say so clearly
instead of failing silently. Everything stays on this computer.
"""
from __future__ import annotations

import io
import re

from pypdf import PdfReader

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PAGES = 10
MAX_CHARS = 20000


class CVError(Exception):
    """The CV could not be read. The message is meant for the user."""


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_cv(data: bytes, filename: str = "") -> str:
    """Return the text of an uploaded CV. Raises CVError with a helpful message."""
    if not data:
        raise CVError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise CVError("The file is too large (max 5 MB).")
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise CVError("The PDF is password protected. Remove the password and upload it again.")
            pages = [(page.extract_text() or "") for page in reader.pages[:MAX_PAGES]]
        except CVError:
            raise
        except Exception:
            raise CVError("This PDF could not be read. Try exporting it again, or paste the text instead.") from None
        text = clean_text("\n\n".join(pages))
        if len(text) < 30:
            raise CVError("No text found in this PDF (it may be a scan). "
                          "Paste the CV text instead, or export a text-based PDF.")
    else:
        if b"\x00" in data[:2000]:                  # NUL bytes mean a binary file, not text
            raise CVError("This file type is not supported. Upload a PDF or a .txt file.")
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = clean_text(data.decode(encoding))
                break
            except UnicodeDecodeError:
                continue
        else:
            raise CVError("This file type is not supported. Upload a PDF or a .txt file.")
        if len(text) < 30:
            raise CVError("The CV text is too short to analyse.")
    return text[:MAX_CHARS]
