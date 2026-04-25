"""api/routes/itinerary_agent.py — Agentic trip planner with SSE streaming.

The agent has access to 7 tools and runs in a loop until it calls
finalize_plan. Each tool call is streamed to the client as an SSE event so
the user sees live progress ("Checking weather…", "Finding hotels…", etc.)
"""

import asyncio
import json
import logging
import traceback
from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.config import settings
from api.logging_setup import correlation_id
from api.models import ItineraryRequest

router = APIRouter()
logger = logging.getLogger("tourai.api")

MAX_ITERATIONS = 12

# ── Tool definitions (Groq / OpenAI function-calling format) ──────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_attractions",
            "description": (
                "Search for tourist attractions, museums, parks, viewpoints, historic sites, "
                "and cultural venues near the destination. Call this once per interest category "
                "that the traveller cares about."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":        {"type": "number", "description": "Destination latitude"},
                    "lon":        {"type": "number", "description": "Destination longitude"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Geoapify category strings, e.g. 'entertainment.museum', "
                            "'natural.park', 'tourism.sights', 'historic', 'natural.beach'"
                        ),
                    },
                    "radius_m": {"type": "integer", "default": 6000},
                    "limit":    {"type": "integer", "default": 20},
                },
                "required": ["lat", "lon", "categories"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": "Find restaurants, cafes, and bars near the destination for meal stops.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":      {"type": "number"},
                    "lon":      {"type": "number"},
                    "radius_m": {"type": "integer", "default": 3000},
                    "limit":    {"type": "integer", "default": 15},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Find hotels and accommodation options near the destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":      {"type": "number"},
                    "lon":      {"type": "number"},
                    "radius_m": {"type": "integer", "default": 4000},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": (
                "Get the weather forecast for each day of the trip. "
                "Use this to decide which days are best for outdoor vs indoor activities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":   {"type": "number"},
                    "lon":   {"type": "number"},
                    "dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ISO dates YYYY-MM-DD for each day of the trip",
                    },
                },
                "required": ["lat", "lon", "dates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_golden_hour",
            "description": (
                "Get golden hour and blue hour timing for a specific date. "
                "Use this when the traveller is interested in photography. "
                "Schedule viewpoints and scenic spots to arrive around these times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":  {"type": "number"},
                    "lon":  {"type": "number"},
                    "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                },
                "required": ["lat", "lon", "date"],
            },
        },
    },
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
    {
        "type": "function",
        "function": {
            "name": "finalize_plan",
            "description": (
                "Submit the complete, finalized trip plan. Call this once you have "
                "gathered enough information to build a high-quality itinerary. "
                "Include accommodation options, a budget estimate, and transit notes "
                "between every stop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title":   {"type": "string"},
                    "summary": {"type": "string", "description": "2-sentence trip summary"},
                    "getting_there": {
                        "type": "object",
                        "properties": {
                            "notes":            {"type": "string"},
                            "drive_time_min":   {"type": "integer"},
                            "drive_distance_km":{"type": "number"},
                            "flights_url":      {"type": "string"},
                        },
                    },
                    "accommodation": {
                        "type": "object",
                        "properties": {
                            "recommended_area": {"type": "string"},
                            "area_reason":      {"type": "string"},
                            "booking_url":      {"type": "string"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name":                    {"type": "string"},
                                        "tier":                    {"type": "string"},
                                        "est_price_usd_per_night": {"type": "integer"},
                                    },
                                },
                            },
                        },
                    },
                    "budget": {
                        "type": "object",
                        "properties": {
                            "accommodation_usd": {"type": "integer"},
                            "food_usd":          {"type": "integer"},
                            "activities_usd":    {"type": "integer"},
                            "transport_usd":     {"type": "integer"},
                            "total_usd":         {"type": "integer"},
                            "notes":             {"type": "string"},
                        },
                    },
                    "days": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date":      {"type": "string"},
                                "day_label": {"type": "string"},
                                "weather": {
                                    "type": "object",
                                    "properties": {
                                        "description":  {"type": "string"},
                                        "temp_high_c":  {"type": "number"},
                                        "temp_low_c":   {"type": "number"},
                                        "is_clear":     {"type": "boolean"},
                                    },
                                },
                                "stops": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name":         {"type": "string"},
                                            "poi_type":     {"type": "string"},
                                            "tip":          {"type": "string"},
                                            "arrival_time": {"type": "string"},
                                            "duration_min": {"type": "integer"},
                                            "is_meal":      {"type": "boolean"},
                                            "lat":          {"type": "number"},
                                            "lon":          {"type": "number"},
                                            "transit_from_prev": {
                                                "type": "object",
                                                "properties": {
                                                    "mode":         {"type": "string", "description": "walk | uber | drive | metro | arrive"},
                                                    "duration_min": {"type": "integer"},
                                                    "notes":        {"type": "string"},
                                                },
                                            },
                                        },
                                        "required": ["name", "arrival_time", "transit_from_prev"],
                                    },
                                },
                            },
                        },
                    },
                },
                "required": ["title", "summary", "days"],
            },
        },
    },
]

