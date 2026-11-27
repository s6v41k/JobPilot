# JobPilot

A local, agent-first assistant for finding student jobs, internships and full-time jobs in
Belgium. Built with [Ollama](https://ollama.com) and Python for the course
"Artificial Intelligence" (Thomas More Mechelen).

> Work in progress. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the full plan.

## Setup

```powershell
git clone https://github.com/s6v41k/JobPilot.git; cd JobPilot
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # then fill in your keys
.venv\Scripts\python -m pytest
```

## Run it

```powershell
.venv\Scripts\python -m app.main          # web app: http://127.0.0.1:8000  (Chat, Jobs, Tracker, Profile)
.venv\Scripts\python -m app.cli           # chat in the terminal
.venv\Scripts\python -m app.agent_minimal # the 50-line fallback agent
.venv\Scripts\python -m app.demo prepare   # the day before the demo (data, snapshot, warm-up, check)
.venv\Scripts\python -m app.preflight --chat # is everything ready? (green / yellow / red report)
.venv\Scripts\python -m pytest            # 342 tests, ~20 s
.venv\Scripts\python -m uitest.smoke      # clicks through the web UI in a headless Edge (72 checks)
```
Which model does which job is set in [agents.yaml](agents.yaml) (teams) and in the Profile tab.
Ollama must be running with a model, for example `ollama pull qwen2.5:7b` and `ollama pull bge-m3`.
Everything about how it is built and tested: [docs/development_log.md](docs/development_log.md),
[docs/testing.md](docs/testing.md), [docs/experiments_log.md](docs/experiments_log.md).

## Versions used
- Ollama: 0.40.18
- `ollama` Python library: see `requirements.txt` (installed version is recorded in `docs/model_selection.md`)
- Python 3.11

## Privacy
Everything runs locally. Your CV and profile are stored only in a SQLite file on your own
machine. Only search keywords are sent to job sites - never your CV or personal data.
`.env`, logs and the database are git-ignored.
