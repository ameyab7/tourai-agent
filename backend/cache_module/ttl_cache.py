"""tourai/cache/ttl_cache.py

Async-safe TTL cache with a Protocol that lets us swap to Redis without touching callers.
Singleton instance is created at module import; FastAPI lifespan can replace it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol


class CacheBackend(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...


class InProcessTTLCache:
    """Thread-safe in-process cache. Good for single-worker dev and small prod.

    NOT safe across multiple uvicorn workers — each worker has its own copy.
    Swap to Redis when you scale horizontally.
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_entries = max_entries

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                # Lazy eviction
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires_at = time.monotonic() + ttl_seconds
        async with self._lock:
            # Crude bounded eviction: if over cap, drop oldest 10% by expiry.
            if len(self._store) >= self._max_entries:
                victims = sorted(self._store.items(), key=lambda kv: kv[1][0])
                for k, _ in victims[: self._max_entries // 10]:
                    self._store.pop(k, None)
            self._store[key] = (expires_at, value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)


# Module-level singleton. Replace via app lifespan if you go to Redis.
cache: CacheBackend = InProcessTTLCache()