# ── Tool executor ─────────────────────────────────────────────────────────────

async def _exec_tool(name: str, args: dict) -> str:
    """Execute a tool call and return a JSON string result."""
    try:
        if name == "search_attractions":
            from utils.geoapify_places import fetch_pois
            # Map user-supplied category strings to Geoapify format
            pois = await fetch_pois(
                args["lat"], args["lon"],
                int(args.get("radius_m", 6000)),
                settings.geoapify_api_key,
                limit=int(args.get("limit", 20)),
            )
            # Filter to only non-restaurant categories
            filtered = [p for p in pois if p["poi_type"] not in
                        {"restaurant", "cafe", "bar", "pub", "bakery", "fast_food"}]
            return json.dumps([
                {"name": p["name"], "poi_type": p["poi_type"],
                 "lat": p["lat"], "lon": p["lon"],
                 "description": p["tags"].get("description", "")}
                for p in filtered[:20]
            ])

        if name == "search_restaurants":
            from utils.geoapify_places import fetch_pois
            from utils.geoapify_places import _PLACES_URL
            import httpx as _httpx
            FOOD_CATS = "catering.restaurant,catering.cafe,catering.bar,catering.pub,catering.fast_food"
            async with _httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    _PLACES_URL,
                    params={
                        "categories": FOOD_CATS,
                        "filter":     f"circle:{args['lon']},{args['lat']},{args.get('radius_m', 3000)}",
                        "limit":      args.get("limit", 15),
                        "apiKey":     settings.geoapify_api_key,
                    },
                )
                resp.raise_for_status()
            features = resp.json().get("features", [])
            results = []
            for f in features:
                p = f.get("properties", {})
                name = p.get("name", "").strip()
                if not name:
                    continue
                coords = f.get("geometry", {}).get("coordinates", [])
                if len(coords) < 2:
                    continue
                results.append({
                    "name": name,
                    "poi_type": "restaurant",
                    "lat": coords[1], "lon": coords[0],
                    "cuisine": p.get("datasource", {}).get("raw", {}).get("cuisine", ""),
                })
            return json.dumps(results[:15])

        if name == "search_hotels":
            from utils.geoapify_places import _PLACES_URL
            import httpx as _httpx
            HOTEL_CATS = "accommodation.hotel,accommodation.guest_house,accommodation.hostel,accommodation.motel"
            async with _httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    _PLACES_URL,
                    params={
                        "categories": HOTEL_CATS,
                        "filter":     f"circle:{args['lon']},{args['lat']},{args.get('radius_m', 4000)}",
                        "limit":      12,
                        "apiKey":     settings.geoapify_api_key,
                    },
                )
                resp.raise_for_status()
            features = resp.json().get("features", [])
            results = []
            for f in features:
                p = f.get("properties", {})
                name = p.get("name", "").strip()
                if not name:
                    continue
                coords = f.get("geometry", {}).get("coordinates", [])
                results.append({
                    "name": name,
                    "lat": coords[1] if len(coords) >= 2 else args["lat"],
                    "lon": coords[0] if len(coords) >= 2 else args["lon"],
                    "stars": p.get("datasource", {}).get("raw", {}).get("stars", ""),
                })
            return json.dumps(results[:12])

        if name == "get_weather_forecast":
            from utils.weather import get_forecast
            forecast = await get_forecast(args["lat"], args["lon"], args["dates"])
            return json.dumps(forecast)

        if name == "get_golden_hour":
            from utils.weather import get_forecast
            from utils.golden_hour import get_light_windows
            forecast = await get_forecast(args["lat"], args["lon"], [args["date"]])
            if not forecast:
                return json.dumps({"error": "date out of forecast range"})
            f = forecast[0]
            windows = get_light_windows(f["sunrise_iso"], f["sunset_iso"])
            return json.dumps({
                "date":           args["date"],
                "sunrise":        f["sunrise_iso"],
                "sunset":         f["sunset_iso"],
                "golden_hour":    windows,
                "evening_golden": f["sunset_iso"],
            })

        if name == "get_drive_time":
            from utils.osrm import get_drive_time
            result = await get_drive_time(
                args["from_lat"], args["from_lon"],
                args["to_lat"],  args["to_lon"],
            )
            return json.dumps(result)

        if name == "finalize_plan":
            # Caller handles this specially — just echo back
            return json.dumps({"status": "accepted"})

    except Exception as exc:
        logger.warning("tool_exec_error", extra={"tool": name, "error": str(exc)})
        return json.dumps({"error": str(exc)})

    return json.dumps({"error": f"unknown tool: {name}"})


