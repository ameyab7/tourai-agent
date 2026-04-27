"""api/routes/itinerary_agent.py — Pre-fetch + single reasoning-model call.

Architecture:
  All data (attractions, restaurants, hotels, weather, golden hour) is fetched
  server-side in parallel with asyncio.gather — no LLM needed for that.
  One call to gpt-oss-120b gives it everything and it reasons through the plan.

  Flow:
    Step 1 — parallel pre-fetch (free, ~2-3 s)
    Step 2 — single deepseek-r1 call with all data in context → JSON plan
"""

import asyncio
import json
import logging
import re
import traceback
from datetime import date, timedelta
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.config import settings
from api.models import ItineraryRequest
from utils.geoapify_places import _PLACES_URL

router = APIRouter()
logger = logging.getLogger("tourai.api")

MODEL = "openai/gpt-oss-120b"


# ── Server-side pre-fetch functions ──────────────────────────────────────────

async def _fetch_attractions(lat: float, lon: float, interests: list[str]) -> list:
    from utils.geoapify_places import fetch_pois
    from utils.poi_ranker import rank_pois
    pois = await fetch_pois(lat, lon, 6000, settings.geoapify_api_key, limit=50)
    food_types = {"restaurant", "cafe", "bar", "pub", "fast_food"}
    attractions = [p for p in pois if p["poi_type"] not in food_types]
    ranked = rank_pois(attractions, interests, lat, lon, limit=10, max_per_type=3)
    return [
        {"name": p["name"], "poi_type": p["poi_type"], "lat": p["lat"], "lon": p["lon"]}
        for p in ranked
    ]


async def _fetch_restaurants(lat: float, lon: float) -> list:
    FOOD_CATS = "catering.restaurant,catering.cafe,catering.bar,catering.pub,catering.fast_food"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                _PLACES_URL,
                params={
                    "categories": FOOD_CATS,
                    "filter": f"circle:{lon},{lat},3000",
                    "limit": 10,
                    "apiKey": settings.geoapify_api_key,
                },
            )
            resp.raise_for_status()
        results = []
        for f in resp.json().get("features", []):
            p = f.get("properties", {})
            name = p.get("name", "").strip()
            if not name:
                continue
            results.append({
                "name": name,
                "cuisine": p.get("datasource", {}).get("raw", {}).get("cuisine", ""),
            })
        return results[:8]
    except Exception as exc:
        logger.warning("prefetch_restaurants_failed", extra={"error": str(exc)})
        return []


async def _fetch_hotels(lat: float, lon: float) -> list:
    HOTEL_CATS = "accommodation.hotel,accommodation.guest_house,accommodation.hostel,accommodation.motel"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                _PLACES_URL,
                params={
                    "categories": HOTEL_CATS,
                    "filter": f"circle:{lon},{lat},4000",
                    "limit": 8,
                    "apiKey": settings.geoapify_api_key,
                },
            )
            resp.raise_for_status()
        results = []
        for f in resp.json().get("features", []):
            p = f.get("properties", {})
            name = p.get("name", "").strip()
            if not name:
                continue
            coords = f.get("geometry", {}).get("coordinates", [])
            results.append({
                "name": name,
                "stars": p.get("datasource", {}).get("raw", {}).get("stars", ""),
                "lat": coords[1] if len(coords) >= 2 else lat,
                "lon": coords[0] if len(coords) >= 2 else lon,
            })
        return results[:8]
    except Exception as exc:
        logger.warning("prefetch_hotels_failed", extra={"error": str(exc)})
        return []


async def _fetch_weather(lat: float, lon: float, dates: list[str]) -> list:
    try:
        from utils.weather import get_forecast
        return await get_forecast(lat, lon, dates)
    except Exception as exc:
        logger.warning("prefetch_weather_failed", extra={"error": str(exc)})
        return []


