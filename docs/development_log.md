# Development log

A diary of how JobPilot is built: **one entry per commit**, newest at the bottom.
Each entry answers four questions: *what* was built, *why* it was built that way, *how to run and test it*,
and *what surprised us*. Real-model measurements live in [experiments_log.md](experiments_log.md),
test details in [testing.md](testing.md).

## Plan and progress

| # | Commit | Status |
|---|---|---|
| 1 | Skeleton, config, brief | done |
| 2 | SQLite storage | done |
| 3 | Adzuna provider (last resort, 25 calls/month) | done |
| 4 | Le Forem open-data provider | done |
| 5 | Tool registry, `search_jobs`, multi-source search | done |
| 6 | Agent loop, CLI chat, minimal agent | done |
| 7 | CV reading, `extract_profile` | done |
| 8 | `get_job_details`, `match_cv` | done |
| 9 | Cover letters, application tracker | done |
| 10 | FastAPI: REST + streaming chat | done |
| 10b | Local copy of the Forem dataset (daily quota problem) | done - **live download still to be verified** |
| 11 | Web UI: Chat with tool trace | done |
| 12 | Web UI: Jobs, Tracker, Profile - **MVP (target 10 Dec)** | done (27 Nov) |
| 13 | Preflight check, demo mode, error banners | |
| 14 | Agent roles (one model per job) and runtime model switching | |
| 15 | Benchmark (two stages) and model selection | |
| 16 | Memory (rolling summary, `num_ctx`) and RAG knowledge tool | |
| 17 | Map and watchlist | |
| 18 | README, final docs, polish | |

## Architecture at a glance

```
 user ──> CLI / Web UI ──> Agent loop (app/agent) ──> Ollama (local LLM)
                               │   ▲
                  tool calls   ▼   │ results {"ok","data","error"}
                          call_tool()  (validate, time out, never raise, log)
                               │
     ┌──────────────┬──────────┼─────────────┬───────────────┐
 search_jobs   get_job_details  match_cv   extract_profile  draft_cover_letter / tracker
     │                                        │
 providers: Le Forem (live) -> Adzuna (budget) -> saved jobs -> real snapshot
                         SQLite (data/jobpilot.db): jobs, cache, profile, applications, embeddings
```
Everything runs on the student's own computer. Only search keywords leave it (to Le Forem / Adzuna).

---

## Commit 1 - Skeleton, config, brief (`e9040c5`)
**What:** repo layout, `PROJECT_BRIEF.md` (with a section 0 listing team decisions that override the original brief),
`app/config.py`, `.env.example`, `requirements.txt`, pytest setup.
**Why:** one `Settings` object that can be changed at runtime (model, `num_ctx`) is what makes "switch model in the UI"
possible later. Secrets only in `.env` (git-ignored).
**Run:** `.venv\Scripts\python -m pytest`.

## Commit 2 - SQLite storage (`f1c4729`)
**What:** `app/db.py`: tables for vacancies, HTTP cache, profile (one row), applications, saved searches and the monthly API budget.
Plain `sqlite3`, no ORM; a fresh connection per call (FastAPI uses several threads).
**Why:** a student can read every query. "Delete my data" removes personal tables but keeps public vacancies.

## Commit 3 - Adzuna provider (`a0b906f`)
**What:** `JobProvider` interface and `Job` model (`providers/base.py`), response cache with secrets stripped from keys,
Adzuna client that goes **cache -> budget check -> one HTTP call**.
**Why:** the free key allows only 25 calls a month, so the budget is counted in SQLite and tests never touch the real API.
**Surprise:** one real call showed Adzuna cuts descriptions to 500 characters and 10 of 50 adverts have no location.
The advert type is guessed with keyword rules; "Backstage" and "Dringend studenten gezocht" taught us word boundaries and title rules.

## Commit 4 - Le Forem open data (`f91395d`)
**What:** `providers/forem.py` queries the official open-data portal of Wallonia-Brussels, plus a polite `RateLimiter`.
**Why:** Jobat and VDAB refuse scripts (HTTP 403) and we decided not to circumvent that; Actiris has no API. Searching for an
*official* source found a dataset of ~25 000 live offers (licence CC BY-SA 4.0), which also contains Flemish and Brussels offers.
**Surprises:** the dataset includes expired offers (always filter on the end date); internships are signed on the student
contract, so "student contract" must not override "internship"; no free-text descriptions exist.
**Run:** the live data needs no key. A real snapshot is saved in `data/snapshots/`.

