"""tourai/storage/plan_store.py

Plan persistence with TTL semantics. Mirrors the InProcessTTLCache pattern
but exposes a domain-typed interface rather than a generic key/value cache.

Default TTL is 30 days — plans are large; eviction keeps memory bounded.
Swap to Redis by replacing PlanStore with a Redis-backed implementation
that satisfies the same interface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from pydantic import BaseModel

from api.models import ItineraryRequest
from prefetch.distance import Leg
from prefetch.orchestrator import PrefetchBundle

logger = logging.getLogger("tourai.storage")

_PLAN_TTL = 30 * 24 * 3600  # 30 days


# ── Bundle serialization ──────────────────────────────────────────────────────

def _serialize_bundle(b: PrefetchBundle) -> dict:
    """Convert PrefetchBundle → JSON-serializable dict."""
    return {
        "lat": b.lat,
        "lon": b.lon,
        "display_name": b.display_name,
        "attractions": b.attractions,
        "restaurants": b.restaurants,
        "hotels": b.hotels,
        "weather": b.weather,
        "distance_matrix": [
            [[leg.km, leg.walking_min, leg.driving_min] for leg in row]
            for row in b.distance_matrix
        ],
        "cache_hits": b.cache_hits,
    }


def _deserialize_bundle(d: dict) -> PrefetchBundle:
    """Reconstruct PrefetchBundle from a serialized dict."""
    return PrefetchBundle(
        lat=d["lat"],
        lon=d["lon"],
        display_name=d["display_name"],
        attractions=d["attractions"],
        restaurants=d["restaurants"],
        hotels=d["hotels"],
        weather=d["weather"],
        distance_matrix=[
            [Leg(km=float(e[0]), walking_min=int(e[1]), driving_min=int(e[2])) for e in row]
            for row in d["distance_matrix"]
        ],
        cache_hits=d.get("cache_hits", {}),
    )


# ── Snapshot model ────────────────────────────────────────────────────────────

class PlanSnapshot(BaseModel):
    plan_id: str
    user_id: str | None
    created_at: datetime
    request: ItineraryRequest
    skeleton_dict: dict
    bundle_dict: dict
    final_plan: dict  # FinalPlan.model_dump()


# ── Store ─────────────────────────────────────────────────────────────────────

class PlanStore:
    """Async-safe in-process store for PlanSnapshot objects.

    NOT safe across multiple uvicorn workers — each worker has its own dict.
    Swap to Redis when you scale horizontally.
    """

    def __init__(self, ttl_seconds: int = _PLAN_TTL) -> None:
        self._store: dict[str, tuple[float, PlanSnapshot]] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def save(self, plan_id: str, snapshot: PlanSnapshot) -> None:
        expires_at = time.monotonic() + self._ttl
        async with self._lock:
            self._store[plan_id] = (expires_at, snapshot)
        logger.info("plan_saved", extra={"plan_id": plan_id})

    async def load(self, plan_id: str) -> PlanSnapshot | None:
        async with self._lock:
            entry = self._store.get(plan_id)
            if entry is None:
                return None
            expires_at, snapshot = entry
            if time.monotonic() >= expires_at:
                self._store.pop(plan_id, None)
                return None
            return snapshot

    async def delete(self, plan_id: str) -> None:
        async with self._lock:
            self._store.pop(plan_id, None)
        logger.info("plan_deleted", extra={"plan_id": plan_id})


# Module-level singleton. Replace via app lifespan if you move to Redis.
plan_store: PlanStore = PlanStore()
