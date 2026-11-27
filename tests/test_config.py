import pytest

from app.config import Settings, load_settings


def test_defaults_without_env(monkeypatch):
    for var in ("OLLAMA_MODEL", "NUM_CTX", "ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Don't let the developer's real .env leak into the test.
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **k: None)
    s = load_settings()
    assert s.num_ctx == 8192
    assert s.adzuna_configured is False


def test_env_overrides(monkeypatch):
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("NUM_CTX", "4096")
    monkeypatch.setenv("ADZUNA_APP_ID", "abc")
    monkeypatch.setenv("ADZUNA_APP_KEY", "def")
    s = load_settings()
    assert s.ollama_model == "llama3.2:3b"
    assert s.num_ctx == 4096
    assert s.adzuna_configured is True


def test_runtime_update_and_unknown_key():
    s = Settings()
    s.update(ollama_model="x:1b", think=True)
    assert s.ollama_model == "x:1b" and s.think is True
    with pytest.raises(KeyError):
        s.update(nope=1)
