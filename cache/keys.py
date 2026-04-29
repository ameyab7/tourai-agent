"""tourai/cache/keys.py

Stable cache keys for each pipeline stage. Two principles:

1. Skeleton cache is keyed on (destination, date_window, interest_cluster) — NOT
   user_id. Skeletons are reusable across users with similar profiles. Narration is
   per-user; we never cache the final plan.

2. Interests are clustered into a stable bucket so two users who say
   ["food", "history"] vs ["history", "cuisine"] hit the same skeleton cache.
"""

from __future__ import annotations

import hashlib
from datetime import date


# Coarse clusters — tune these once you have usage data.
# Order within a cluster doesn't matter; presence does.
_INTEREST_CLUSTERS = {
    "food":        {"food", "cuisine", "restaurants", "culinary", "foodie", "dining"},
    "history":     {"history", "historical", "heritage", "ruins", "monuments"},
    "art":         {"art", "museums", "galleries", "architecture"},
    "nature":      {"nature", "hiking", "outdoors", "parks", "wildlife", "scenic"},
    "nightlife":   {"nightlife", "bars", "clubs", "music", "live music"},
    "shopping":    {"shopping", "markets", "boutiques"},
    "photography": {"photography", "photo spots", "viewpoints"},
    "wellness":    {"wellness", "spa", "yoga", "relaxation"},
    "family":      {"family", "kids", "children"},
    "adventure":   {"adventure", "thrill", "watersports", "climbing"},
}


def _cluster_interests(interests: list[str]) -> tuple[str, ...]:
    """Map raw interests to a sorted tuple of cluster names. Stable across re-orderings."""
    found: set[str] = set()
    for raw in interests:
        norm = raw.strip().lower()
        for cluster, members in _INTEREST_CLUSTERS.items():
            if norm in members:
                found.add(cluster)
                break
        else:
            # Unknown interest — keep it raw so we don't lose signal entirely.
            # Prefix with "x:" so it's distinguishable from cluster names.
            found.add(f"x:{norm}")
    return tuple(sorted(found))


def _normalize_dest(destination: str) -> str:
    return destination.strip().lower()


def _hash(parts: tuple) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ── Stage 1 prefetch caches (per data source, different TTLs) ────────────────

def geocode_key(destination: str) -> str:
    return f"geo:{_hash((_normalize_dest(destination),))}"


def pois_key(lat: float, lon: float, radius_m: int) -> str:
    # Round to ~1km so nearby queries share a cache entry.
    return f"pois:{round(lat, 2)}:{round(lon, 2)}:{radius_m}"


def restaurants_key(lat: float, lon: float) -> str:
    return f"rest:{round(lat, 2)}:{round(lon, 2)}"


def hotels_key(lat: float, lon: float) -> str:
    return f"hotel:{round(lat, 2)}:{round(lon, 2)}"


def weather_key(lat: float, lon: float, dates: list[str]) -> str:
    # Weather is per-day — round coords coarsely, hash the date list.
    return f"wx:{round(lat, 1)}:{round(lon, 1)}:{_hash(tuple(dates))}"


# ── Stage 2 skeleton cache (the big win) ─────────────────────────────────────

def skeleton_key(
    destination: str,
    start_date: str,
    end_date: str,
    interests: list[str],
    pace: str,
    drive_tol_hrs: float,
) -> str:
    """Reusable across users with the same destination, dates, and interest cluster.

    Note: we DO include dates because POI selection can depend on day-of-week
    (some museums close Mondays). We do NOT include style ("solo"/"couple") —
    that's a narration concern, not a selection one.
    """
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    num_days = (d1 - d0).days + 1
    weekday_start = d0.weekday()

    parts = (
        _normalize_dest(destination),
        num_days,
        weekday_start,
        _cluster_interests(interests),
        pace,
        round(drive_tol_hrs, 1),
    )
    return f"skel:{_hash(parts)}"


# ── TTLs in seconds ──────────────────────────────────────────────────────────

class TTL:
    GEOCODE     = 30 * 24 * 3600   # 30 days — places don't move
    POIS        = 7 * 24 * 3600    # 1 week — new restaurants open occasionally
    RESTAURANTS = 24 * 3600        # 1 day — closures and openings
    HOTELS      = 7 * 24 * 3600    # 1 week — hotel inventory is stable
    WEATHER     = 6 * 3600         # 6 hours — forecasts update
    SKELETON    = 3 * 24 * 3600    # 3 days — selection is stable for a destination
