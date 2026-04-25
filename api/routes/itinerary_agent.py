"""api/routes/itinerary_agent.py — Agentic trip planner with SSE streaming.

Architecture (efficient version):
  Phase 1 — Pre-fetch: all data-gathering runs in parallel (asyncio.gather).
             Attractions, restaurants, hotels, and weather are fetched once
             before the agent starts, so no tool calls are needed for them.
  Phase 2 — Plan: agent receives all data in its initial context.
             Only tool available is get_drive_time (requires agent-decided
             coordinates). Agent outputs the plan as a JSON code block in
             typically 1–2 Groq calls instead of 6–12.

Total API calls per plan:
  Before: ~6–12 Groq + 5 Geoapify + 2 Open-Meteo (sequential, ~30–60 s)
  After:  ~2 Groq + 4 Geoapify + 1 Open-Meteo (parallel pre-fetch, ~8–12 s)
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
from api.logging_setup import correlation_id
from api.models import ItineraryRequest
from utils.geoapify_places import _PLACES_URL

router = APIRouter()
logger = logging.getLogger("tourai.api")

MAX_ITERATIONS = 6   # much lower now — agent only needs 1–2 calls to plan

# ── Tool definitions — only get_drive_time remains ───────────────────────────
# All fetching is done upfront in parallel; the agent only validates legs.

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_drive_time",
            "description": (
                "Get driving time in minutes between two coordinates. "
                "Use this to validate that consecutive stops are reachable within "
                "the traveller's drive tolerance, and to decide transit mode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_lat": {"type": "number"},
                    "from_lon": {"type": "number"},
                    "to_lat":   {"type": "number"},
                    "to_lon":   {"type": "number"},
                },
                "required": ["from_lat", "from_lon", "to_lat", "to_lon"],
            },
        },
    },
]


# ── Parallel pre-fetch helpers ────────────────────────────────────────────────

async def _prefetch_attractions(lat: float, lon: float) -> list[dict]:
    from utils.geoapify_places import fetch_pois
    pois = await fetch_pois(lat, lon, 6000, settings.geoapify_api_key, limit=30)
    return [
        {"name": p["name"], "poi_type": p["poi_type"], "lat": p["lat"], "lon": p["lon"]}
        for p in pois
        if p["poi_type"] not in {"restaurant", "cafe", "bar", "pub", "fast_food"}
    ][:25]


async def _prefetch_restaurants(lat: float, lon: float) -> list[dict]:
    FOOD_CATS = "catering.restaurant,catering.cafe,catering.bar,catering.pub,catering.fast_food"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                _PLACES_URL,
                params={
                    "categories": FOOD_CATS,
                    "filter":     f"circle:{lon},{lat},3000",
                    "limit":      15,
                    "apiKey":     settings.geoapify_api_key,
                },
            )
            resp.raise_for_status()
        results = []
        for f in resp.json().get("features", []):
            p      = f.get("properties", {})
            name   = p.get("name", "").strip()
            coords = f.get("geometry", {}).get("coordinates", [])
            if not name or len(coords) < 2:
                continue
            results.append({
                "name":     name,
                "poi_type": "restaurant",
                "lat":      coords[1],
                "lon":      coords[0],
                "cuisine":  p.get("datasource", {}).get("raw", {}).get("cuisine", ""),
            })
        return results[:15]
    except Exception as exc:
        logger.warning("prefetch_restaurants_failed", extra={"error": str(exc)})
        return []


async def _prefetch_hotels(lat: float, lon: float) -> list[dict]:
    HOTEL_CATS = "accommodation.hotel,accommodation.guest_house,accommodation.hostel,accommodation.motel"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                _PLACES_URL,
                params={
                    "categories": HOTEL_CATS,
                    "filter":     f"circle:{lon},{lat},4000",
                    "limit":      12,
                    "apiKey":     settings.geoapify_api_key,
                },
            )
            resp.raise_for_status()
        results = []
        for f in resp.json().get("features", []):
            p      = f.get("properties", {})
            name   = p.get("name", "").strip()
            coords = f.get("geometry", {}).get("coordinates", [])
            if not name:
                continue
            results.append({
                "name":  name,
                "lat":   coords[1] if len(coords) >= 2 else lat,
                "lon":   coords[0] if len(coords) >= 2 else lon,
                "stars": p.get("datasource", {}).get("raw", {}).get("stars", ""),
            })
        return results[:12]
    except Exception as exc:
        logger.warning("prefetch_hotels_failed", extra={"error": str(exc)})
        return []


# ── Tool executor (drive time only) ──────────────────────────────────────────

async def _exec_tool(name: str, args: dict) -> str:
    try:
        if name == "get_drive_time":
            from utils.osrm import get_drive_time
            result = await get_drive_time(
                args["from_lat"], args["from_lon"],
                args["to_lat"],  args["to_lon"],
            )
            return json.dumps(result)
    except Exception as exc:
        logger.warning("tool_exec_error", extra={"tool": name, "error": str(exc)})
        return json.dumps({"error": str(exc)})
    return json.dumps({"error": f"unknown tool: {name}"})


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(interests: list[str], pace: str, style: str, drive_tol_hrs: float) -> str:
    stops_per_day = {"relaxed": "2–3", "balanced": "3–4", "packed": "4–5"}.get(pace, "3–4")
    return f"""You are TourAI, an expert AI travel agent. Build a complete, personalised trip plan — accommodation, getting there, meals, transit between every stop, and a realistic budget.