## Commit 5 - Tool registry and `search_jobs` (`20c9354`)
**What:** `app/tools/__init__.py`: the single door `call_tool()` (validate arguments, time limit, catches *every* exception,
logs to `logs/tool_calls.jsonl`). `providers/aggregator.py`: Forem -> Adzuna (only if fewer than 5 results) -> saved jobs -> real snapshot,
each labelled `live | cached | snapshot`. English keywords are also searched in Dutch/French.
**Why:** small models need simple schemas and clear errors; the fallback chain keeps the demo alive without internet.

## Commit 6 - Agent loop and CLI (`d2be62b`)
**What:** `app/agent/agent.py` (streaming tool-calling loop: max 6 rounds, one retry, friendly errors, loop guards, text-form
tool calls), `prompts.py`, `cli.py`, and `agent_minimal.py` (50 lines, safe fallback for the demo).
**Run:** `.venv\Scripts\python -m app.cli` (commands `/reset`, `/model NAME`, `/quit`).
**Surprise:** on the second question the model **invented a vacancy with a link** because the chat history had no tool calls in it.
Two fixes: history keeps compact tool calls, and every answer is checked - links/ids that no tool returned make the answer
be discarded (`retract` event) and the model must search. See experiments log, section 3.

## Commit 7 - CV and profile (`27a3a30`)
**What:** `app/cv.py` reads PDF/text (clear errors for scans, passwords, binaries), `app/llm.py` gets structured JSON answers
(schema, one repair retry), `extract_profile` tool. Two **fictional** CVs in `data/sample_cvs/`.
**Why:** the tool takes no text argument: it uses the CV already uploaded, because small models copy long text badly.
**Surprise:** the model left skills/languages empty; a plain-text fallback that reads the "Skills/Languages" sections fixed it.

## Commit 8 - `get_job_details` and `match_cv` (`1875e28`, follow-ups `c177cd3`, `831273a`)
**What:** score 0-100 from three explainable signals: text similarity (bge-m3 embeddings, cached), skills overlap (EN/NL/FR
vocabulary in `app/skills.py`) and language fit (important in Belgium). Several ids per call. Works without embeddings.
**Why not an LLM?** it would add ~40 s per vacancy on this CPU, and a formula always gives the same answer.
**Surprises:** calibrated on 108 real vacancies; first scale was too generous. Small models invented scores ("85/100") and
bracketed ids "(123)" - the hallucination guard now checks those too.
Mistake worth remembering: a commit was pushed with one failing test because the shell chain checked `tail`, not `pytest`. Fixed in `831273a`.

## Docs commit (`4405312`)
`docs/experiments_log.md`, `docs/testing.md` and `eval/run_scenario.py` (reproducible live runs saved as JSON).

## Commit 9 - Cover letters and tracker (`27b93e8`)
**What:** `draft_cover_letter` (about 130-150 words, language of the vacancy, three tones) and the tracker tools
`save_application`, `update_application_status`, `list_applications` (interested / applied / interview / offer / rejected).
**Why checks live in code, not only in the prompt:** reading real letters showed four defects (placeholders, greeting the
candidate, English greeting in a French letter, an invented university). Each is now detected and the model gets one repair
retry with precise feedback.
**Run:** `python -m eval.run_scenario --model qwen2.5:7b` for the search+match scenario; letters are tested live as in experiments log section 8.

## Commit 10 - FastAPI server (this commit)
**What:** `app/main.py` - REST API and the streaming chat.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Ollama, models, database, Adzuna budget - the UI status bar; answers even when Ollama is down |
| `GET /api/jobs` | search with filters, **no LLM**; optional `match=true` adds a match score per vacancy |
| `GET /api/jobs/{id}`, `POST /api/match`, `POST /api/tools/{name}` | details, scores, and any agent tool as a button action |
| `POST /api/chat` | **Server-Sent Events**: `token`, `tool_call`, `tool_result`, `retract`, `error`, `done` |
| `GET/PUT /api/profile`, `POST /api/profile/cv`, `POST /api/profile/analyze` | CV upload (PDF/text/pasted), analysis, manual edits |
| `GET/POST/PATCH/DELETE /api/applications` | tracker |
| `GET/PUT /api/model` | switch model, `num_ctx`, `think` at runtime (checks the model is installed) |
| `DELETE /api/my-data` | GDPR button |

