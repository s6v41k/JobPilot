"""Polite rate limiting: wait between two requests to the same site.

Every provider that talks to a website goes through one `RateLimiter`, so we never
hammer a server (default: at least `SCRAPE_DELAY_SECONDS` between requests per domain).
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    def __init__(self, min_interval: float,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._clock = clock          # injectable so tests need no real waiting
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str) -> float:
        """Block until a request to `domain` is allowed. Returns the seconds waited."""
        with self._lock:
            now = self._clock()
            last = self._last.get(domain)
            delay = 0.0 if last is None else max(0.0, last + self.min_interval - now)
            if delay:
                self._sleep(delay)
            self._last[domain] = self._clock()
            return delay
