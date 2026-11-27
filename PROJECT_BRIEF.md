# JobPilot - project brief

Final project of the course "Artificial Intelligence" at Thomas More Mechelen.
Three students, one laptop (16 GB RAM, AMD integrated graphics, no discrete GPU).
Demo: 18 December 2026 (possibly 11 December: stable MVP by 10 December).
The live demo (15-minute slot) must NOT crash. Stability beats features.

## 0. Amendments (decided later with the team - these override the original text below)

- No tutorial code exists. `docs/tutorial/` is not used. `app/agent_minimal.py` is simply a
  ~50-line, easy-to-explain agent loop using the official `ollama` library.
- **Real data only.** No invented/seed vacancies. Offline fallback = a snapshot of REAL
  vacancies we fetched earlier, labelled "Cached real data, fetched <date>".
- **Scraping is allowed** (team decision, robots.txt not enforced) but only with a polite rate
  limit, a cache, public pages only. No captcha / block circumvention, no login walls.
- **Adzuna is a last resort:** only 25 calls/month. Calls are counted in SQLite
  (`ADZUNA_MONTHLY_BUDGET`) and refused when the budget is used. Scrapers and the cache come first.
- **Agent profiles:** different roles (orchestrator, cv_analyst, job_searcher, matcher, writer)
  can use different models (`agents.yaml`). The benchmark compares role->model combinations.
- Commits are made locally and prepared for review; the team pushes them (never push automatically).
- Repository: https://github.com/s6v41k/JobPilot.git
- Everything lives in the repository root (no extra `jobpilot/` folder).

## 1. Assignment (verbatim requirements)
Create an Agentic AI application using the Ollama framework, proposed by us according to
a personal need.
Pass requirements:
 (a) a working AI Agent using the Ollama framework, as discussed in the tutorial of class 3;
 (b) at least one tool extending the agent to our use case;
 (c) a live demonstration during the last class that does not crash.
Extra marks: (d) consider different LLMs and choose the best one for the application;
 (e) tweak context, knowledge and memory of the agent for the use case;
 (f) any other extensions.

## 2. Product
JobPilot: a locally running, AGENT-FIRST assistant that helps students find student jobs,
part-time jobs, internships and full-time jobs anywhere in Belgium (Flanders, Wallonia,
Brussels). The user chats with an agent that reads their CV, searches vacancies, ranks them
against the profile, explains skill gaps, drafts short motivation letters, answers questions
about student work, and tracks applications. A map is one of several views, not the core.
The agent must visibly decide on its own which tools to call and in what order (multi-step),
and the UI must show the tool-call trace.

## 3. Priorities (MoSCoW) - build in this order
MUST (MVP, stable by 10 Dec):
 - agent loop with Ollama + tools: search_jobs, get_job_details, extract_profile,
   match_cv, draft_cover_letter, application tracker tools
 - job providers (scrapers + Adzuna last resort) with SQLite cache
 - CLI chat and a web UI with Chat, Jobs, Tracker, Profile tabs
 - robustness layer, logging, preflight check, demo mode
SHOULD:
 - model comparison benchmark (two stages) and model switching
 - memory: persistent profile, rolling summary, explicit num_ctx tuning
 - RAG knowledge tool (search_knowledge)
COULD (only after MUST and SHOULD are stable):
 - Map tab (all of Belgium), watchlist (save_search / check_new_jobs), SSE streaming polish,
   delete-my-data (GDPR) button, quality rubric for letters.
Feature freeze 5 days before the demo: only bug fixes after that.

## 4. Tech stack (ask before changing)
- Python 3.11+, official `ollama` Python library (native tool calling, our own agent loop),
  FastAPI + uvicorn (Server-Sent Events), SQLite, pydantic, httpx, pypdf, pytest, PyYAML.
- Frontend: Vue 3 via CDN (no build step), Leaflet + OpenStreetMap + markercluster.
  Dark theme with a yellow accent. English UI.
- Job data: scrapers (polite) + cache, Adzuna API (country "be") as last resort, keys in .env
  (ADZUNA_APP_ID, ADZUNA_APP_KEY). Show the required Adzuna attribution. Cache every response
  in SQLite. Never send the CV or personal data to any job site (search keywords only).
- Layout inspiration for the optional map: the "Op zoek naar een club" finder of Voetbal
  Vlaanderen (clustered markers, radius circle, Kaart/Lijst toggle). Do not copy assets or code.

## 5. Models (Ollama)
- Default dev chat model: start with a smaller model (qwen2.5:7b is installed); the final
  choice comes from the benchmark. Embeddings: bge-m3 (multilingual).
- Benchmark candidates: qwen3.5:9b, qwen3:8b, granite4.2:8b, gemma3:12b, gpt-oss:20b,
  llama3.1:8b, qwen2.5:7b, plus a small fallback (llama3.2:3b or granite4.2:3b).
  Verify tags and tool support on the Ollama library before pulling; skip models without tools.
- Everything configurable in .env (OLLAMA_HOST, OLLAMA_MODEL, EMBED_MODEL, NUM_CTX,
  REQUEST_TIMEOUT) and switchable at runtime. Support the `think` option on/off per model
  and measure its effect on latency and accuracy.
- Record `ollama --version` and the python library version in the README.