**Why these choices**
- Every error becomes `{"error": "short human sentence"}`; there is a catch-all handler, so the UI never sees a stack trace.
- Only one chat runs at a time (the CPU can run one model call anyway); a second request gets a polite message, not a hang.
- The UI can call any agent tool directly (`/api/tools/...`) so buttons use the same validated door as the agent.
- Server binds to `127.0.0.1` only: nothing is exposed to the network.

**Run:** `.venv\Scripts\python -m app.main`, open http://127.0.0.1:8000 (a placeholder page until commit 11).
**Test:** `tests/test_api.py` (26 tests, fake Ollama, no internet). Also checked by hand: health, jobs, SSE chat with the real model.

**Surprises (important for the demo)**
1. **Le Forem's portal has a daily quota of 10 000 anonymous requests per IP** (error 10005, resets 00:00 UTC). We exhausted it
   during development although we made far fewer calls ourselves, so the quota is shared with other users of our network.
   -> next commit mirrors the whole dataset locally (one bulk download instead of many small queries).
2. A failed Forem call made the aggregator spend a **real Adzuna call** (2 of 25 used so far). Rule tightened: Adzuna is only
   tried when Forem found nothing or failed - never because "only a few" results came back.
3. A budget counter lives in the database file, so tests that use a temporary database do not see the real count. For now: do not
   run live searches against temporary databases while Forem is unavailable.

## Commit 10b - Local copy of the Le Forem dataset (this commit)
**What:** `app/providers/forem_mirror.py` downloads **all current offers in one request** (the portal's `exports/json`
endpoint) into the table `forem_offers`; `ForemProvider` now searches that local copy and only falls back to the live API
when no copy exists yet. The server refreshes the copy in a background thread at start-up and then every hour if it is older than 6 h.
**Why:** the portal allows 10 000 anonymous requests per day per address and we ran into it (see commit 10). Searching locally is
also faster (about 150 ms over 25 000 offers, measured), works offline during the demo and does not bother the portal.
**How it behaves**
- Search words must *start* a word of the title or occupation ("serveur" finds "Serveurs", "erveur" does not); accents and case are ignored.
- City names work in NL/FR/EN (`Antwerp` = `Anvers` = `Antwerpen`); `Brussels` is the whole region.
- Expired offers are filtered at query time, so the copy never shows yesterday's offers.
- A failed download keeps the old copy. A copy older than 24 h is labelled `cached` with a note ("last refreshed 30 hours ago").
- User text is cleaned of SQL wildcards (`%`, `_`) and passed as parameters: it can never act as a pattern or as SQL.
**Run:** `.venv\Scripts\python -m app.providers.forem_mirror` (download now), or just start the server. `/api/health` shows
`vacancies: {offers, age_hours}`; `POST /api/refresh` triggers a download.
**Test:** `tests/test_mirror.py` (22 tests) uses the **real** 108-offer snapshot as the "downloaded dataset".
**To verify live (not possible on the day of writing - the portal quota was exhausted until 00:00 UTC):**
1. run `python -m app.providers.forem_mirror` and check it prints about 24 000 offers (the export format is assumed to be a JSON list of records);
2. check the file size of `data/jobpilot.db` (expected ~35 MB) and a search through `/api/jobs`.
If the export format differs, only `refresh()` needs adjusting; the search side is already tested.

## Commit 11 - Web UI: the Chat tab (this commit)
**What:** `web/` - Vue 3 without a build step. Dark theme, yellow accent, English UI.
- **Chat** streams the answer word by word and shows what the agent decides: a chip per tool call (running / ok / failed with its duration),
  a collapsible **tool trace** with the arguments, and a "thinking... 12 s" indicator (a local model can take minutes, so silence would look like a crash).
- **Vacancy cards, match scores and cover letters are built from the tool results, not from the model's text.** They are therefore always correct
  (real titles, links, scores), sorted by score, with matched skills in green and missing ones struck through. Save puts a card into the tracker.
- A data badge ("Live data" / "Cached real data" / "Saved snapshot from ...") and the licence attribution are shown under every answer.
- Status bar: Ollama state + active model, size and age of the local vacancy list. Friendly banners for: server not reachable, Ollama not running,
  model not installed, vacancy list still downloading. Stop button, New chat, copy button for letters, suggestions in four languages.
