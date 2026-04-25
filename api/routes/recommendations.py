"""api/routes/recommendations.py — POST /v1/recommendations"""

import asyncio
import logging
import math
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.supabase_client import get_supabase
from utils.golden_hour import get_light_windows
from utils.weather import get_conditions

router = APIRouter()
logger = logging.getLogger("tourai.api")


# ---------------------------------------------------------------------------
# Scoring tables
# ---------------------------------------------------------------------------

# OSM poi_type / tag value → interest categories
_TYPE_TO_INTERESTS: dict[str, set[str]] = {
    "park":             {"nature", "hiking", "photography", "social"},
    "nature_reserve":   {"nature", "hiking", "photography"},
    "garden":           {"nature", "photography", "relaxed"},
    "viewpoint":        {"photography", "nature", "architecture"},
    "peak":             {"hiking", "nature", "photography"},
    "beach":            {"nature", "photography", "social"},
    "museum":           {"history", "culture", "architecture"},
    "art_gallery":      {"culture", "photography", "architecture"},
    "gallery":          {"culture", "photography"},
    "theatre":          {"culture", "social"},
    "cinema":           {"culture", "social"},
    "library":          {"culture", "history"},
    "historic":         {"history", "architecture", "photography"},
    "monument":         {"history", "architecture", "photography"},
    "memorial":         {"history"},
    "castle":           {"history", "architecture", "photography"},
    "ruins":            {"history", "photography"},
    "archaeological_site": {"history"},
    "restaurant":       {"food", "social"},
    "cafe":             {"food", "social"},
    "bakery":           {"food"},
    "pub":              {"social", "food"},
    "bar":              {"social"},
    "marketplace":      {"food", "social", "shopping"},
    "mall":             {"shopping"},
    "sports_centre":    {"sports"},
    "stadium":          {"sports", "social"},
    "swimming_pool":    {"sports"},
    "pitch":            {"sports"},
    "attraction":       {"culture", "photography"},
    "artwork":          {"culture", "photography", "architecture"},
}

# Mood → which interests to boost for scoring
_MOOD_BOOSTS: dict[str, set[str]] = {
    "adventurous":   {"nature", "hiking", "sports", "photography"},
    "relaxed":       {"culture", "history", "food", "architecture"},
    "spontaneous":   {"food", "social", "shopping", "culture", "nature"},
    "social":        {"social", "food", "shopping"},
    "photography":   {"photography", "nature", "architecture", "history"},
}

# Interests that benefit from golden hour / good light
_LIGHT_SENSITIVE = {"photography", "nature", "architecture"}

# Interests that benefit from clear weather
_OUTDOOR_INTERESTS = {"nature", "hiking", "photography", "sports"}


def _poi_interests(poi: dict) -> set[str]:
    """Derive interest categories from a POI's type and OSM tags."""
    cats: set[str] = set()
    poi_type = poi.get("poi_type", "").lower()
    cats |= _TYPE_TO_INTERESTS.get(poi_type, set())

    tags = poi.get("tags", {})
    for key in ("tourism", "amenity", "leisure", "historic", "natural"):
        val = tags.get(key, "").lower()
        if val:
            cats |= _TYPE_TO_INTERESTS.get(val, set())

    return cats