# ── Agent system prompt ───────────────────────────────────────────────────────

def _build_system_prompt(interests: list[str], pace: str, style: str, drive_tol_hrs: float) -> str:
    stops_per_day = {"relaxed": "2–3", "balanced": "3–4", "packed": "4–5"}.get(pace, "3–4")
    return f"""You are TourAI, an expert AI travel agent. Your job is to build a complete, personalised trip plan — not just a list of attractions, but a full end-to-end plan covering accommodation, getting there, meals, transit between stops, and a budget estimate.

Traveller profile:
  Interests: {', '.join(interests) if interests else 'general sightseeing'}
  Pace: {pace} ({stops_per_day} activity stops per day, not counting meals)
  Travelling as: {style}
  Max drive between stops: {drive_tol_hrs} hours

How to use your tools:
1. Call search_attractions to find things to do (tailor categories to interests)
2. Call get_weather_forecast to see which days suit outdoor vs indoor activities
3. If photography is an interest, call get_golden_hour for each day — schedule scenic spots at those times
4. Call search_restaurants to find meal spots (include breakfast, lunch, dinner)
5. Call search_hotels to find accommodation
6. Call get_drive_time to validate legs that look long — reorder stops if needed
7. Once you have enough, call finalize_plan

Rules for the itinerary:
- Every day must include breakfast, lunch, and dinner stops (is_meal: true)
- Schedule outdoor/photography spots on clear days, museums/galleries on rainy days
- transit_from_prev.mode: "arrive" for first stop, "walk" if ≤ 15 min, "uber" if 15–30 min, "drive" if > 30 min
- Cluster stops geographically to minimise travel
- Hotel check-in is the first stop on Day 1 (arrival_time: "2:00 PM", poi_type: "accommodation")
- Hotel check-out is the last stop on the final day (arrival_time: "11:00 AM")
- accommodation.booking_url: Booking.com search link for the destination + dates
- getting_there.flights_url: Google Flights search link
- Budget: realistic estimate in USD, include a helpful "notes" string
- Write tips that feel like insider knowledge, not a Wikipedia summary

Output a complete, high-quality plan. The traveller should be able to follow it without opening another app."""


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


