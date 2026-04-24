"""api/routes/recommendations.py — POST /v1/recommendations"""

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
# Interest → OSM tag mapping
# ---------------------------------------------------------------------------

_INTEREST_TAGS: dict[str, set[str]] = {
    "nature":        {"park", "nature_reserve", "garden", "forest", "water", "beach", "viewpoint"},
    "history":       {"historic", "museum", "monument", "memorial", "castle", "ruins", "archaeological_site"},
    "culture":       {"museum", "art_gallery", "theatre", "cinema", "library", "cultural_centre"},
    "food":          {"restaurant", "cafe", "bakery", "food_court", "marketplace", "pub"},
    "photography":   {"viewpoint", "park", "nature_reserve", "beach", "monument", "artwork"},
    "hiking":        {"park", "nature_reserve", "trail", "peak", "forest"},
    "sports":        {"sports_centre", "stadium", "pitch", "swimming_pool", "gym"},
    "shopping":      {"marketplace", "mall", "shop", "boutique"},
    "social":        {"pub", "bar", "restaurant", "cafe", "marketplace", "park"},
    "architecture":  {"historic", "castle", "cathedral", "monument", "library", "art_gallery"},
}

# Mood → interest weights (which interests to boost)
_MOOD_INTERESTS: dict[str, list[str]] = {
    "adventurous":   ["nature", "hiking", "photography", "sports"],
    "relaxed":       ["culture", "food", "architecture", "history"],
    "spontaneous":   ["food", "social", "shopping", "culture", "nature"],
    "social":        ["social", "food", "shopping"],
    "photography":   ["photography", "nature", "architecture", "history"],
}

# POI type → category tags (from OSM poi_type field)
_POI_CATEGORIES: dict[str, set[str]] = {
    "park":              {"park", "nature", "hiking", "photography"},
    "museum":            {"history", "culture", "architecture"},
    "restaurant":        {"food", "social"},
    "cafe":              {"food", "relaxed", "social"},
    "viewpoint":         {"photography", "nature"},
    "historic":          {"history", "architecture", "photography"},
    "nature_reserve":    {"nature", "hiking", "photography"},
    "art_gallery":       {"culture", "photography"},
    "pub":               {"social", "food"},
    "marketplace":       {"social", "food", "shopping"},
    "sports_centre":     {"sports"},
    "beach":             {"nature", "photography", "social"},
}


def _poi_interests(poi: dict) -> set[str]:
    poi_type = poi.get("poi_type", "").lower()
    tags     = poi.get("tags", {})
    cats: set[str] = set()

    cats |= _POI_CATEGORIES.get(poi_type, set())

    for tag_val in [tags.get("tourism", ""), tags.get("amenity", ""),
                    tags.get("leisure", ""), tags.get("historic", "")]:
        cats |= _POI_CATEGORIES.get(tag_val.lower(), set())

    return cats


def _score(poi: dict, user_interests: list[str], mood: str, light: dict, weather: dict) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []

    poi_cats     = _poi_interests(poi)
    mood_boosts  = set(_MOOD_INTERESTS.get(mood, []))

    # Interest match
    matched = [i for i in user_interests if i in poi_cats or
               any(t in poi_cats for t in _INTEREST_TAGS.get(i, set()))]
    if matched:
        score += len(matched) * 2
        reasons.append(f"matches your interest in {matched[0].replace('_', ' ')}")

    # Mood match
    mood_matched = mood_boosts & poi_cats
    if mood_matched:
        score += 1.5

    # Golden hour boost for photography-relevant spots
    if light["active"] and poi_cats & {"photography", "viewpoint", "nature"}:
        score += 3
        reasons.append(f"{light['label']} is active now")
    elif light["minutes_away"] is not None and light["minutes_away"] <= 60 and \
            poi_cats & {"photography", "viewpoint", "nature"}:
        reasons.append(f"{light['label']} in {light['minutes_away']} min")
        score += 1.5

    # Weather boost for outdoor spots
    if weather["is_clear"] and poi_cats & {"nature", "hiking", "photography", "beach", "park"}:
        score += 1
        if not reasons:
            reasons.append(f"{weather['description'].lower()} today")

    # Crowd heuristic (weekday morning = low crowd bonus)
    now = datetime.now(timezone.utc)
    if now.weekday() < 5 and now.hour < 11:
        score += 0.5
        if not reasons:
            reasons.append("low crowds this time of day")

    reason = (reasons[0].capitalize() if reasons else
              f"Popular {poi.get('poi_type', 'spot').replace('_', ' ')}")

    return score, reason


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R  = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a  = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


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
# Overpass fetch (simple, radius in metres)
# ---------------------------------------------------------------------------