def _score_poi(
    poi: dict,
    user_interests: list[str],
    mood: str,
    light: dict,
    weather: dict,
) -> tuple[float, list[str]]:
    """Return (score, reason_parts) for a single POI."""
    score   = 0.0
    reasons: list[str] = []
    cats    = _poi_interests(poi)
    mood_boosts = _MOOD_BOOSTS.get(mood, set())

    # --- Interest match (+2 per matched interest) ---
    matched = [i for i in user_interests if i in cats]
    if matched:
        score += len(matched) * 2.0
        label  = matched[0].replace("_", " ")
        reasons.append(f"matches your interest in {label}")

    # --- Mood alignment (+1.5) ---
    if cats & mood_boosts:
        score += 1.5
        if not matched:
            # Only add mood reason if no interest reason already covers it
            reasons.append(f"great for a {mood} mood")

    # --- Light conditions ---
    if cats & _LIGHT_SENSITIVE:
        if light["active"]:
            score += 3.0
            reasons.append(f"{light['label']} is happening right now")
        elif light["minutes_away"] is not None and light["minutes_away"] <= 60:
            score += 1.5
            reasons.append(f"{light['label']} in {light['minutes_away']} min")

    # --- Clear weather boost for outdoor spots ---
    if weather["is_clear"] and cats & _OUTDOOR_INTERESTS:
        score += 1.0
        if len(reasons) == 0:
            reasons.append(f"{weather['description'].lower()} — perfect conditions")

    # --- Low-crowd heuristic (weekday before 11 AM) ---
    now = datetime.now(timezone.utc)
    if now.weekday() < 5 and now.hour < 11:
        score += 0.5
        if not reasons:
            reasons.append("low crowds this time of day")

    # --- Distance penalty (slight preference for closer places) ---
    dist_km = poi.get("distance_km", 0)
    score  -= dist_km * 0.05

    return score, reasons


def _build_reason(poi: dict, reasons: list[str]) -> str:
    if not reasons:
        poi_type = poi.get("poi_type", "spot").replace("_", " ")
        return f"Popular {poi_type} nearby"
    # Capitalise and join up to 2 reasons cleanly
    parts = reasons[:2]
    parts[0] = parts[0].capitalize()
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RecommendationsRequest(BaseModel):
    lat:       float = Field(..., ge=-90,  le=90)
    lon:       float = Field(..., ge=-180, le=180)
    mood:      str   = Field(..., pattern="^(adventurous|relaxed|spontaneous|social|photography)$")
    radius_km: float = Field(5.0, gt=0, le=50)
    limit:     int   = Field(10, ge=1, le=30)


class RecommendationCard(BaseModel):
    id:           str
    name:         str
    poi_type:     str
    lat:          float
    lon:          float
    distance_km:  float
    reason:       str
    conditions:   dict
    score:        float


class RecommendationsResponse(BaseModel):
    cards:       list[RecommendationCard]
    mood:        str
    conditions:  dict
    timestamp:   str


# ---------------------------------------------------------------------------
# Overpass fetch
# ---------------------------------------------------------------------------

_OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# [timeout:12] so each mirror gives up before our per-mirror httpx timeout
_QUERY = """\
[out:json][timeout:12];
nw(around:{radius},{lat},{lon})
["name"]
[~"^(tourism|amenity|leisure|historic|natural)$"~"."];
out center 100;
"""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R   = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ  = math.radians(lat2 - lat1)
    dλ  = math.radians(lon2 - lon1)
    a   = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _parse_elements(elements: list) -> list[dict]:
    pois = []
    for el in elements:
        name = el.get("tags", {}).get("name", "").strip()
        if not name:
            continue
        clat = el.get("lat") or el.get("center", {}).get("lat")
        clon = el.get("lon") or el.get("center", {}).get("lon")
        if not clat or not clon:
            continue
        tags     = el.get("tags", {})
        poi_type = (
            tags.get("tourism") or tags.get("amenity") or
            tags.get("leisure") or tags.get("historic") or
            tags.get("natural") or "place"
        )
        pois.append({
            "id":       str(el.get("id", "")),
            "name":     name,
            "lat":      clat,
            "lon":      clon,
            "poi_type": poi_type,
            "tags":     tags,
        })
    return pois