_TOOL_LABELS = {
    "search_attractions":   "Exploring attractions",
    "search_restaurants":   "Finding places to eat",
    "search_hotels":        "Searching for accommodation",
    "get_weather_forecast": "Checking the weather forecast",
    "get_golden_hour":      "Calculating golden hour",
    "get_drive_time":       "Checking drive times",
    "finalize_plan":        "Building your complete plan",
}


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
    """Async generator that yields SSE strings.

    Yields progress events as the agent works, then a final 'complete' or 'error' event.
    """
    from groq import AsyncGroq

    client  = AsyncGroq(api_key=settings.groq_api_key)
    system  = _build_system_prompt(interests, pace, style, drive_tol)

    # Build date list for the trip
    d0    = date.fromisoformat(start_date)
    d1    = date.fromisoformat(end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(destination)}"
    booking_url = (
        f"https://www.booking.com/search.html?ss={quote_plus(destination)}"
        f"&checkin={start_date}&checkout={end_date}"
    )

    user_msg = (
        f"Plan a trip to {destination}.\n"
        f"Dates: {start_date} to {end_date} ({len(dates)} day{'s' if len(dates)>1 else ''})\n"
        f"Destination coordinates: lat={lat}, lon={lon}\n"
        f"Interests: {', '.join(interests) if interests else 'general sightseeing'}\n"
        f"Flights search: {flights_url}\n"
        f"Hotel booking search: {booking_url}\n"
        f"Use these URLs in finalize_plan."
    )

    messages = [{"role": "user", "content": user_msg}]

    yield _sse({"type": "start", "message": f"Planning your trip to {destination}…"})

    for iteration in range(MAX_ITERATIONS):
        resp = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system}] + messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=2000,
            temperature=0.3,
        )

        msg = resp.choices[0].message

        # No tool calls → agent is done (shouldn't happen before finalize, but handle it)
        if not msg.tool_calls:
            yield _sse({"type": "error", "message": "Agent stopped without finalising the plan."})
            return

        # Process each tool call the agent requested this iteration
        tool_results = []
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except Exception:
                tool_args = {}

            label = _TOOL_LABELS.get(tool_name, tool_name.replace("_", " ").title())
            yield _sse({"type": "step", "tool": tool_name, "message": f"{label}…"})

            # finalize_plan → extract the plan and return
            if tool_name == "finalize_plan":
                yield _sse({"type": "step", "tool": "finalize_plan", "message": "Putting it all together…"})
                plan = dict(tool_args)
                plan.setdefault("getting_there", {})
                plan["getting_there"].setdefault("flights_url", flights_url)
                plan.setdefault("accommodation", {})
                plan["accommodation"].setdefault("booking_url", booking_url)
                plan["destination"]  = destination
                plan["start_date"]   = start_date
                plan["end_date"]     = end_date
                yield _sse({"type": "complete", "plan": plan})
                return

            result = await _exec_tool(tool_name, tool_args)

            # Emit a human-readable result summary
            try:
                parsed = json.loads(result)
                if isinstance(parsed, list):
                    yield _sse({"type": "result", "tool": tool_name,
                                "message": f"Found {len(parsed)} results"})
                elif isinstance(parsed, dict) and "duration_min" in parsed:
                    mins = parsed.get("duration_min")
                    km   = parsed.get("distance_km")
                    yield _sse({"type": "result", "tool": tool_name,
                                "message": f"{mins} min drive · {km} km"})
                elif isinstance(parsed, dict) and "error" not in parsed:
                    yield _sse({"type": "result", "tool": tool_name, "message": "Done"})
            except Exception:
                pass

            tool_results.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result,
            })

        # Append assistant message + all tool results before next iteration
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": msg.tool_calls})
        messages.extend(tool_results)

        await asyncio.sleep(0)  # yield control between iterations

    yield _sse({"type": "error", "message": "Could not complete the plan in time. Please try again."})


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v1/itinerary/stream")
async def stream_itinerary(
    body: ItineraryRequest,
    authorization: str | None = Header(default=None),
):
    # Optional: pull profile prefs if auth token provided
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
                    if not interests:    interests = p.get("interests") or []
                    if style == "solo":  style     = p.get("travel_style") or "solo"
                    if pace == "balanced": pace    = p.get("pace") or "balanced"
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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
