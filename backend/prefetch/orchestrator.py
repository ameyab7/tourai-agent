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

from cache_module.keys import (
    TTL,
    geocode_key,
    hotels_key,
    pois_key,
    restaurants_key,
    weather_key,
)
from cache_module.ttl_cache import cache
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
class AttractionsFetchDiagnostic:
    initial_count: int = 0
    initial_radius: int = 0
    did_refetch: bool = False
    refetch_count: int = 0
    refetch_radius: int = 0
    final_count: int = 0
    refetch_outcome: str = ""


def dataclass_as_dict(obj) -> dict:
    """Convert dataclass to dict, handling nested dataclasses."""
    result = {}
    for k, v in obj.__dict__.items():
        if hasattr(v, "__dataclass_fields__"):
            result[k] = dataclass_as_dict(v)
        elif isinstance(v, (list, tuple)):
            result[k] = [
                dataclass_as_dict(i) if hasattr(i, "__dataclass_fields__") else i
                for i in v
            ]
        else:
            result[k] = v
    return result


@dataclass
class FetchResult:
    data: list
    failed: bool = False
    error: str | None = None
    diagnostic: AttractionsFetchDiagnostic | None = None


@dataclass
class DistanceMatrixIndex:
    attractions_start: int = 0
    attractions_count: int = 0
    restaurants_start: int = 0
    restaurants_count: int = 0
    hotels_start: int = 0
    hotels_count: int = 0


@dataclass
class PrefetchBundle:
    lat: float
    lon: float
    display_name: str
    attractions: list[dict] = field(default_factory=list)   # ranked, with lat/lon
    restaurants: list[dict] = field(default_factory=list)
    hotels:      list[dict] = field(default_factory=list)
    weather:     list[dict] = field(default_factory=list)
    distance_matrix: list[list[Leg]] = field(default_factory=list)
    matrix_index: DistanceMatrixIndex = field(default_factory=DistanceMatrixIndex)
    # Diagnostics
    cache_hits:  dict[str, bool] = field(default_factory=dict)
    fetch_errors: dict[str, str] = field(default_factory=dict)


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


async def _cached_or_fetch_conditional(
    key: str,
    ttl: int,
    fetch_coro_factory,
    label: str,
    hits: dict[str, bool],
    should_cache: callable = None,
):
    """Like _cached_or_fetch but with conditional caching (e.g., skip ambiguous geocodes)."""
    cached = await cache.get(key)
    if cached is not None:
        hits[label] = True
        return cached
    hits[label] = False
    try:
        result = await fetch_coro_factory()
    except Exception as exc:
        logger.warning(f"Could not fetch {label!r} data during prefetch — {exc}")
        return None
    if result is not None:
        if should_cache is None or should_cache(result):
            await cache.set(key, result, ttl)
    return result


# ── Individual fetchers (delegate to your existing utils, but cached) ─────────

