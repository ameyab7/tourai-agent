"""api/routes/recommendations.py — POST /v1/recommendations"""

import asyncio
import json
import logging
import math
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.config import settings
from api.supabase_client import get_supabase
from utils.golden_hour import get_light_windows
from utils.weather import get_conditions

router = APIRouter()
logger = logging.getLogger("tourai.api")


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
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
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
                r = await client.post(
                    mirror,
                    data={"data": query},
                    headers={"Accept": "application/json"},
                )
                if r.status_code in (406, 429, 500, 502, 503, 504):
                    logger.warning("overpass_mirror_skipped", extra={"mirror": mirror, "status": r.status_code})
                    continue
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
# LLM ranking
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are TourAI's recommendation engine. Given a user's travel profile, current mood, "
    "local conditions, and a list of nearby places, select and rank the best places for this person "
    "right now. For each selected place write one vivid, specific sentence explaining exactly why it's "
    "a great match — reference their interests, mood, or current conditions where relevant. "
    "Be specific and personal, not generic. Never say 'this place is great' — say WHY it's great for THIS person NOW."
)


def _build_prompt(profile: dict, mood: str, conditions: dict, pois: list[dict], limit: int) -> str:
    interests    = profile.get("interests", [])
    travel_style = profile.get("travel_style", "")
    pace         = profile.get("pace", "")

    light_str = ""
    if conditions["light_window"]:
        if conditions["light_active"]:
            light_str = f"{conditions['light_window']} is active right now."
        elif conditions["light_mins_away"] and conditions["light_mins_away"] <= 90:
            light_str = f"{conditions['light_window']} in {conditions['light_mins_away']} minutes."

    poi_lines = "\n".join(
        f"- id={p['id']} | {p['name']} ({p['poi_type']}) | {p['distance_km']:.2f}km away"
        + (f" | tags: {', '.join(f'{k}={v}' for k, v in list(p['tags'].items())[:4])}" if p['tags'] else "")
        for p in pois
    )

    return f"""User profile:
- Interests: {', '.join(interests) or 'not specified'}
- Travel style: {travel_style or 'not specified'}
- Pace: {pace or 'not specified'}
- Current mood: {mood}

Current conditions:
- Weather: {conditions['weather']}, {conditions['temperature_c']}°C
- {light_str or 'No special light window right now.'}

Nearby places:
{poi_lines}

Return a JSON array of the top {limit} places for this person right now, ordered best-first.
Each item: {{"id": "<id>", "reason": "<one vivid sentence>"}}
Return only valid JSON, no markdown, no explanation."""


async def _rank_with_llm(profile: dict, mood: str, conditions: dict, pois: list[dict], limit: int) -> list[dict]:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.groq_api_key)
    prompt = _build_prompt(profile, mood, conditions, pois, limit)

    completion = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=600,
        temperature=0.5,
    )

    raw = completion.choices[0].message.content.strip()
    # Strip markdown code fences if model wraps in ```json
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


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
        result = sb.table("profiles").select("interests,travel_style,pace").eq("user_id", str(user.id)).execute()
        return result.data[0] if result.data else {}

    profile_data, weather, pois = await asyncio.gather(
        _profile(),
        get_conditions(body.lat, body.lon),
        _fetch_pois(body.lat, body.lon, int(body.radius_km * 1000)),
    )

    light = get_light_windows(weather["sunrise_iso"], weather["sunset_iso"])

    conditions_summary = {
        "weather":         weather["description"],
        "temperature_c":   weather["temperature_c"],
        "is_clear":        weather["is_clear"],
        "light_window":    light["label"],
        "light_active":    light["active"],
        "light_mins_away": light["minutes_away"],
    }

    if not pois:
        raise HTTPException(status_code=503, detail="Could not fetch nearby places")

    # Attach distance, filter to radius, take 30 closest for LLM context
    candidates = []
    for poi in pois:
        dist_km = _haversine_km(body.lat, body.lon, poi["lat"], poi["lon"])
        if dist_km <= body.radius_km:
            candidates.append({**poi, "distance_km": dist_km})

    candidates.sort(key=lambda p: p["distance_km"])
    candidates = candidates[:30]

    if not candidates:
        raise HTTPException(status_code=503, detail="No places found within radius")

    # LLM ranks and reasons
    try:
        ranked = await _rank_with_llm(profile_data, body.mood, conditions_summary, candidates, body.limit)
    except Exception as exc:
        logger.error("llm_ranking_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail="Could not rank recommendations right now")

    # Build final card list using LLM ordering + reasons
    poi_by_id = {p["id"]: p for p in candidates}
    cards = []
    for item in ranked:
        poi = poi_by_id.get(str(item.get("id", "")))
        if not poi:
            continue
        cards.append(RecommendationCard(
            id          = poi["id"],
            name        = poi["name"],
            poi_type    = poi["poi_type"],
            lat         = poi["lat"],
            lon         = poi["lon"],
            distance_km = round(poi["distance_km"], 2),
            reason      = item.get("reason", ""),
            conditions  = conditions_summary,
        ))

    logger.info("recommendations", extra={
        "user_id":    str(user.id),
        "mood":       body.mood,
        "pois_found": len(pois),
        "candidates": len(candidates),
        "cards":      len(cards),
    })

    return RecommendationsResponse(
        cards      = cards,
        mood       = body.mood,
        conditions = conditions_summary,
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