_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

_QUERY = """\
[out:json][timeout:20];
nw(around:{radius},{lat},{lon})
["name"]
[~"^(tourism|amenity|leisure|historic|natural)$"~"."];
out center 100;
"""


async def _fetch_pois(lat: float, lon: float, radius_m: int) -> list[dict]:
    query = _QUERY.format(radius=radius_m, lat=lat, lon=lon)
    for mirror in _OVERPASS_MIRRORS:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(mirror, data={"data": query})
                r.raise_for_status()
                elements = r.json().get("elements", [])
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
                    poi_type = (tags.get("tourism") or tags.get("amenity") or
                                tags.get("leisure") or tags.get("historic") or
                                tags.get("natural") or "place")
                    pois.append({
                        "id":       str(el.get("id", "")),
                        "name":     name,
                        "lat":      clat,
                        "lon":      clon,
                        "poi_type": poi_type,
                        "tags":     tags,
                    })
                return pois
        except Exception as exc:
            logger.warning("overpass_failed", extra={"mirror": mirror, "error": str(exc)})
    return []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/v1/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    body: RecommendationsRequest,
    user=Depends(get_current_user),
) -> RecommendationsResponse:

    # Fetch user profile + conditions in parallel
    import asyncio
    sb = get_supabase()

    async def _profile():
        result = sb.table("profiles").select("interests").eq("user_id", str(user.id)).execute()
        return result.data[0] if result.data else {}

    profile_data, weather, pois = await asyncio.gather(
        _profile(),
        get_conditions(body.lat, body.lon),
        _fetch_pois(body.lat, body.lon, int(body.radius_km * 1000)),
    )

    user_interests: list[str] = profile_data.get("interests", [])
    light = get_light_windows(weather["sunrise_iso"], weather["sunset_iso"])

    conditions_summary = {
        "weather":        weather["description"],
        "temperature_c":  weather["temperature_c"],
        "is_clear":       weather["is_clear"],
        "light_window":   light["label"],
        "light_active":   light["active"],
        "light_mins_away": light["minutes_away"],
    }

    if not pois:
        raise HTTPException(status_code=503, detail="Could not fetch nearby places")

    # Score and rank
    scored: list[tuple[float, dict, str]] = []
    for poi in pois:
        dist_km = _haversine_km(body.lat, body.lon, poi["lat"], poi["lon"])
        if dist_km > body.radius_km:
            continue
        score, reason = _score(poi, user_interests, body.mood, light, weather)
        scored.append((score, poi, reason, dist_km))

    scored.sort(key=lambda x: x[0], reverse=True)

    cards = [
        RecommendationCard(
            id          = poi["id"],
            name        = poi["name"],
            poi_type    = poi["poi_type"],
            lat         = poi["lat"],
            lon         = poi["lon"],
            distance_km = round(dist_km, 2),
            reason      = reason,
            conditions  = conditions_summary,
            score       = round(score, 2),
        )
        for score, poi, reason, dist_km in scored[:body.limit]
    ]

    logger.info("recommendations", extra={
        "user_id":   str(user.id),
        "mood":      body.mood,
        "pois_found": len(pois),
        "cards":     len(cards),
    })

    return RecommendationsResponse(
        cards      = cards,
        mood       = body.mood,
        conditions = conditions_summary,
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
