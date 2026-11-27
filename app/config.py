"""Central configuration.

All settings come from environment variables (or a local `.env` file).
Everything the user might want to change at runtime (model, num_ctx, ...)
lives in one `Settings` object, so the UI/CLI can switch it without a restart.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder that contains `app/`.
ROOT_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    # --- Ollama ---
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    embed_model: str = "bge-m3"
    num_ctx: int = 8192
    request_timeout: float = 120.0
    # Models can "think" before answering (qwen3 etc.). Off by default: faster.
    think: bool = False

    # --- Adzuna (very limited free quota, so it is a last-resort source) ---
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_monthly_budget: int = 25

    # --- Scraping: seconds to wait between requests to the same site ---
    scrape_delay_seconds: float = 2.5
    user_agent: str = "JobPilot/0.1 (student project; local use)"

    # --- Storage ---
    db_path: str = "data/jobpilot.db"
    log_dir: str = "logs"

    # ---- helpers ----
    @property
    def db_file(self) -> Path:
        return _resolve(self.db_path)

    @property
    def log_path(self) -> Path:
        return _resolve(self.log_dir)

    @property
    def adzuna_configured(self) -> bool:
        return bool(self.adzuna_app_id and self.adzuna_app_key)

    def update(self, **changes) -> None:
        """Change settings at runtime (e.g. switch model). Unknown keys are rejected."""
        valid = {f.name for f in fields(self)}
        for key, value in changes.items():
            if key not in valid:
                raise KeyError(f"Unknown setting: {key}")
            setattr(self, key, value)


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT_DIR / p


def normalize_host(host: str) -> str:
    """'localhost' -> '127.0.0.1'.

    On Windows a new connection to 'localhost' first tries IPv6 and waits ~2 seconds before falling
    back to IPv4 (measured: 2055 ms vs 25 ms). Using the numeric address avoids that on every request."""
    return host.replace("//localhost", "//127.0.0.1").replace("//LOCALHOST", "//127.0.0.1")


def load_settings() -> Settings:
    """Read settings from the environment / .env file."""
    load_dotenv(ROOT_DIR / ".env")
    return Settings(
        ollama_host=normalize_host(_env("OLLAMA_HOST", Settings.ollama_host)),
        ollama_model=_env("OLLAMA_MODEL", Settings.ollama_model),
        embed_model=_env("EMBED_MODEL", Settings.embed_model),
        num_ctx=int(_env("NUM_CTX", str(Settings.num_ctx))),
        request_timeout=float(_env("REQUEST_TIMEOUT", str(Settings.request_timeout))),
        think=_env("THINK", "false").lower() in ("1", "true", "yes", "on"),
        adzuna_app_id=_env("ADZUNA_APP_ID", ""),
        adzuna_app_key=_env("ADZUNA_APP_KEY", ""),
        adzuna_monthly_budget=int(_env("ADZUNA_MONTHLY_BUDGET", str(Settings.adzuna_monthly_budget))),
        scrape_delay_seconds=float(_env("SCRAPE_DELAY_SECONDS", str(Settings.scrape_delay_seconds))),
        db_path=_env("DB_PATH", Settings.db_path),
        log_dir=_env("LOG_DIR", Settings.log_dir),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Shared singleton - every module uses this one object."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
