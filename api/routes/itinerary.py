"""api/routes/itinerary.py — /v1/itinerary endpoint."""

import asyncio
import json
import logging
import re
import traceback
from datetime import date

from fastapi import APIRouter, HTTPException, Header

from api.config import settings
from api.logging_setup import correlation_id
from api.models import ItineraryRequest, ItineraryResponse, ItineraryDay, ItineraryStop

router = APIRouter()
logger = logging.getLogger("tourai.api")

# ---------------------------------------------------------------------------
# Overpass POI fetch for destination area
# ---------------------------------------------------------------------------

async def _fetch_pois(lat: float, lon: float, interests: list[str]) -> list[dict]:
    from utils.geoapify_places import fetch_pois
    from utils.poi_ranker import rank_pois
    pois = await fetch_pois(lat, lon, 5000, settings.geoapify_api_key, limit=60)
    food_types = {"restaurant", "cafe", "bar", "pub", "fast_food"}
    food       = [p for p in pois if p["poi_type"] in food_types][:8]
    attractions = [p for p in pois if p["poi_type"] not in food_types]
    ranked     = rank_pois(attractions, interests, lat, lon, limit=20, max_per_type=3)
    return ranked + food


# ---------------------------------------------------------------------------
# Profile lookup (optional — gracefully ignored if no auth)
# ---------------------------------------------------------------------------

def _get_user_profile(authorization: str | None) -> dict | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        from api.supabase_client import get_supabase
        token = authorization.removeprefix("Bearer ").strip()
        resp  = get_supabase().auth.get_user(token)
        if not resp.user:
            return None
        result = (
            get_supabase()
            .table("profiles")
            .select("interests,travel_style,pace,drive_tolerance_hrs")
            .eq("user_id", str(resp.user.id))
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LLM itinerary generation
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are TourAI, an expert travel itinerary planner. "
    "Create a realistic, day-by-day itinerary using the provided list of real nearby attractions. "
    "Choose the best stops that match the traveller's interests and pace. "
    "Return ONLY valid JSON — no markdown, no extra text."
)

_DATE_LABELS = [
    "Arrival & First Impressions", "Deeper Exploration", "Hidden Gems",
    "Culture & History", "Local Flavour", "Scenic Day", "Final Day",
]


def _build_prompt(
    destination: str,
    start_date: str,
    end_date: str,
    pois: list[dict],
    interests: list[str],
    travel_style: str,
    pace: str,
    drive_tolerance_hrs: float,
) -> str:
    d0       = date.fromisoformat(start_date)
    d1       = date.fromisoformat(end_date)
    num_days = (d1 - d0).days + 1

    poi_lines = "\n".join(
        f"- {p['name']} ({p['poi_type']})"
        + (f": {p['tags'].get('description', '')[:120]}" if p['tags'].get('description') else "")
        for p in pois[:40]
    )

    stops_per_day = {"relaxed": 2, "balanced": 3, "packed": 4}.get(pace, 3)

    return f"""Destination: {destination}
Dates: {start_date} to {end_date} ({num_days} day{"s" if num_days > 1 else ""})
Travel style: {travel_style} | Pace: {pace} ({stops_per_day} stops/day) | Max drive: {drive_tolerance_hrs}h
Interests: {', '.join(interests) if interests else 'general sightseeing'}

Nearby attractions:
{poi_lines if poi_lines else '(No specific POIs found — use your knowledge of the destination)'}

Return JSON with this structure — {stops_per_day} stops per day, as many days as needed:
{{"title":str, "summary":str, "days":[{{"date":str, "day_label":str, "stops":[{{"name":str, "poi_type":str, "tip":str, "arrival_time":str, "duration_min":int, "drive_from_prev_min":int}}]}}]}}"""


def _coerce_int(val, default: int) -> int:
    """Return val as int, stripping any trailing non-numeric chars (e.g. '60 min' → 60)."""
    try:
        return int(str(val).split()[0])
    except (ValueError, TypeError, IndexError):
        return default


def _parse_itinerary_json(text: str) -> dict:
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


async def _generate_itinerary(prompt: str) -> dict:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.groq_api_key)
    resp   = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=2000,
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    return _parse_itinerary_json(resp.choices[0].message.content)


# ---------------------------------------------------------------------------
# Drive splitting
# ---------------------------------------------------------------------------

def _apply_drive_splitting(days: list[dict], tolerance_hrs: float) -> list[dict]:
    tolerance_min = tolerance_hrs * 60
    for day in days:
        for stop in day.get("stops", []):
            if stop.get("drive_from_prev_min", 0) > tolerance_min:
                stop["overnight_warning"] = True
    return days


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/v1/itinerary", response_model=ItineraryResponse)
async def generate_itinerary(
    body: ItineraryRequest,
    authorization: str | None = Header(default=None),
) -> ItineraryResponse:
    cid = correlation_id.get("-")

    # 1. Geocode
    from utils.google_places import geocode_destination
    geo = await geocode_destination(body.destination, api_key=settings.geoapify_api_key)
    if not geo:
        raise HTTPException(status_code=422, detail=f"Could not find destination: {body.destination!r}")

    lat, lon = geo["lat"], geo["lon"]
    display  = geo["display_name"].split(",")[0].strip()

    # 2. Merge profile prefs (request body overrides profile)
    profile   = await asyncio.to_thread(_get_user_profile, authorization)
    p         = profile or {}
    interests = body.interests or p.get("interests") or []
    style     = body.travel_style or p.get("travel_style") or "solo"
    pace      = body.pace or p.get("pace") or "balanced"
    drive_tol = body.drive_tolerance_hrs if body.drive_tolerance_hrs is not None else float(p.get("drive_tolerance_hrs") or 2.0)

    # 3. Fetch POIs ranked by interests
    pois = await _fetch_pois(lat, lon, interests)
    logger.info("itinerary_pois_fetched", extra={"destination": body.destination, "count": len(pois)})

    # 4. Build prompt and generate
    prompt = _build_prompt(body.destination, body.start_date, body.end_date, pois, interests, style, pace, drive_tol)

    try:
        raw = await _generate_itinerary(prompt)
    except Exception:
        logger.error("itinerary_llm_error", extra={"exc": traceback.format_exc()})
        raise HTTPException(status_code=502, detail="Could not generate itinerary right now.")

    # 5. Apply drive splitting
    raw_days = _apply_drive_splitting(raw.get("days", []), drive_tol)

    # 6. Parse into response model
    try:
        days = [
            ItineraryDay(
                date=d["date"],
                day_label=d.get("day_label", f"Day {i+1}"),
                stops=[
                    ItineraryStop(
                        name=s["name"],
                        poi_type=s.get("poi_type", "place"),
                        tip=s.get("tip", ""),
                        arrival_time=s.get("arrival_time", ""),
                        duration_min=_coerce_int(s.get("duration_min"), 60),
                        drive_from_prev_min=_coerce_int(s.get("drive_from_prev_min"), 0),
                    )
                    for s in d.get("stops", [])
                ],
            )
            for i, d in enumerate(raw_days)
        ]
    except Exception:
        logger.error("itinerary_parse_error", extra={"exc": traceback.format_exc()})
        raise HTTPException(status_code=502, detail="Itinerary format error — please try again.")

    logger.info("itinerary_generated", extra={"destination": body.destination, "days": len(days)})

    return ItineraryResponse(
        title=raw.get("title", f"Your trip to {display}"),
        summary=raw.get("summary", ""),
        destination=display,
        start_date=body.start_date,
        end_date=body.end_date,
        days=days,
        correlation_id=cid,
    )