async def _geocode(destination: str, api_key: str) -> dict | None:
    from utils.google_places import geocode_destination

    key = geocode_key(destination)
    hits: dict[str, bool] = {}

    def _should_cache_geocode(result: dict) -> bool:
        return not result.get("ambiguous", False)

    return await _cached_or_fetch_conditional(
        key,
        TTL.GEOCODE,
        lambda: geocode_destination(destination, api_key),
        "geocode",
        hits,
        should_cache=_should_cache_geocode,
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

_RESTAURANT_DEFAULT_RADIUS = 8_000    # 8km — captures most food scenes
_RESTAURANT_EXTRA_LARGE_RADIUS = 12_000  # 12km — Las Vegas Strip, LA neighborhoods
_HOTEL_DEFAULT_RADIUS = 6_000      # 6km — reasonable hotel search area around center
_HOTEL_EXTRA_LARGE_RADIUS = 10_000  # 10km — sprawling metros with multiple hotel areas


def _is_extra_large_city(display_name: str) -> bool:
    if not display_name:
        return False
    return display_name.lower().split(",")[0].strip() in _EXTRA_LARGE_CITIES


def _poi_radius(display_name: str) -> int:
    """Return fetch radius in metres.

    Most cities (including international ones) are served well by 20 km.
    Cities in _EXTRA_LARGE_CITIES get 30 km — their points of interest are
    genuinely scattered across a much larger footprint.
    """
    if _is_extra_large_city(display_name):
        return _EXTRA_LARGE_RADIUS
    return _DEFAULT_RADIUS


def _restaurant_radius(display_name: str) -> int:
    """Restaurants need wider radius than attractions for tourist areas.
    Las Vegas Strip is 6-8km from downtown geocode. Dallas Uptown is 5km from center.
    """
    if _is_extra_large_city(display_name):
        return _RESTAURANT_EXTRA_LARGE_RADIUS
    return _RESTAURANT_DEFAULT_RADIUS


def _hotel_radius(display_name: str) -> int:
    """Hotels can be spread across a metro area. Search wider to capture options."""
    if _is_extra_large_city(display_name):
        return _HOTEL_EXTRA_LARGE_RADIUS
    return _HOTEL_DEFAULT_RADIUS


async def _attractions(lat: float, lon: float, interests: list[str], api_key: str, display_name: str = "") -> FetchResult:
    """POI fetch + interest-aware ranking. Cache the ranked result keyed by coords+interests+radius."""
    from utils.geoapify_places import fetch_pois
    from utils.poi_ranker import rank_pois

    diagnostic = AttractionsFetchDiagnostic()
    radius = _poi_radius(display_name)
    food_types = {"restaurant", "cafe", "bar", "pub", "fast_food"}

    base_key = pois_key(lat, lon, radius)
    interest_tag = ",".join(sorted(i.lower() for i in interests)) or "default"
    key = f"{base_key}:{interest_tag}"

    async def _fetch() -> list[dict]:
        nonlocal diagnostic, radius

        raw = await fetch_pois(lat, lon, radius, api_key, limit=100)
        diagnostic.initial_count = len(raw)
        diagnostic.initial_radius = radius

        attractions = [p for p in raw if p["poi_type"] not in food_types]
        attraction_count = len(attractions)

        if attraction_count < _MIN_ATTRACTIONS and radius < _EXTRA_LARGE_RADIUS:
            diagnostic.did_refetch = True
            diagnostic.refetch_radius = _EXTRA_LARGE_RADIUS

            logger.info(
                f"POI diagnostic: {display_name!r} initial={attraction_count} at {radius}m — refetching at {_EXTRA_LARGE_RADIUS}m"
            )

            try:
                raw2 = await fetch_pois(lat, lon, _EXTRA_LARGE_RADIUS, api_key, limit=100)
                diagnostic.refetch_count = len(raw2)
                attractions = [p for p in raw2 if p["poi_type"] not in food_types]
                attraction_count = len(attractions)

                if attraction_count >= _MIN_ATTRACTIONS:
                    diagnostic.refetch_outcome = "success_radius"
                    radius = _EXTRA_LARGE_RADIUS
                else:
                    diagnostic.refetch_outcome = "still_low_radius"
            except Exception as exc:
                diagnostic.refetch_outcome = "refetch_failed"
                logger.warning(f"POI refetch failed for {display_name!r}: {exc}")
        else:
            diagnostic.refetch_outcome = "no_refetch_needed"
            diagnostic.final_count = attraction_count
            if attraction_count < _MIN_ATTRACTIONS and radius == _DEFAULT_RADIUS:
                logger.info(
                    f"POI diagnostic: {display_name!r} small destination — only {attraction_count} POIs at default radius"
                )

        ranked = rank_pois(attractions, interests, lat, lon, limit=25, max_per_type=3)
        diagnostic.final_count = len(ranked)

        logger.info(
            f"POI diagnostic: {display_name!r} outcome={diagnostic.refetch_outcome} final={diagnostic.final_count}",
            extra={"diagnostic": dataclass_as_dict(diagnostic)},
        )

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
    if result is not None:
        return FetchResult(data=result, failed=False, diagnostic=diagnostic)
    return FetchResult(data=[], failed=True, error="fetch failed", diagnostic=diagnostic)


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
    return FetchResult(data=out[:20], failed=False)


async def _restaurants(lat: float, lon: float, api_key: str, display_name: str = "") -> FetchResult:
    try:
        from utils.geoapify_places import _PLACES_URL
        radius = _restaurant_radius(display_name)
        FOOD_CATS = "catering.restaurant,catering.cafe,catering.bar,catering.pub"
        client = get_http_client()
        resp = await client.get(
            _PLACES_URL,
            params={
                "categories": FOOD_CATS,
                "filter": f"circle:{lon},{lat},{radius}",
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
        logger.info(f"Restaurants: {len(out)} found within {radius}m for {display_name!r}")
        return FetchResult(data=out[:20], failed=False)
    except Exception as exc:
        logger.warning(f"Restaurant fetch failed: {exc}")
        return FetchResult(data=[], failed=True, error=str(exc))


async def _hotels(lat: float, lon: float, api_key: str, display_name: str = "") -> FetchResult:
    try:
        from utils.geoapify_places import _PLACES_URL
        radius = _hotel_radius(display_name)
        HOTEL_CATS = "accommodation.hotel,accommodation.guest_house,accommodation.hostel,accommodation.motel"
        client = get_http_client()
        resp = await client.get(
            _PLACES_URL,
            params={
                "categories": HOTEL_CATS,
                "filter": f"circle:{lon},{lat},{radius}",
                "limit": 10,
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
        logger.info(f"Hotels: {len(out)} found within {radius}m for {display_name!r}")
        return FetchResult(data=out[:8], failed=False)
    except Exception as exc:
        logger.warning(f"Hotel fetch failed: {exc}")
        return FetchResult(data=[], failed=True, error=str(exc))


async def _weather(lat: float, lon: float, dates: list[str]) -> FetchResult:
    try:
        from utils.weather import get_forecast
        data = await get_forecast(lat, lon, dates)
        return FetchResult(data=data, failed=False)
    except Exception as exc:
        logger.warning(f"Weather fetch failed: {exc}")
        return FetchResult(data=[], failed=True, error=str(exc))


# ── Public entry point ───────────────────────────────────────────────────────

class AmbiguousDestinationError(Exception):
    """Raised when geocoding returns multiple valid results for an ambiguous destination."""
    def __init__(self, options: list[dict], original_query: str):
        self.options = options
        self.original_query = original_query
        super().__init__(f"Ambiguous destination: {original_query}")


async def prefetch_all(
    destination: str,
    dates: list[str],
    interests: list[str],
    geoapify_api_key: str,
) -> PrefetchBundle | None:
    """Two-stage prefetch: hotels first (to anchor restaurant search), then rest.

    Stage 1: Geocode → fetch hotels around destination center → pick best hotel
    Stage 2: Fetch restaurants around picked hotel (not destination center)
             Fetch attractions around destination center
             Fetch weather

    Returns None if geocoding fails (the only fatal prefetch error).
    Raises AmbiguousDestinationError if disambiguation is needed.
    """
    geo = await _geocode(destination, geoapify_api_key)

    if geo and geo.get("ambiguous") and geo.get("disambiguation"):
        raise AmbiguousDestinationError(
            options=geo["disambiguation"],
            original_query=destination,
        )

    if not geo or geo.get("lat") is None:
        logger.warning(f"Geocoding failed for destination: {destination!r}")
        return None

    if geo.get("warning"):
        logger.warning(f"Geocode warning for {destination!r}: {geo['warning']}")

    dest_lat, dest_lon = geo["lat"], geo["lon"]
    display = (geo.get("display_name") or destination).split(",")[0].strip()

    fetch_errors: dict[str, str] = {}

    # ── Stage 1: fetch hotels around destination center, pick best ─────────────
    hotels_res = await _hotels(dest_lat, dest_lon, geoapify_api_key, display_name=display)
    hotels = hotels_res.data

    if hotels_res.failed:
        fetch_errors["hotels"] = hotels_res.error or "unknown"
        logger.warning(f"Hotels fetch failed: {fetch_errors['hotels']}")

    picked_hotel = None
    hotel_lat, hotel_lon = dest_lat, dest_lon
    if hotels:
        rated = [h for h in hotels if str(h.get("stars", "")).strip().isdigit()]
        if rated:
            picked_hotel = max(rated, key=lambda h: int(h["stars"]))
        else:
            picked_hotel = hotels[0]
        if picked_hotel:
            hotel_lat = picked_hotel.get("lat", dest_lat)
            hotel_lon = picked_hotel.get("lon", dest_lon)
            logger.info(f"Picked hotel: {picked_hotel['name']} at ({hotel_lat}, {hotel_lon})")

    # ── Stage 2: parallel fetch of attractions, restaurants, weather ────────────
    attractions_res, restaurants_res, weather_res = await asyncio.gather(
        _attractions(dest_lat, dest_lon, interests, geoapify_api_key, display_name=display),
        _restaurants(hotel_lat, hotel_lon, geoapify_api_key, display_name=display),
        _weather(dest_lat, dest_lon, dates),
        return_exceptions=False,
    )

    attractions = attractions_res.data
    if attractions_res.failed:
        fetch_errors["attractions"] = attractions_res.error or "unknown"
        logger.warning(f"Attractions fetch failed: {fetch_errors['attractions']}")

    restaurants = restaurants_res.data
    if restaurants_res.failed:
        fetch_errors["restaurants"] = restaurants_res.error or "unknown"
        logger.warning(f"Restaurants fetch failed: {fetch_errors['restaurants']}")

    weather = weather_res.data
    if weather_res.failed:
        fetch_errors["weather"] = weather_res.error or "unknown"
        logger.warning(f"Weather fetch failed: {fetch_errors['weather']}")

    if fetch_errors:
        logger.warning(f"Partial prefetch failures for {destination!r}: {fetch_errors}")

    # Build distance matrix
    all_points = []
    all_points.extend([(p["lat"], p["lon"]) for p in attractions])
    all_points.extend([(p["lat"], p["lon"]) for p in restaurants])
    all_points.extend([(p["lat"], p["lon"]) for p in hotels])

    matrix = distance_provider.matrix(all_points) if all_points else []

    idx = DistanceMatrixIndex(
        attractions_start=0,
        attractions_count=len(attractions),
        restaurants_start=len(attractions),
        restaurants_count=len(restaurants),
        hotels_start=len(attractions) + len(restaurants),
        hotels_count=len(hotels),
    )

    logger.info(
        f"Prefetch complete for {destination!r} — "
        f"{len(attractions)} attractions, {len(restaurants)} restaurants around hotel, "
        f"{len(hotels)} hotels, {len(weather)} days of weather",
        extra={"fetch_errors": fetch_errors, "matrix_points": len(all_points), "hotel_center": (hotel_lat, hotel_lon)},
    )

    return PrefetchBundle(
        lat=dest_lat,
        lon=dest_lon,
        display_name=display,
        attractions=attractions,
        restaurants=restaurants,
        hotels=hotels,
        weather=weather,
        distance_matrix=matrix,
        matrix_index=idx,
        cache_hits={},
        fetch_errors=fetch_errors,
    )
