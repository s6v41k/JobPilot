# Testing

Two kinds of tests, with different purposes.

| | Automated tests (`pytest`) | Live experiments (`eval/run_scenario.py`) |
|---|---|---|
| Question | Does the code do what we think? | How does a *real model* behave on real data? |
| Uses a real LLM | No (fake Ollama client) | Yes |
| Uses the internet | No (mocked HTTP) | Yes (Le Forem open data) |
| Speed | ~5 seconds | minutes per run on this laptop (CPU only) |
| Deterministic | Yes | No - run it several times before trusting a number |
| Results go to | the terminal | `eval/results/*.json` and `docs/experiments_log.md` |

## Automated tests

Run all: `.venv\Scripts\python -m pytest` (305 tests at the time of writing, plus 13 JavaScript tests).
Rule: a commit is made only when this prints **no failures**.

| File | Tests | What it protects |
|---|---|---|
| `test_config.py` | 4 | settings from `.env`, runtime switching of model/num_ctx |
| `test_db.py` | 7 | SQLite: vacancies, cache, profile, applications, API budget, "delete my data" |
| `test_adzuna.py` | 11 | Adzuna client: cache first, 25-calls/month budget, keys never leak into cache or errors, HTTP errors |
| `test_forem.py` | 11 | Le Forem open data: query building, expired offers hidden, internship vs student contract, rate limiter |
| `test_geo_and_inference.py` | 16 | regions, city names (Antwerp = Anvers = Antwerpen), job type / language / level guessing |
| `test_aggregator.py` | 12 | source order Forem -> Adzuna (only when Forem finds nothing/fails) -> saved jobs -> real snapshot; NL/FR keyword expansion |
| `test_tools.py` | 10 | the one safe door `call_tool`: validation, timeouts, never raises, logging |
| `test_agent.py` | 41 | agent loop with a fake Ollama: tool rounds, retries, Ollama down, missing model, loop guards, **hallucination guard** |
| `test_profile.py` | 25 | CV reading (PDF/text/scan/encrypted), structured LLM output, profile extraction |
| `test_matching.py` | 28 | skill vocabulary, language levels, score maths, `get_job_details`, `match_cv` |
| `test_mirror.py` | 22 | local copy of the Le Forem data: bulk download, failures keep the old copy, word-start search, city names, expired offers, no SQL injection, freshness label |
| `test_warmup_preflight.py` | 18 | model warm-up (state, failures never raise, no double warm-up) and the pre-flight report (Ollama down, missing model, no tool support, crashing check, exit codes) |
| `test_demo.py` | 24 | demo mode: scenarios match the written script, recordings (save/load/safe ids), replay timing and label, vacancies stored for Save, API replay, prepare, offline environment |
| `test_language.py` | 23 | language guess on short messages, the hidden reply-language hint, the 'must use a tool for vacancy questions' rule (once, cannot loop) |
| `test_web.py` | 7 | web files: assets served, page works without internet, every API path used by the JavaScript exists, Node tests |
| `test_api.py` | 32 | the REST API and SSE chat with a fake Ollama: health when Ollama is down, filters, CV upload errors, tracker, model switching, delete-my-data, calm 500 errors |
| `test_letter_and_tracker.py` | 16 | cover letters (length limit, language, no placeholders, no invented school) and the application tracker |

### Test data
- Vacancies in tests are either **trimmed real responses** (`tests/fixtures/*_real_sample.json`,
  fetched 2026-11-27) or small rows clearly named "Test job". Nothing pretends to be real.
- CVs are **fictional** (`data/sample_cvs/`).
- Tests never call Adzuna for real (the monthly budget is only 25 calls).

## Live experiments

```
python -m eval.run_scenario --model qwen2.5:7b
python -m eval.run_scenario --model llama3.2:3b --question "Zoek een stage in Antwerpen" --profile none
```
Each run uses a throw-away database, asks one question and stores: tool calls with timings, the
final answer, tokens/s, and how often the hallucination guard had to discard an answer.
The full benchmark (two stages, many cases, all candidate models) is built on this in phase P4.

Every experiment worth remembering is written down in [experiments_log.md](experiments_log.md).

## Web UI checks

| Command | What it checks | Needs |
|---|---|---|
| `node --test tests/js/util.test.js` | the JavaScript helpers: HTML escaping, links, lists, SSE parsing, formatting | Node.js |
| `python -m uitest.smoke` | a real headless Edge clicks through the whole UI (64 checks: chat, Jobs, Tracker, Profile, system check, Demo panel and replay, phone, Ollama down), screenshots to `eval/results/ui/` | Microsoft Edge |

`uitest/scripted_server.py` runs the real server, agent loop and tools with a *scripted model*, so a whole conversation takes seconds instead of minutes.
Everything is real except the words the "model" says and the text of the cover letter.

## Before a rehearsal or the demo

```powershell
.venv\Scripts\python -m app.preflight --chat     # everything green? (loads the model, ~1.5 min the first time)
.venv\Scripts\python -m pytest                   # code still fine?
.venv\Scripts\python -m uitest.smoke             # web UI still fine?
```
