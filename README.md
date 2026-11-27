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

## Versions used
- Ollama: 0.40.18
- `ollama` Python library: see `requirements.txt` (installed version is recorded in `docs/model_selection.md`)
- Python 3.11

## Privacy
Everything runs locally. Your CV and profile are stored only in a SQLite file on your own
machine. Only search keywords are sent to job sites - never your CV or personal data.
`.env`, logs and the database are git-ignored.
