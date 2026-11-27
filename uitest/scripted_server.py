"""The real JobPilot server with a SCRIPTED model, for checking the web UI in seconds.

Real parts: the FastAPI app, the agent loop, the tools, the vacancies (from the saved real snapshot)
and the match scores. Scripted parts: what the "LLM" says (so a conversation does not take minutes)
and the cover letter text. It runs on a temporary database; nothing of yours is touched.

    python -m uitest.scripted_server 8011            # normal
    python -m uitest.scripted_server 8012 down       # pretends Ollama is not running
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
from types import SimpleNamespace

from app.config import get_settings

_tmp = pathlib.Path(tempfile.mkdtemp())
get_settings().update(db_path=str(_tmp / "ui.db"), log_dir=str(_tmp / "logs"))

from app import db, main                                            # noqa: E402  (settings first)
from app.agent.agent import Agent                                   # noqa: E402
from app.providers import forem as forem_module                     # noqa: E402
from app.providers.aggregator import SearchOutcome                  # noqa: E402
from app.providers.base import JobQuery                             # noqa: E402
from app.providers.snapshot import SnapshotProvider                 # noqa: E402
from app.tools import TOOLS                                         # noqa: E402
from eval.run_scenario import sample_profile                        # noqa: E402

MODE = sys.argv[2] if len(sys.argv) > 2 else "normal"


def fake_search(query):
    """Real vacancies from the saved snapshot, stored like the real aggregator does."""
    jobs = SnapshotProvider().search(query)
    with db.connect() as conn:
        for job in jobs:
            db.upsert_job(conn, job.to_row())
    return SearchOutcome(jobs=jobs, data_source="live", attribution=[forem_module.ATTRIBUTION])


def words(text: str, delay: float = 0.04):
    """Stream a sentence word by word, like a real model."""
    for word in text.split(" "):
        time.sleep(delay)
        yield SimpleNamespace(message=SimpleNamespace(content=word + " ", tool_calls=None), done=False)
    yield SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), done=True, eval_count=40,
                          eval_duration=int(6e9), prompt_eval_count=900, total_duration=int(9e9))


def tool_call(name: str, args: dict):
    time.sleep(0.6)
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
    yield SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]), done=False)
    yield SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), done=True, eval_count=30,
                          eval_duration=int(4e9), prompt_eval_count=800, total_duration=int(6e9))


class ScriptedOllama:
    """Decides what to do from the last message: search -> match -> answer; 'letter' -> draft a letter."""

    def chat(self, **kw):
        if MODE == "down":
            raise ConnectionError("refused")
        messages = kw["messages"]
        last = messages[-1]
        user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if last["role"] == "user":
            if "letter" in user.lower():
                job = SnapshotProvider().search(JobQuery(keywords="serveur", limit=1))[0]
                with db.connect() as conn:
                    db.upsert_job(conn, job.to_row())
                return tool_call("draft_cover_letter", {"job_id": job.id, "language": "en"})
            return tool_call("search_jobs", {"job_type": "student", "region": "brussels", "limit": 5})
        name = last.get("tool_name")
        if name == "search_jobs":
            jobs = json.loads(last["content"])["data"]["jobs"]
            return tool_call("match_cv", {"job_ids": [j["id"] for j in jobs]})
        if name == "match_cv":
            best = json.loads(last["content"])["data"]["matches"][0]
            return words(f"The best fit is **{best['title']}** with a score of {best['score']}/100 (id: {best['job_id']}).\n\n"
                         "- It is a student job in Brussels\n- Your customer-service experience helps\n\n"
                         "Source: Le Forem open data.")
        if name == "draft_cover_letter":
            return words("Here is your letter, ready to copy.")
        return words("Hello! I can search vacancies in Belgium.")


def scripted_letter(args):
    return {"letter": "Dear hiring team,\n\nI would love to work as a waiter with you.\n\nKind regards,\nEmma Verbeke",
            "word_count": 17, "language": "en", "tone": "friendly",
            "job": {"id": args.job_id, "title": "Serveur - Job étudiant", "company": "Accent"}}


def scripted_profile(cv_text: str):
    from app.tools.profile_tools import Profile
    return Profile(name="Scripted Student", city="Mechelen", summary="Analysed by a scripted model.",
                   skills=["Teamwork", "Excel"], languages=["Dutch (native)", "English (fluent)"])


def prepare() -> None:
    profile, cv_text = sample_profile("emma_verbeke")            # the FICTIONAL student
    with db.connect() as conn:
        db.save_profile(conn, profile, cv_text=cv_text)
    import app.tools.search_jobs as search_tool
    search_tool.search_all = fake_search
    main.search_all = fake_search
    TOOLS["draft_cover_letter"].func = scripted_letter           # a real letter needs the slow LLM
    main._agent = Agent(client=ScriptedOllama())
    main.installed_models = (lambda: None) if MODE == "down" else (
        lambda: ["qwen2.5:7b", "bge-m3:latest", "llama3.2:3b"])
    main.build_profile = scripted_profile                        # reading a CV needs the slow LLM
    main.forem_mirror.refresh = lambda **kw: 24310               # no download from the portal in UI tests
    main._ollama_get = lambda path: {"version": "scripted", "models": []}
    main._refresher.start = lambda: None                         # never download from the portal in UI tests


if __name__ == "__main__":
    import uvicorn
    prepare()
    uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
