"""tourai/prefetch/orchestrator.py

Stage 1: parallel prefetch of all data sources, with caching.

Key changes vs the original `_prefetch_all`:
  - Single shared httpx.AsyncClient (no per-call TLS handshake)
  - Cache layer wraps every fetcher (cache-aside pattern)
  - Distance matrix computed here, once, for all candidate POIs
  - Returns a typed PrefetchBundle, not a dict
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from cache.keys import (
    TTL,
    geocode_key,
    hotels_key,
    pois_key,
    restaurants_key,
    weather_key,
)
from cache.ttl_cache import cache
from prefetch.distance import Leg, distance_provider

logger = logging.getLogger("tourai.prefetch")


# ── Shared HTTP client ────────────────────────────────────────────────────────

_HTTP: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Module-level singleton. Wire .aclose() into FastAPI lifespan shutdown."""
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            http2=True,
        )
    return _HTTP


async def close_http_client() -> None:
    global _HTTP
    if _HTTP is not None:
        await _HTTP.aclose()
        _HTTP = None


# ── Bundle returned by Stage 1 ────────────────────────────────────────────────

@dataclass
class PrefetchBundle:
    lat: float
    lon: float
    display_name: str
    attractions: list[dict] = field(default_factory=list)   # ranked, with lat/lon
    restaurants: list[dict] = field(default_factory=list)
    hotels:      list[dict] = field(default_factory=list)
    weather:     list[dict] = field(default_factory=list)
    distance_matrix: list[list[Leg]] = field(default_factory=list)  # parallel to attractions order
    # Diagnostics
    cache_hits:  dict[str, bool] = field(default_factory=dict)


# ── Cache-aside helper ────────────────────────────────────────────────────────

async def _cached_or_fetch(key: str, ttl: int, fetch_coro_factory, label: str, hits: dict[str, bool]):
    """Wrap a fetch with cache-aside. fetch_coro_factory is a no-arg callable returning a coroutine."""
    cached = await cache.get(key)
    if cached is not None:
        hits[label] = True
        return cached
    hits[label] = False
    try:
        result = await fetch_coro_factory()
    except Exception as exc:
        logger.warning(f"Could not fetch {label!r} data during prefetch — {exc}")
        return None  # caller decides default
    if result is not None:
        await cache.set(key, result, ttl)
    return result


# ── Individual fetchers (delegate to your existing utils, but cached) ─────────

async def _geocode(destination: str, api_key: str) -> dict | None:
    from utils.google_places import geocode_destination

    key = geocode_key(destination)
    hits: dict[str, bool] = {}
    return await _cached_or_fetch(
        key,
        TTL.GEOCODE,
        lambda: geocode_destination(destination, api_key),
        "geocode",
        hits,
    )


# Extra-large cities have attractions genuinely spread across 30 km — the Strip,
# Henderson, North Las Vegas etc. are all separate areas. For most international
# and compact cities 20 km is more than enough and avoids pulling in distant
# suburban noise.
_EXTRA_LARGE_CITIES = frozenset({
    "las vegas", "los angeles", "phoenix", "houston", "dallas", "san antonio",
    "jacksonville", "fort worth", "san jose", "austin", "charlotte", "columbus",
    "indianapolis", "denver", "nashville", "oklahoma city", "el paso", "memphis",
    "seattle", "portland", "atlanta", "miami", "tampa", "orlando",
})

_DEFAULT_RADIUS      = 20_000  # good default for dense/international cities
_EXTRA_LARGE_RADIUS  = 30_000  # sprawling US metros only
_MIN_ATTRACTIONS     = 15      # trigger refetch if below this


def _poi_radius(display_name: str) -> int:
    """Return fetch radius in metres.

    Most cities (including international ones) are served well by 20 km.
    Cities in _EXTRA_LARGE_CITIES get 30 km — their points of interest are
    genuinely scattered across a much larger footprint.
    """
    if display_name.lower().split(",")[0].strip() in _EXTRA_LARGE_CITIES:
        return _EXTRA_LARGE_RADIUS
    return _DEFAULT_RADIUS


async def _attractions(lat: float, lon: float, interests: list[str], api_key: str, display_name: str = "") -> list[dict]:
    """POI fetch + interest-aware ranking. Cache the ranked result keyed by coords+interests+radius."""
    from utils.geoapify_places import fetch_pois
    from utils.poi_ranker import rank_pois

    # Radius must be computed before the cache key so the key reflects the real fetch.
    radius = _poi_radius(display_name)
    food_types = {"restaurant", "cafe", "bar", "pub", "fast_food"}

    base_key = pois_key(lat, lon, radius)
    interest_tag = ",".join(sorted(i.lower() for i in interests)) or "default"
    key = f"{base_key}:{interest_tag}"

    async def _fetch() -> list[dict]:
        raw = await fetch_pois(lat, lon, radius, api_key, limit=100)
        attractions = [p for p in raw if p["poi_type"] not in food_types]

        # Thin result — retry at 30 km (only if initial radius was smaller).
        if len(attractions) < _MIN_ATTRACTIONS and radius < _EXTRA_LARGE_RADIUS:
            logger.info(f"Only {len(attractions)} attractions found at initial radius — retrying at {_EXTRA_LARGE_RADIUS}m")
            try:
                raw2 = await fetch_pois(lat, lon, _EXTRA_LARGE_RADIUS, api_key, limit=100)
                attractions = [p for p in raw2 if p["poi_type"] not in food_types]
            except Exception as exc:
                logger.warning(f"Attraction refetch at wider radius failed — {exc}")

            if len(attractions) < _MIN_ATTRACTIONS:
                logger.warning(
                    f"Still only {len(attractions)} attractions after expanding to {_EXTRA_LARGE_RADIUS}m for {display_name!r}"
                )

        ranked = rank_pois(attractions, interests, lat, lon, limit=25, max_per_type=3)
        return [
            {
                "poi_id": f"a{idx}",
                "name": p["name"],
                "poi_type": p["poi_type"],
                "lat": p["lat"],
                "lon": p["lon"],
                "tags": p.get("tags", {}),
            }
            for idx, p in enumerate(ranked)
        ]

    _attr_hits: dict[str, bool] = {}
    result = await _cached_or_fetch(key, TTL.POIS, _fetch, "attractions", _attr_hits)
    return result if result is not None else []