async def _try_mirror(mirror: str, query: str) -> list[dict]:
    """Attempt one Overpass mirror. Raises on failure so gather can skip it."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            mirror,
            data={"data": query},
            headers={"Accept": "application/json"},
        )
        if r.status_code not in (200, 201):
            raise ValueError(f"HTTP {r.status_code}")
        pois = _parse_elements(r.json().get("elements", []))
        logger.info("overpass_ok", extra={"mirror": mirror, "pois": len(pois)})
        return pois


async def _fetch_pois(lat: float, lon: float, radius_m: int) -> list[dict]:
    """Fire all mirrors in parallel; return the first successful result."""
    query = _QUERY.format(radius=radius_m, lat=lat, lon=lon)

    tasks = {
        asyncio.ensure_future(_try_mirror(m, query)): m
        for m in _OVERPASS_MIRRORS
    }
    pending = set(tasks)

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for fut in done:
                exc = fut.exception()
                if exc is None:
                    # Cancel remaining in-flight requests
                    for p in pending:
                        p.cancel()
                    return fut.result()
                logger.warning("overpass_mirror_failed", extra={
                    "mirror": tasks[fut], "error": str(exc),
                })
    finally:
        for p in pending:
            p.cancel()

    logger.warning("overpass_all_mirrors_failed")
    return []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/v1/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    body: RecommendationsRequest,
    user=Depends(get_current_user),
) -> RecommendationsResponse:

    sb = get_supabase()

    async def _profile():
        result = (
            sb.table("profiles")
            .select("interests,travel_style,pace")
            .eq("user_id", str(user.id))
            .execute()
        )
        return result.data[0] if result.data else {}

    profile_data, weather, raw_pois = await asyncio.gather(
        _profile(),
        get_conditions(body.lat, body.lon),
        _fetch_pois(body.lat, body.lon, int(body.radius_km * 1000)),
    )

    # Gracefully return empty cards when all Overpass mirrors fail
    if not raw_pois:
        logger.warning("recommendations_no_pois", extra={"lat": body.lat, "lon": body.lon})
        light = get_light_windows(
            weather.get("sunrise_iso", ""), weather.get("sunset_iso", "")
        )
        return RecommendationsResponse(
            cards=[],
            mood=body.mood,
            conditions={
                "weather":         weather.get("description", ""),
                "temperature_c":   weather.get("temperature_c", 0),
                "is_clear":        weather.get("is_clear", False),
                "light_window":    light["label"],
                "light_active":    light["active"],
                "light_mins_away": light["minutes_away"],
            },
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    user_interests: list[str] = profile_data.get("interests") or []
    light = get_light_windows(weather["sunrise_iso"], weather["sunset_iso"])

    conditions_summary = {
        "weather":         weather["description"],
        "temperature_c":   weather["temperature_c"],
        "is_clear":        weather["is_clear"],
        "light_window":    light["label"],
        "light_active":    light["active"],
        "light_mins_away": light["minutes_away"],
    }

    # Attach distances, filter to radius, deduplicate by name
    seen_names: set[str] = set()
    candidates: list[dict] = []
    for poi in raw_pois:
        dist_km = _haversine_km(body.lat, body.lon, poi["lat"], poi["lon"])
        if dist_km > body.radius_km:
            continue
        name_key = poi["name"].lower().strip()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        candidates.append({**poi, "distance_km": dist_km})

    if not candidates:
        return RecommendationsResponse(
            cards=[],
            mood=body.mood,
            conditions=conditions_summary,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Score and sort
    scored: list[tuple[float, dict, str]] = []
    for poi in candidates:
        score, reasons = _score_poi(poi, user_interests, body.mood, light, weather)
        reason = _build_reason(poi, reasons)
        scored.append((score, poi, reason))

    scored.sort(key=lambda x: x[0], reverse=True)

    cards = [
        RecommendationCard(
            id          = poi["id"],
            name        = poi["name"],
            poi_type    = poi["poi_type"],
            lat         = poi["lat"],
            lon         = poi["lon"],
            distance_km = round(poi["distance_km"], 2),
            reason      = reason,
            conditions  = conditions_summary,
            score       = round(score, 2),
        )
        for score, poi, reason in scored[:body.limit]
    ]

    logger.info("recommendations_ok", extra={
        "user_id":    str(user.id),
        "mood":       body.mood,
        "pois_found": len(raw_pois),
        "candidates": len(candidates),
        "cards":      len(cards),
    })

    return RecommendationsResponse(
        cards      = cards,
        mood       = body.mood,
        conditions = conditions_summary,
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
