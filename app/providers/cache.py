"""Response cache helpers: never ask a site the same question twice.

The cache lives in the `http_cache` table (see app/db.py). The key is a hash of the
URL and the query parameters. Secrets (API keys) are removed BEFORE hashing, so they
never end up in the database.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SECRET_PARAMS = {"app_id", "app_key", "api_key", "key", "token"}


def make_key(source: str, url: str, params: dict[str, Any] | None = None) -> str:
    """Stable cache key for (source, url, params) without any secret parameter."""
    safe = {k: v for k, v in (params or {}).items() if k not in SECRET_PARAMS}
    raw = json.dumps([source, url, sorted(safe.items())], ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