async def _prefetch_all(lat: float, lon: float, dates: list[str], interests: list[str]) -> dict:
    """Fetch all data sources in parallel — replaces the agent's first tool-calling iteration."""
    attractions, restaurants, hotels, weather = await asyncio.gather(
        _fetch_attractions(lat, lon, interests),
        _fetch_restaurants(lat, lon),
        _fetch_hotels(lat, lon),
        _fetch_weather(lat, lon, dates),
        return_exceptions=True,
    )

    def _safe(val, default):
        return default if isinstance(val, Exception) else val

    data = {
        "attractions": _safe(attractions, []),
        "restaurants": _safe(restaurants, []),
        "hotels":      _safe(hotels, []),
        "weather":     _safe(weather, []),
    }

    # Compute golden hour from weather data — no extra API call needed
    if "photography" in interests and data["weather"]:
        from utils.golden_hour import get_light_windows
        data["golden_hour"] = [
            {"date": w.get("date", ""), **get_light_windows(w.get("sunrise_iso", ""), w.get("sunset_iso", ""))}
            for w in data["weather"]
            if w.get("sunrise_iso") and w.get("sunset_iso")
        ]

    logger.info("prefetch_complete", extra={
        "attractions": len(data["attractions"]),
        "restaurants": len(data["restaurants"]),
        "hotels":      len(data["hotels"]),
        "weather_days": len(data["weather"]),
    })
    return data


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(interests: list[str], pace: str, style: str, drive_tol_hrs: float) -> str:
    stops_per_day = {"relaxed": "2–3", "balanced": "3–4", "packed": "4–5"}.get(pace, "3–4")
    return f"""You are TourAI, an expert AI travel agent. You have been given real-time data about the destination — use it to build a complete, personalised trip plan covering accommodation, getting there, meals, transit between every stop, and a realistic budget.

Traveller profile:
  Interests: {', '.join(interests) if interests else 'general sightseeing'}
  Pace: {pace} ({stops_per_day} activity stops per day, not counting meals)
  Travelling as: {style}
  Max drive between stops: {drive_tol_hrs} hours

Planning rules:
- Every day must include breakfast, lunch, and dinner stops (is_meal: true)
- Schedule outdoor/photography spots on clear days; museums/galleries on rainy days
- transit_from_prev.mode: "arrive" for first stop, "walk" ≤15 min, "uber" 15–30 min, "drive" >30 min
- Cluster stops geographically to minimise travel time
- Hotel check-in is the first stop on Day 1 (arrival_time: "2:00 PM", poi_type: "accommodation")
- Hotel check-out is the last stop on the final day (arrival_time: "11:00 AM")
- Use only places from the provided data where possible; supplement with your knowledge for meals
- Write tips with insider knowledge, not Wikipedia summaries

Respond with ONLY a ```json code block containing:
title, summary, getting_there(notes,drive_time_min,drive_distance_km,flights_url),
accommodation(recommended_area,area_reason,booking_url,options:[name,tier,est_price_usd_per_night]),
budget(accommodation_usd,food_usd,activities_usd,transport_usd,total_usd,notes),
days:[date,day_label,weather(description,temp_high_c,temp_low_c,is_clear),
stops:[name,poi_type,tip,arrival_time,duration_min,is_meal,lat,lon,
transit_from_prev(mode,duration_min,notes)]]"""


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ── Planner call ──────────────────────────────────────────────────────────────

async def _call_planner(system: str, user_msg: str) -> str:
    """Single streaming call to gpt-oss-120b. Returns full response content."""
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.groq_api_key)

    stream = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        temperature=1,
        max_completion_tokens=4000,
        reasoning_effort="low",
        stream=True,
    )

    content = ""
    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if choice and choice.delta.content:
            content += choice.delta.content

    return content


# ── Main generator ────────────────────────────────────────────────────────────