## 6. Repository structure
PROJECT_BRIEF.md, CLAUDE.md, README.md, requirements.txt, .env.example, .gitignore
docs/ demo_script.md, architecture.md, model_selection.md, requirements_checklist.md
app/ main.py, config.py, db.py, geo.py, preflight.py, cli.py,
     agent_minimal.py (tiny agent, kept as a safe fallback demo)
     agent/ agent.py, prompts.py, memory.py, streaming.py, roles.py
     providers/ base.py, adzuna.py, scrapers/, cache.py
     tools/ __init__.py (registry + schemas), search_jobs.py, job_details.py,
            profile_tools.py, match_cv.py, cover_letter.py, applications.py,
            knowledge.py, watchlist.py
     rag/ ingest.py, retriever.py
knowledge/ *.md (student work in Belgium, cover letters, interviews; cite sources)
data/ municipalities.json, sample_cvs/ (fictional only), snapshots/ (real cached vacancies)
web/ index.html, app.js, style.css
eval/ cases_screening.yaml, cases_deep.yaml, benchmark.py, weights.yaml, results/
tests/

## 7. Tools (strict JSON schemas, clear docstrings so small models call them correctly)
1. search_jobs(keywords, job_type, region, city, radius_km, language, experience_level)
2. get_job_details(job_id)
3. extract_profile(cv_text): structured profile via Ollama structured output (`format`
   with a JSON schema), saved to the DB
4. match_cv(job_id): score 0-100 (embedding similarity + skill overlap), matched and
   missing skills, one-sentence reason, based on the stored profile
5. draft_cover_letter(job_id, language, tone): SHORT (max ~150 words), focused on
   motivation and personal qualities; do not list technologies, no personal backstory
6. save_application / update_application_status / list_applications
   (interested, applied, interview, offer, rejected)
7. search_knowledge(query): RAG over knowledge/*.md, returns source file names
8. save_search / check_new_jobs: watchlist reporting only unseen vacancies
Every tool validates arguments, catches ALL exceptions and returns
{"ok": bool, "data": ..., "error": "human readable message"}.

## 8. Agent behaviour, memory, context
- System prompt: role, tools and when to use them, answer in the user's language
  (EN/NL/FR/RU input possible), never invent vacancies, only report what tools returned.
- Multi-step example: "find internships in Antwerp and tell me which fits my CV best" ->
  search_jobs -> match_cv (several) -> ranked answer.
- Memory: persistent profile injected into the prompt; last N messages + rolling summary of
  older ones; remember shown/applied jobs; explicit num_ctx. Measure with/without memory
  in the benchmark.
- Limits: max 6 tool iterations per user message, per-call timeout, one retry, graceful
  fallback message. Unknown tool or malformed arguments: catch, correct once, then apologise.
- Stream tokens and tool events to the UI (SSE); show tool calls in a collapsible trace.

## 9. API
GET /api/jobs (filters, no LLM), POST /api/chat (SSE), GET/PUT /api/profile,
POST /api/profile/cv (PDF/text), GET /api/applications (+PATCH), GET/POST /api/searches,
GET /api/health (Ollama, models, Adzuna, DB), GET/PUT /api/model, DELETE /api/my-data.

## 10. UI
Chat (streaming, tool chips, job cards), Jobs (filters + match score, works without the LLM),
Tracker (kanban), Profile (CV upload, editable parsed profile, model selector,
delete-my-data), Map (optional). Friendly error banners everywhere, never a blank page,
visible data-source badge ("Live", "Cached real data").

## 11. Robustness (the demo must not crash)
- Ollama down or model missing: clear message; non-LLM views keep working.
- Source failure: fall back to cache, then to the real snapshot.
- python -m app.preflight checks Ollama, models, DB, keys, a sample chat and a sample tool
  call, and prints a green/red report.
- Demo mode: scripted, reliable scenarios (docs/demo_script.md) with a ~10 min timing,
  plus a fallback to agent_minimal.py in the terminal.
- Log every tool call (name, args, duration, ok) to logs/. Keep the model warm.

## 12. Benchmark and model selection
Stage 1 (screening): ~10 quick cases on ALL candidates.
Stage 2 (deep): 20+ cases on the top 3: single tool, multi-step chains, NL/FR/EN/RU prompts,
malformed input, with/without memory; plus role->model combinations.
Metrics: correct tool %, valid arguments %, multi-step success %, language handling,
avg and p95 latency, tokens/s, peak memory, loops/crashes, letter-quality rubric (manual).
Weighted final score defined in eval/weights.yaml. Output a markdown table and a PNG chart
into the README and docs/model_selection.md, with pros/cons and the final justification.

## 13. Privacy
Everything runs locally. CV and profile stay in SQLite on the machine. Short privacy note in
README and UI. Test CVs are fictional. .env, logs and user data are git-ignored.

## 14. Tests and documentation
- pytest: tools, providers (mocked), agent loop with a mocked Ollama client, API smoke tests.
- docs/requirements_checklist.md maps every assignment requirement to code and a test.
- README: setup in 5 commands, screenshots, architecture, benchmark table, demo steps.
- Code simple and commented in English: each of us must be able to explain every file.

## 15. How to work
Small steps, ~18 commits, committed locally and prepared for review. After each commit tell
the team exactly how to run and test it. Ask before adding dependencies or changing the stack.
