"""The web server: a small REST API plus a streaming chat endpoint.

Start it with:   python -m app.main        (then open http://127.0.0.1:8000)

Design rules (the demo must never crash):
  * every error is turned into a short JSON message {"error": "..."} - never a stack trace;
  * everything except the chat works WITHOUT the LLM (jobs, tracker, profile, health);
  * the chat streams Server-Sent Events: tokens and tool calls appear live in the UI.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator, Literal

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db
from app.agent.agent import Agent
from app.config import ROOT_DIR, get_settings
from app.cv import CVError, read_cv
from app.llm import LLMError
from app.matching import match_jobs
from app.providers.aggregator import search_all
from app.providers.base import JobQuery
from app.tools import TOOLS, call_tool
from app.tools.profile_tools import Profile, build_profile

WEB_DIR = ROOT_DIR / "web"

app = FastAPI(title="JobPilot", version="0.1")


# ------------------------------------------------------------------ error handling
@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or 'request'}: {e['msg']}" for e in exc.errors())
    return JSONResponse({"error": f"Invalid request - {problems}"}, status_code=422)


@app.exception_handler(HTTPException)
async def http_problem(request: Request, exc: HTTPException):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def anything_else(request: Request, exc: Exception):
    # Last line of defence: the user sees a calm message, the details stay in the server console.
    print(f"[server error] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse({"error": "Something went wrong on the server. Please try again."}, status_code=500)


# ------------------------------------------------------------------------ Ollama
def _ollama_get(path: str) -> Any:
    """GET from the Ollama HTTP API with a short timeout. Raises httpx errors."""
    host = get_settings().ollama_host.rstrip("/")
    response = httpx.get(f"{host}{path}", timeout=3)
    response.raise_for_status()
    return response.json()


def installed_models() -> list[str] | None:
    """Names of the models installed in Ollama, or None when Ollama is not reachable."""
    try:
        return [m["name"] for m in _ollama_get("/api/tags").get("models", [])]
    except Exception:
        return None


def _has(models: list[str], wanted: str) -> bool:
    return any(m == wanted or m == f"{wanted}:latest" or m.split(":")[0] == wanted for m in models)


# ------------------------------------------------------------------------ agent
_agent: Agent | None = None
_chat_lock = threading.Lock()       # one chat at a time: the laptop can only run one model call anyway


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


# ----------------------------------------------------------------------- health
@app.get("/api/health")
def health() -> dict:
    """Everything the UI needs to show a friendly status bar (never raises)."""
    s = get_settings()
    result: dict[str, Any] = {"ok": True}

    models = installed_models()
    try:
        version = _ollama_get("/api/version").get("version") if models is not None else None
    except Exception:
        version = None
    result["ollama"] = {
        "ok": models is not None, "host": s.ollama_host, "version": version,
        "message": None if models is not None else "Ollama is not running. Start the Ollama app to use the chat.",
    }
    result["models"] = {
        "chat": {"name": s.ollama_model, "installed": _has(models, s.ollama_model) if models is not None else None},
        "embed": {"name": s.embed_model, "installed": _has(models, s.embed_model) if models is not None else None},
        "installed": models or [],
    }
    try:
        with db.connect() as conn:
            result["database"] = {"ok": True, "applications": len(db.list_applications(conn)),
                                  "has_profile": db.load_profile(conn) is not None,
                                  "adzuna_used": db.budget_used(conn, "adzuna")}
    except Exception as e:
        result["database"] = {"ok": False, "message": f"Database problem ({type(e).__name__})"}
    result["adzuna"] = {"configured": s.adzuna_configured, "monthly_budget": s.adzuna_monthly_budget,
                        "used": result["database"].get("adzuna_used")}
    result["ok"] = bool(result["ollama"]["ok"] and result["database"]["ok"])
    return result


# ------------------------------------------------------------------------- jobs
@app.get("/api/jobs")
def jobs(
    keywords: str = Query("", max_length=100),
    job_type: Literal["student", "part-time", "internship", "full-time"] | None = None,
    region: Literal["flanders", "wallonia", "brussels"] | None = None,
    city: str | None = Query(None, max_length=60),
    language: Literal["nl", "fr", "en"] | None = None,
    experience_level: Literal["none", "junior", "medior", "senior"] | None = None,
    limit: int = Query(20, ge=1, le=50),
    match: bool = Query(False, description="add a match score for each vacancy (needs a profile)"),
) -> dict:
    """Search vacancies. No LLM involved; match scores use the embedding model only."""
    query = JobQuery(keywords=keywords, job_type=job_type, region=region, city=city or None, language=language,
                     experience_level=experience_level, limit=limit)
    outcome = search_all(query)
    rows = [j.to_row() for j in outcome.jobs]
    if match and rows:
        with db.connect() as conn:
            profile = db.load_profile(conn)
        if profile:
            scores = {m["job_id"]: m for m in match_jobs(rows, profile)}
            for row in rows:
                row["match"] = scores.get(row["id"])
    return {"count": len(rows), "data_source": outcome.data_source, "snapshot_date": outcome.snapshot_date,
            "notes": outcome.notes, "attribution": outcome.attribution, "jobs": rows}


@app.get("/api/jobs/{job_id}")
def job_details(job_id: str) -> dict:
    result = call_tool("get_job_details", {"job_id": job_id})
    if not result["ok"]:
        raise HTTPException(404, result["error"])
    return result["data"]


class MatchRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=8)


@app.post("/api/match")
def match(request: MatchRequest) -> dict:
    result = call_tool("match_cv", {"job_ids": request.job_ids})
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return result["data"]


@app.post("/api/tools/{name}")
def run_tool(name: str, arguments: dict[str, Any] | None = None) -> dict:
    """Run one agent tool directly (the UI uses it for buttons like 'draft letter').
    Always answers 200 with {"ok", "data", "error"}; the tool itself reports problems."""
    if name not in TOOLS:
        raise HTTPException(404, f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}")
    return call_tool(name, arguments or {})


# ------------------------------------------------------------------------- chat
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    reset: bool = Field(False, description="forget the conversation before answering")


def sse(event: str, payload: dict) -> str:
    """One Server-Sent Event: 'event: name' + 'data: json' + blank line."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def chat_stream(message: str, reset: bool) -> Iterator[str]:
    if not _chat_lock.acquire(blocking=False):
        yield sse("error", {"type": "error", "message": "Another question is still being answered. "
                                                        "Please wait a moment."})
        return
    try:
        agent = get_agent()
        if reset:
            agent.reset()
        for event in agent.run(message):
            yield sse(event["type"], event)
    except Exception as e:                       # the agent already guards itself; this is the backstop
        yield sse("error", {"type": "error", "message": f"Something went wrong ({type(e).__name__}). Please try again."})
    finally:
        _chat_lock.release()