async def _run_planner(
    destination: str,
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    interests: list[str],
    style: str,
    pace: str,
    drive_tol: float,
):
    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(destination)}"
    booking_url = (
        f"https://www.booking.com/search.html?ss={quote_plus(destination)}"
        f"&checkin={start_date}&checkout={end_date}"
    )

    d0    = date.fromisoformat(start_date)
    d1    = date.fromisoformat(end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    yield _sse({"type": "start", "message": f"Planning your trip to {destination}…"})

    # ── Step 1: parallel pre-fetch (free, no LLM) ──────────────────────────
    yield _sse({"type": "step", "tool": "prefetch", "message": "Gathering local data…"})

    data = await _prefetch_all(lat, lon, dates, interests)

    yield _sse({"type": "result", "tool": "prefetch", "message": (
        f"Found {len(data['attractions'])} attractions · "
        f"{len(data['restaurants'])} restaurants · "
        f"{len(data['hotels'])} hotels"
    )})

    # ── Step 2: single reasoning call ──────────────────────────────────────
    yield _sse({"type": "step", "tool": "finalize_plan", "message": "Putting it all together…"})

    weather_summary = "\n".join(
        f"  {w.get('date','?')}: {w.get('description','?')} · {w.get('temp_high_c','?')}°C high / {w.get('temp_low_c','?')}°C low · {'clear' if w.get('is_clear') else 'not clear'}"
        for w in data["weather"]
    ) or "  Weather data unavailable"

    golden_section = ""
    if "golden_hour" in data:
        golden_section = "\nGolden hour windows:\n" + "\n".join(
            f"  {g['date']}: {g.get('label','?')} · active={g.get('active')} · mins_away={g.get('minutes_away')}"
            for g in data["golden_hour"]
        )

    user_msg = f"""Plan a trip to {destination}.
Dates: {start_date} to {end_date} ({len(dates)} day{'s' if len(dates) > 1 else ''})
Coordinates: lat={lat}, lon={lon}
Interests: {', '.join(interests) if interests else 'general sightseeing'}
Flights URL: {flights_url}
Booking URL: {booking_url}

--- LIVE DATA ---

Attractions nearby:
{json.dumps(data['attractions'])}

Restaurants nearby:
{json.dumps(data['restaurants'])}

Hotels nearby:
{json.dumps(data['hotels'])}

Weather forecast:
{weather_summary}
{golden_section}"""

    system = _build_system_prompt(interests, pace, style, drive_tol)

    try:
        content = await _call_planner(system, user_msg)
    except Exception as exc:
        logger.error("planner_call_failed", extra={"error": str(exc)})
        yield _sse({"type": "error", "message": "Planning failed. Please try again."})
        return

    # Strip <think>...</think> reasoning block before parsing
    content_clean = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()

    plan = None

    # 1. Prefer a fully closed ```json ... ``` block
    match = re.search(r"```json\s*([\s\S]*?)\s*```", content_clean)
    if match:
        try:
            plan = json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. Truncated response — grab everything after ```json and parse what we have
    if plan is None:
        open_match = re.search(r"```json\s*([\s\S]*)", content_clean)
        if open_match:
            try:
                plan = json.loads(open_match.group(1).strip())
            except json.JSONDecodeError:
                pass

    # 3. Raw JSON with no code fence
    if plan is None:
        try:
            plan = json.loads(content_clean)
        except Exception:
            pass

    if plan:
        plan.setdefault("getting_there", {}).setdefault("flights_url", flights_url)
        plan.setdefault("accommodation", {}).setdefault("booking_url", booking_url)
        plan["destination"] = destination
        plan["start_date"]  = start_date
        plan["end_date"]    = end_date
        yield _sse({"type": "complete", "plan": plan})
    else:
        logger.error("plan_parse_failed", extra={"content_preview": content[:300]})
        yield _sse({"type": "error", "message": "Could not parse the plan. Please try again."})


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v1/itinerary/stream")
async def stream_itinerary(
    body: ItineraryRequest,
    authorization: str | None = Header(default=None),
):
    interests = list(body.interests)
    style     = body.travel_style or "solo"
    pace      = body.pace or "balanced"
    drive_tol = body.drive_tolerance_hrs

    if authorization and authorization.startswith("Bearer "):
        try:
            from api.supabase_client import get_supabase
            token = authorization.removeprefix("Bearer ").strip()
            user  = get_supabase().auth.get_user(token).user
            if user:
                result = (
                    get_supabase()
                    .table("profiles")
                    .select("interests,travel_style,pace,drive_tolerance_hrs")
                    .eq("user_id", str(user.id))
                    .execute()
                )
                if result.data:
                    p = result.data[0]
                    if not interests:      interests = p.get("interests") or []
                    if style == "solo":    style     = p.get("travel_style") or "solo"
                    if pace == "balanced": pace      = p.get("pace") or "balanced"
        except Exception as exc:
            logger.warning("profile_load_failed", extra={"error": str(exc)})

    from utils.google_places import geocode_destination
    geo = await geocode_destination(body.destination, api_key=settings.geoapify_api_key)
    if not geo:
        async def _err():
            yield _sse({"type": "error", "message": f"Could not find destination: {body.destination!r}"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    lat  = geo.get("lat")
    lon  = geo.get("lon")
    if lat is None or lon is None:
        async def _err():
            yield _sse({"type": "error", "message": f"Could not geocode destination: {body.destination!r}"})
        return StreamingResponse(_err(), media_type="text/event-stream")
    dest = (geo.get("display_name") or body.destination).split(",")[0].strip()

    async def _stream():
        try:
            async for chunk in _run_planner(dest, lat, lon, body.start_date, body.end_date,
                                            interests, style, pace, drive_tol):
                yield chunk
        except Exception:
            logger.error("agent_stream_error", extra={"exc": traceback.format_exc()})
            yield _sse({"type": "error", "message": "Something went wrong. Please try again."})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
