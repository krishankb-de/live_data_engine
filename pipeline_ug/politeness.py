"""Per-domain async rate-limiting gate.

Guarantee: no more than one outbound request per `min_interval` seconds to the
same domain, regardless of how many concurrent tasks are running. Each domain
has its own `asyncio.Lock` so different domains run in parallel without
blocking each other.

Honours `Retry-After` if you pass it explicitly via `back_off()`.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse


class DomainPolitenessGate:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = float(min_interval_seconds)
        self._last_hit: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._cooldown_until: dict[str, float] = {}

    @staticmethod
    def domain_of(url: str) -> str:
        return (urlparse(url).netloc or "").lower()

    async def acquire(self, url: str) -> None:
        """Block until we're allowed to hit this domain."""
        domain = self.domain_of(url)
        if not domain:
            return
        async with self._locks[domain]:
            now = time.monotonic()
            # Honour explicit cooldown first
            cd = self._cooldown_until.get(domain, 0.0)
            if now < cd:
                await asyncio.sleep(cd - now)
            # Then the rolling per-domain rate-limit
            last = self._last_hit.get(domain, 0.0)
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[domain] = time.monotonic()

    def back_off(self, url: str, seconds: float) -> None:
        """Mark a domain as cooled-down for `seconds` (e.g. after a 429)."""
        domain = self.domain_of(url)
        if domain:
            self._cooldown_until[domain] = time.monotonic() + max(seconds, 0)

    def is_cool(self, url: str) -> tuple[bool, float]:
        """Return (cool_now, remaining_seconds)."""
        domain = self.domain_of(url)
        rem = self._cooldown_until.get(domain, 0.0) - time.monotonic()
        return rem <= 0, max(rem, 0.0)
