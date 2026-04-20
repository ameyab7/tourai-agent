"""api/cache.py — In-memory TTL cache + cache key helpers + sweep background task."""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("tourai.api")

_VIS_GRID   = 0.001
_POI_GRID   = 0.001
_HDG_BUCKET = 22.5


class MemoryCache:
    """Async-interface TTL dict."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self.hits   = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    def sweep(self) -> int:
        """Delete all expired entries. Returns count removed."""
        now     = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if exp < now]
        for k in expired:
            del self._store[k]
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)


cache = MemoryCache()


# ── Cache key helpers ────────────────────────────────────────────────────────

def vis_cache_key(lat: float, lon: float, heading: float) -> str:
    glat = round(lat / _VIS_GRID) * _VIS_GRID
    glon = round(lon / _VIS_GRID) * _VIS_GRID
    hdg  = round(heading / _HDG_BUCKET) * _HDG_BUCKET
    return f"vis:{glat:.4f}:{glon:.4f}:{hdg:.1f}"


def poi_cache_key(lat: float, lon: float, radius: float) -> str:
    glat = round(lat / _POI_GRID) * _POI_GRID
    glon = round(lon / _POI_GRID) * _POI_GRID
    return f"poi:{glat:.4f}:{glon:.4f}:{int(radius)}"


def story_cache_key(name: str, lat: float, lon: float) -> str:
    """Stable key regardless of ID jitter — rounded to ~100m grid."""
    return f"story:{name.lower().strip()}:{round(lat, 3)}:{round(lon, 3)}"


# ── Background sweep ─────────────────────────────────────────────────────────

async def cache_sweep_loop() -> None:
    """Proactively evict expired cache entries every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        evicted = cache.sweep()
        if evicted:
            logger.info("cache_sweep", extra={"evicted": evicted})