Traveller profile:
  Interests: {', '.join(interests) if interests else 'general sightseeing'}
  Pace: {pace} ({stops_per_day} activity stops per day, not counting meals)
  Travelling as: {style}
  Max drive between stops: {drive_tol_hrs} hours

All attraction, restaurant, hotel, and weather data has already been fetched and is included in the user message. Use it directly — do NOT call any search tools.

You have ONE tool available: get_drive_time(from_lat, from_lon, to_lat, to_lon).
Use it sparingly — only to validate legs that look longer than {drive_tol_hrs} hours.

Planning rules:
- Every day must include breakfast, lunch, and dinner stops (is_meal: true)
- Schedule outdoor/photography spots on clear days; museums/galleries on rainy days
- transit_from_prev.mode: "arrive" for first stop, "walk" ≤15 min, "uber" 15–30 min, "drive" >30 min
- Cluster stops geographically to minimise travel
- Hotel check-in is the first stop on Day 1 (arrival_time: "2:00 PM", poi_type: "accommodation")
- Hotel check-out is the last stop on the final day (arrival_time: "11:00 AM")
- Write tips that feel like insider knowledge, not a Wikipedia summary
- Pick real places from the provided lists; invent names only if the lists are empty

When ready, respond with ONLY a JSON code block:

```json
{{
  "title": "Trip title",
  "summary": "2-sentence summary",
  "getting_there": {{
    "notes": "How to get there",
    "drive_time_min": 0,
    "drive_distance_km": 0.0,
    "flights_url": "..."
  }},
  "accommodation": {{
    "recommended_area": "Area name",
    "area_reason": "Why this area",
    "booking_url": "...",
    "options": [{{"name": "Hotel", "tier": "mid-range", "est_price_usd_per_night": 150}}]
  }},
  "budget": {{
    "accommodation_usd": 300,
    "food_usd": 150,
    "activities_usd": 100,
    "transport_usd": 80,
    "total_usd": 630,
    "notes": "Budget notes"
  }},
  "days": [
    {{
      "date": "YYYY-MM-DD",
      "day_label": "Day 1 — Arrival",
      "weather": {{"description": "Sunny", "temp_high_c": 28, "temp_low_c": 18, "is_clear": true}},
      "stops": [
        {{
          "name": "Stop name",
          "poi_type": "attraction",
          "tip": "Insider tip",
          "arrival_time": "10:00 AM",
          "duration_min": 90,
          "is_meal": false,
          "lat": 0.0,
          "lon": 0.0,
          "transit_from_prev": {{"mode": "walk", "duration_min": 10, "notes": "Short walk"}}
        }}
      ]
    }}
  ]
}}
```"""


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ── Agent loop ────────────────────────────────────────────────────────────────

async def _run_agent(
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
    """Async generator yielding SSE strings.

    Pre-fetches all data in parallel, then runs the agent with a rich context.
    The agent only needs to call get_drive_time (optional) then output the plan.
    """
    from groq import AsyncGroq
    from utils.weather import get_forecast
    from utils.golden_hour import get_light_windows

    groq_client = AsyncGroq(api_key=settings.groq_api_key)
    system      = _build_system_prompt(interests, pace, style, drive_tol)

    d0    = date.fromisoformat(start_date)
    d1    = date.fromisoformat(end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(destination)}"
    booking_url = (
        f"https://www.booking.com/search.html?ss={quote_plus(destination)}"
        f"&checkin={start_date}&checkout={end_date}"
    )

    yield _sse({"type": "start", "message": f"Planning your trip to {destination}…"})

    # ── Phase 1: parallel pre-fetch ───────────────────────────────────────────
    yield _sse({"type": "step", "tool": "search_attractions", "message": "Gathering local information…"})

    attractions, restaurants, hotels, weather = await asyncio.gather(
        _prefetch_attractions(lat, lon),
        _prefetch_restaurants(lat, lon),
        _prefetch_hotels(lat, lon),
        get_forecast(lat, lon, dates),
        return_exceptions=True,
    )

    # Normalise exceptions → empty lists
    if isinstance(attractions, Exception):
        logger.warning("prefetch_attractions_exc", extra={"error": str(attractions)})
        attractions = []
    if isinstance(restaurants, Exception):
        logger.warning("prefetch_restaurants_exc", extra={"error": str(restaurants)})
        restaurants = []
    if isinstance(hotels, Exception):
        logger.warning("prefetch_hotels_exc", extra={"error": str(hotels)})
        hotels = []
    if isinstance(weather, Exception):
        logger.warning("prefetch_weather_exc", extra={"error": str(weather)})
        weather = []

    yield _sse({"type": "result", "tool": "search_attractions",
                "message": f"Found {len(attractions)} attractions, {len(restaurants)} restaurants, {len(hotels)} hotels"})
    yield _sse({"type": "result", "tool": "get_weather_forecast",
                "message": f"Weather ready for {len(weather)} days"})

    # Compute golden hour inline from weather data (no extra API call)
    golden_hours = {}
    if "photography" in (interests or []) or "nature" in (interests or []):
        for w in weather:
            if w.get("sunrise_iso") and w.get("sunset_iso"):
                golden_hours[w["date"]] = get_light_windows(w["sunrise_iso"], w["sunset_iso"])

    # ── Phase 2: build rich context message ───────────────────────────────────
    yield _sse({"type": "step", "tool": "get_drive_time", "message": "Building your personalised itinerary…"})

    context_parts = [
        f"Plan a trip to {destination}.",
        f"Dates: {start_date} to {end_date} ({len(dates)} day{'s' if len(dates) > 1 else ''})",
        f"Destination coordinates: lat={lat}, lon={lon}",
        f"Flights search URL: {flights_url}",
        f"Hotel booking URL: {booking_url}",
        "",
        f"ATTRACTIONS ({len(attractions)} found):",
        json.dumps(attractions, separators=(",", ":")),
        "",
        f"RESTAURANTS ({len(restaurants)} found):",
        json.dumps(restaurants, separators=(",", ":")),
        "",
        f"HOTELS ({len(hotels)} found):",
        json.dumps(hotels, separators=(",", ":")),
        "",
        f"WEATHER FORECAST ({len(weather)} days):",
        json.dumps(weather, separators=(",", ":")),
    ]
    if golden_hours:
        context_parts += ["", "GOLDEN HOUR WINDOWS:", json.dumps(golden_hours, separators=(",", ":"))]

    user_msg  = "\n".join(context_parts)
    messages  = [{"role": "user", "content": user_msg}]

    # ── Phase 3: agent planning loop (1–2 iterations expected) ───────────────
    for iteration in range(MAX_ITERATIONS):
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system}] + messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=4000,
            temperature=0.3,
        )

        msg = resp.choices[0].message

        # No tool calls → agent has output the final plan
        if not msg.tool_calls:
            content = msg.content or ""
            yield _sse({"type": "step", "tool": "finalize_plan", "message": "Putting it all together…"})

            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
            if match:
                try:
                    plan = json.loads(match.group(1))
                    plan.setdefault("getting_there", {}).setdefault("flights_url", flights_url)
                    plan.setdefault("accommodation", {}).setdefault("booking_url", booking_url)
                    plan["destination"] = destination
                    plan["start_date"]  = start_date
                    plan["end_date"]    = end_date
                    yield _sse({"type": "complete", "plan": plan})
                    return
                except json.JSONDecodeError:
                    pass
            # Fallback: try parsing raw content as JSON
            try:
                plan = json.loads(content)
                plan.setdefault("getting_there", {}).setdefault("flights_url", flights_url)
                plan.setdefault("accommodation", {}).setdefault("booking_url", booking_url)
                plan["destination"] = destination
                plan["start_date"]  = start_date
                plan["end_date"]    = end_date
                yield _sse({"type": "complete", "plan": plan})
                return
            except Exception:
                pass

            yield _sse({"type": "error", "message": "Agent stopped without finalising the plan."})
            return

        # Process tool calls (only get_drive_time expected)
        tool_results = []
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except Exception:
                tool_args = {}

            if tool_name == "get_drive_time":
                yield _sse({"type": "step", "tool": "get_drive_time", "message": "Checking drive time between stops…"})

            result = await _exec_tool(tool_name, tool_args)

            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "duration_min" in parsed:
                    mins = parsed.get("duration_min")
                    km   = parsed.get("distance_km")
                    yield _sse({"type": "result", "tool": tool_name,
                                "message": f"{mins} min drive · {km} km"})
            except Exception:
                pass

            tool_results.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result,
            })

        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": msg.tool_calls})
        messages.extend(tool_results)

        await asyncio.sleep(0)

    yield _sse({"type": "error", "message": "Could not complete the plan in time. Please try again."})


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
            token  = authorization.removeprefix("Bearer ").strip()
            user   = get_supabase().auth.get_user(token).user
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
        except Exception:
            pass

    from utils.google_places import geocode_destination
    geo = await geocode_destination(body.destination, api_key=settings.geoapify_api_key)
    if not geo:
        async def _err():
            yield _sse({"type": "error", "message": f"Could not find destination: {body.destination!r}"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    lat  = geo["lat"]
    lon  = geo["lon"]
    dest = geo["display_name"].split(",")[0].strip()

    async def _stream():
        try:
            async for chunk in _run_agent(dest, lat, lon, body.start_date, body.end_date,
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