- The other tabs exist but say "next step" (commit 12).
**Why**
- **Vue is downloaded into `web/vendor/` instead of loaded from a CDN** (v3.5.46, MIT licence). The demo may run without internet; with a CDN the page would be empty.
- `web/util.js` holds the pure logic (safe Markdown, SSE parsing, formatters) so it can be tested with Node without a browser.
- Markdown is *escaped first* and only a few patterns become tags; only `http(s)` links are allowed - a model answer can never inject HTML or `javascript:` links.
**Run:** `.venv\Scripts\python -m app.main`, open http://127.0.0.1:8000.
**Test (three layers):**
1. `node --test tests/js/util.test.js` - 11 tests of the helpers (also run by pytest if Node is installed);
2. `tests/test_web.py` - assets exist, the page needs no internet, every `/api/...` path used by `app.js` exists on the server;
3. **`python -m uitest.smoke`** - drives a real headless Edge: types a question, waits for streaming, checks chips, cards, scores, trace, Save, letter,
   tabs, New chat, phone width, and the "Ollama is down" state (25 checks, screenshots in `eval/results/ui/`). It uses `uitest/scripted_server.py`: the
   real server and tools with real vacancies from the snapshot, but a scripted "model", so it finishes in about 30 s. Run it before every rehearsal.
**Surprises**
- A first run showed *no scores and no answer* - not a UI bug: the scripted search had not saved the vacancies, so `match_cv` correctly said "unknown id".
  The UI showed the failed chip and the error calmly instead of breaking.
- Scrollbars and the textarea were light-grey on the dark theme until `color-scheme: dark` was added.

## Commit 12 - Jobs, Tracker and Profile tabs: the MVP (this commit)
**What:** the three remaining tabs, each in its own file (`web/tab-jobs.js`, `tab-tracker.js`, `tab-profile.js`) plus the shared vacancy card (`web/components.js`).
- **Jobs** - filters (keywords, type, region, city, language, 20/50 results), vacancy cards with *Details*, *Letter* and *Save*, optional **"Match with my CV"** scores
  (cards re-sort by score), data badge + attribution, a helpful message when nothing is found. **Works without the LLM**: only the optional match score uses the
  embedding model, and without it the score falls back to skills + language. The *Letter* button opens a dialog (language, tone, copy).
- **Tracker** - a kanban board with the five statuses (interested / applied / interview / offer / rejected): change the status in a drop-down, add a note, remove, open the ad.
- **Profile** - CV upload (PDF / text file / pasted text) and *Analyze my CV*; the extracted profile is shown in an **editable form** (one item per line); model picker
  (installed models, context size, thinking on/off) that takes effect immediately; "Refresh now" for the local vacancy list; a two-step **Delete my data** button.
- Toasts (floating, fade away) confirm actions; banners at the top are only for states that last (server down, Ollama off, model missing, list downloading).
**Why it is built like this:** one vacancy card component is used by the chat and by the Jobs tab, so a card always looks and behaves the same. Tabs refresh themselves
when opened (so a vacancy saved in the chat appears in the Tracker at once). Every API path used by the JavaScript is checked against the real server routes in a test.
**Run:** `.venv\Scripts\python -m app.main` and open http://127.0.0.1:8000.
**Tests:** 13 Node tests, 237 Python tests, and **54 browser checks** (`python -m uitest.smoke`) covering chat, Jobs filters / match / details / letter dialog, Tracker moves / notes / remove,
Profile edit / paste CV / analyze / switch model / refresh / delete, phone width and the "Ollama is down" state.

**First end-to-end run with a REAL model in the browser (MVP check, 2026-11-27)** - real server, `qwen2.5:7b`, vacancy list seeded from the real snapshot, fictional CV:
*"Find student jobs in Brussels and tell me which one fits my CV best"* -> `search_jobs` (37 ms) -> `match_cv` with **all 5** ids (5.2 s) -> correct, grounded answer
(best: "Serveur - Job etudiant", 85/100, a real score), 5 cards sorted by score, source line shown, no retraction, no error. **Total 222 s** at 5.1 tokens/s.
It works, but **speed is now the main risk for the demo**; commits 13-15 deal with it (warm model, scripted demo scenarios, smaller models, iGPU test).

**Surprises**
- The first UI test run failed on several checks. Two were real *design* problems, found by looking at the screenshots (not by the checks): buttons wrapped onto two lines in narrow cards,
  and confirmations piled up as banners and pushed the page down. Fixed (wider cards, toasts).
- The others were wrong assumptions in the test (a vacancy was already tracked from the chat test; the first `input` of the Profile tab is the hidden file picker).
- Quota reminder: the live Forem download could still not be verified today; the local list in this test was seeded from the saved snapshot.