async def _restaurants(lat: float, lon: float, api_key: str) -> list[dict]:
    from utils.geoapify_places import _PLACES_URL

    FOOD_CATS = "catering.restaurant,catering.cafe,catering.bar,catering.pub"
    client = get_http_client()
    resp = await client.get(
        _PLACES_URL,
        params={
            "categories": FOOD_CATS,
            "filter": f"circle:{lon},{lat},5000",
            "limit": 30,
            "apiKey": api_key,
        },
    )
    resp.raise_for_status()
    out: list[dict] = []
    for f in resp.json().get("features", []):
        p = f.get("properties", {})
        name = (p.get("name") or "").strip()
        if not name:
            continue
        coords = f.get("geometry", {}).get("coordinates", [])
        out.append({
            "name": name,
            "cuisine": p.get("datasource", {}).get("raw", {}).get("cuisine", ""),
            "lat": coords[1] if len(coords) >= 2 else lat,
            "lon": coords[0] if len(coords) >= 2 else lon,
        })
    return out[:20]


async def _hotels(lat: float, lon: float, api_key: str) -> list[dict]:
    from utils.geoapify_places import _PLACES_URL

    HOTEL_CATS = "accommodation.hotel,accommodation.guest_house,accommodation.hostel,accommodation.motel"
    client = get_http_client()
    resp = await client.get(
        _PLACES_URL,
        params={
            "categories": HOTEL_CATS,
            "filter": f"circle:{lon},{lat},4000",
            "limit": 8,
            "apiKey": api_key,
        },
    )
    resp.raise_for_status()
    out: list[dict] = []
    for f in resp.json().get("features", []):
        p = f.get("properties", {})
        name = (p.get("name") or "").strip()
        if not name:
            continue
        coords = f.get("geometry", {}).get("coordinates", [])
        out.append({
            "name": name,
            "stars": p.get("datasource", {}).get("raw", {}).get("stars", ""),
            "lat": coords[1] if len(coords) >= 2 else lat,
            "lon": coords[0] if len(coords) >= 2 else lon,
        })
    return out[:8]


async def _weather(lat: float, lon: float, dates: list[str]) -> list[dict]:
    from utils.weather import get_forecast
    return await get_forecast(lat, lon, dates)


# ── Public entry point ───────────────────────────────────────────────────────

async def prefetch_all(
    destination: str,
    dates: list[str],
    interests: list[str],
    geoapify_api_key: str,
) -> PrefetchBundle | None:
    """Geocode → fan out to all data sources in parallel → compute distance matrix.

    Returns None if geocoding fails (the only fatal prefetch error).
    All other fetchers degrade to empty lists on failure.
    """
    geo = await _geocode(destination, geoapify_api_key)
    if not geo or geo.get("lat") is None:
        return None

    lat, lon = geo["lat"], geo["lon"]
    display = (geo.get("display_name") or destination).split(",")[0].strip()

    hits: dict[str, bool] = {}

    async def _wrap(label, key, ttl, factory, default):
        result = await _cached_or_fetch(key, ttl, factory, label, hits)
        return result if result is not None else default

    attractions, restaurants, hotels, weather = await asyncio.gather(
        # Attractions has its own cache logic (interest-aware key)
        _attractions(lat, lon, interests, geoapify_api_key, display_name=display),
        _wrap("restaurants", restaurants_key(lat, lon), TTL.RESTAURANTS,
              lambda: _restaurants(lat, lon, geoapify_api_key), []),
        _wrap("hotels", hotels_key(lat, lon), TTL.HOTELS,
              lambda: _hotels(lat, lon, geoapify_api_key), []),
        _wrap("weather", weather_key(lat, lon, dates), TTL.WEATHER,
              lambda: _weather(lat, lon, dates), []),
        return_exceptions=False,  # individual fetchers already swallow their errors
    )

    # Distance matrix over attractions (Stage 2 needs this; computing here once)
    points = [(p["lat"], p["lon"]) for p in attractions]
    matrix = distance_provider.matrix(points) if points else []

    logger.info(
        f"Prefetch complete for {destination!r} — "
        f"{len(attractions)} attractions, {len(restaurants)} restaurants, "
        f"{len(hotels)} hotels, {len(weather)} days of weather",
        extra={"cache_hits": hits},
    )

    return PrefetchBundle(
        lat=lat,
        lon=lon,
        display_name=display,
        attractions=attractions,
        restaurants=restaurants,
        hotels=hotels,
        weather=weather,
        distance_matrix=matrix,
        cache_hits=hits,
    )
