# JobPilot - rules for Claude (distilled from PROJECT_BRIEF.md)

Read `PROJECT_BRIEF.md` first (section 0 = latest team decisions, they override the rest).

## Non-negotiable
- The live demo must not crash. Stability beats features. Feature freeze 5 days before demo.
- **Real data only.** Never invent vacancies, not even as "seed" or "demo" data. Test fixtures
  are allowed only inside `tests/` and must be clearly named as test data.
- Adzuna: max 25 calls/month, counted in SQLite. Use it last, never in loops or tests.
  Scrapers (polite rate limit `SCRAPE_DELAY_SECONDS`, cached, public pages only) come first.
- Never send the CV or personal data to any job site. Search keywords only.
- Secrets live in `.env` (git-ignored). Never print or commit keys.
- Commit locally in small steps; NEVER `git push`. The team reviews and pushes.
- Ask before adding dependencies or changing the stack (Python, ollama lib, FastAPI, SQLite,
  Vue 3 via CDN, Leaflet). English UI, dark theme with yellow accent.

## Code style
- Simple, commented English code. Every file must be explainable by a student.
- Every tool: validate arguments, catch ALL exceptions, return `{"ok", "data", "error"}`.
- Agent loop: max 6 tool iterations per message, per-call timeout, one retry, graceful fallback.
- Log every tool call (name, args, duration, ok) to `logs/`.

## Run
- venv: `.venv\Scripts\python` (Windows). Tests: `.venv\Scripts\python -m pytest`.
- Ollama binary: `C:\Users\Poltavskyi Vsevolod\AppData\Local\Programs\Ollama\ollama.exe`
  (not on PATH in some shells; the HTTP API at localhost:11434 always works).