@app.post("/api/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    """Stream the agent's work as Server-Sent Events:
    token / tool_call / tool_result / retract / error / done."""
    return StreamingResponse(chat_stream(request.message, request.reset), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/reset")
def chat_reset() -> dict:
    get_agent().reset()
    return {"ok": True}


# ---------------------------------------------------------------------- profile
@app.get("/api/profile")
def get_profile() -> dict:
    with db.connect() as conn:
        profile, cv_text = db.load_profile(conn), db.load_cv_text(conn)
    return {"profile": profile, "has_cv": bool(cv_text), "cv_chars": len(cv_text or ""),
            "cv_preview": (cv_text or "")[:400]}


@app.put("/api/profile")
def put_profile(profile: Profile) -> dict:
    """Save an edited profile (the UI lets the student correct what the model extracted)."""
    with db.connect() as conn:
        db.save_profile(conn, profile.model_dump())
    return {"ok": True, "profile": profile.model_dump()}


@app.post("/api/profile/cv")
async def upload_cv(file: UploadFile | None = File(None), text: str | None = Form(None)) -> dict:
    """Store the CV text (PDF or text file, or pasted text). Analysing it is a separate call."""
    try:
        if file is not None:
            cv_text = read_cv(await file.read(), file.filename or "")
        elif text and text.strip():
            cv_text = read_cv(text.encode("utf-8"), "pasted.txt")
        else:
            raise CVError("Upload a PDF or text file, or paste the CV text.")
    except CVError as e:
        raise HTTPException(400, str(e)) from None
    with db.connect() as conn:
        db.save_cv_text(conn, cv_text)
    return {"ok": True, "cv_chars": len(cv_text), "cv_preview": cv_text[:400]}


@app.post("/api/profile/analyze")
def analyze_cv() -> dict:
    """Ask the LLM to turn the stored CV text into a structured profile (takes up to a minute)."""
    with db.connect() as conn:
        cv_text = db.load_cv_text(conn)
    if not cv_text:
        raise HTTPException(400, "Upload your CV first.")
    try:
        profile = build_profile(cv_text)
    except LLMError as e:
        raise HTTPException(503, str(e)) from None
    with db.connect() as conn:
        db.save_profile(conn, profile.model_dump())
    return {"ok": True, "profile": profile.model_dump()}


# ----------------------------------------------------------------- applications
Status = Literal["interested", "applied", "interview", "offer", "rejected"]


class NewApplication(BaseModel):
    job_id: str
    status: Status = "interested"
    notes: str | None = Field(None, max_length=500)


class ApplicationChange(BaseModel):
    status: Status | None = None
    notes: str | None = Field(None, max_length=500)


@app.get("/api/applications")
def applications(status: Status | None = None) -> dict:
    with db.connect() as conn:
        rows = db.list_applications(conn, status)
    return {"count": len(rows), "applications": rows}


@app.post("/api/applications")
def add_application(item: NewApplication) -> dict:
    result = call_tool("save_application", item.model_dump())
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return result["data"]


@app.patch("/api/applications/{job_id}")
def change_application(job_id: str, change: ApplicationChange) -> dict:
    with db.connect() as conn:
        current = next((a for a in db.list_applications(conn) if a["job_id"] == job_id), None)
    if current is None:
        raise HTTPException(404, f"'{job_id}' is not in the tracker.")
    result = call_tool("update_application_status",
                       {"job_id": job_id, "status": change.status or current["status"], "notes": change.notes})
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return result["data"]


@app.delete("/api/applications/{job_id}")
def remove_application(job_id: str) -> dict:
    with db.connect() as conn:
        if not db.delete_application(conn, job_id):
            raise HTTPException(404, f"'{job_id}' is not in the tracker.")
    return {"ok": True}


# ------------------------------------------------------------------------ model
class ModelChange(BaseModel):
    model: str | None = Field(None, max_length=80)
    num_ctx: int | None = Field(None, ge=1024, le=32768)
    think: bool | None = None


def model_info() -> dict:
    s = get_settings()
    return {"model": s.ollama_model, "embed_model": s.embed_model, "num_ctx": s.num_ctx, "think": s.think,
            "installed": installed_models()}


@app.get("/api/model")
def get_model() -> dict:
    return model_info()


@app.put("/api/model")
def put_model(change: ModelChange) -> dict:
    """Switch the chat model (or num_ctx / think) without restarting the server."""
    s = get_settings()
    if change.model:
        models = installed_models()
        if models is None:
            raise HTTPException(503, "Ollama is not running, so the model cannot be checked.")
        if not _has(models, change.model):
            raise HTTPException(400, f"The model '{change.model}' is not installed. "
                                     f"Install it with: ollama pull {change.model}")
        s.update(ollama_model=change.model)
        get_agent().model = change.model
    if change.num_ctx:
        s.update(num_ctx=change.num_ctx)
    if change.think is not None:
        s.update(think=change.think)
    return model_info()


# ---------------------------------------------------------------------- privacy
@app.delete("/api/my-data")
def delete_my_data() -> dict:
    """GDPR button: remove CV, profile, applications and saved searches from this computer."""
    with db.connect() as conn:
        db.delete_user_data(conn)
    get_agent().reset()
    return {"ok": True, "message": "Your CV, profile and applications were deleted from this computer."}


# ------------------------------------------------------------------- static web
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    print("JobPilot is running: open http://127.0.0.1:8000  (Ctrl+C to stop)")
    main()
