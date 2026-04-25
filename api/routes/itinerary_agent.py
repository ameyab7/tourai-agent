"""api/routes/itinerary_agent.py — Agentic trip planner with SSE streaming.

Architecture:
  The agent decides which tools to call. Because gpt-oss-120b batches tool
  calls in a single response, we execute every batch in parallel with
  asyncio.gather — so the agent is both autonomous AND fast.

  Typical flow (2 Groq calls total):
    Call 1 → agent batches: search_attractions + get_weather_forecast
                            + search_restaurants + search_hotels
             → server runs all 4 in parallel
    Call 2 → agent has all data, outputs JSON plan
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

MAX_ITERATIONS = 8

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_attractions",
            "description": (
                "Search for tourist attractions, museums, parks, viewpoints, historic sites, "
                "and cultural venues near the destination."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":      {"type": "number"},
                    "lon":      {"type": "number"},
                    "radius_m": {"type": "integer", "default": 6000},
                    "limit":    {"type": "integer", "default": 25},
                },
                "required": ["lat", "lon"],
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
                "Get the daily weather forecast for each day of the trip. "
                "Use this to decide which days suit outdoor vs indoor activities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat":   {"type": "number"},
                    "lon":   {"type": "number"},
                    "dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ISO dates YYYY-MM-DD for each day",
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
                "Only call this when photography is in the traveller's interests."
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
                "Use to validate legs that may exceed the traveller's drive tolerance."
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


# ── Tool executors ────────────────────────────────────────────────────────────

async def _exec_tool(name: str, args: dict) -> str:
    try:
        if name == "search_attractions":
            from utils.geoapify_places import fetch_pois
            pois = await fetch_pois(
                args["lat"], args["lon"],
                int(args.get("radius_m", 6000)),
                settings.geoapify_api_key,
                limit=int(args.get("limit", 25)),
            )
            filtered = [p for p in pois if p["poi_type"] not in
                        {"restaurant", "cafe", "bar", "pub", "fast_food"}]
            return json.dumps([
                {"name": p["name"], "poi_type": p["poi_type"], "lat": p["lat"], "lon": p["lon"]}
                for p in filtered[:25]
            ])

        if name == "search_restaurants":
            FOOD_CATS = "catering.restaurant,catering.cafe,catering.bar,catering.pub,catering.fast_food"
            async with httpx.AsyncClient(timeout=12) as client:
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
            results = []
            for f in resp.json().get("features", []):
                p      = f.get("properties", {})
                name_  = p.get("name", "").strip()
                coords = f.get("geometry", {}).get("coordinates", [])
                if not name_ or len(coords) < 2:
                    continue
                results.append({
                    "name":     name_,
                    "poi_type": "restaurant",
                    "lat":      coords[1],
                    "lon":      coords[0],
                    "cuisine":  p.get("datasource", {}).get("raw", {}).get("cuisine", ""),
                })
            return json.dumps(results[:15])

        if name == "search_hotels":
            HOTEL_CATS = "accommodation.hotel,accommodation.guest_house,accommodation.hostel,accommodation.motel"
            async with httpx.AsyncClient(timeout=12) as client:
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
            results = []
            for f in resp.json().get("features", []):
                p      = f.get("properties", {})
                name_  = p.get("name", "").strip()
                coords = f.get("geometry", {}).get("coordinates", [])
                if not name_:
                    continue
                results.append({
                    "name":  name_,
                    "lat":   coords[1] if len(coords) >= 2 else args["lat"],
                    "lon":   coords[0] if len(coords) >= 2 else args["lon"],
                    "stars": p.get("datasource", {}).get("raw", {}).get("stars", ""),
                })
            return json.dumps(results[:12])

        if name == "get_weather_forecast":
            from utils.weather import get_forecast
            return json.dumps(await get_forecast(args["lat"], args["lon"], args["dates"]))

        if name == "get_golden_hour":
            from utils.weather import get_forecast
            from utils.golden_hour import get_light_windows
            forecast = await get_forecast(args["lat"], args["lon"], [args["date"]])
            if not forecast:
                return json.dumps({"error": "date out of forecast range"})
            f = forecast[0]
            return json.dumps({
                "date":    args["date"],
                "sunrise": f["sunrise_iso"],
                "sunset":  f["sunset_iso"],
                "windows": get_light_windows(f["sunrise_iso"], f["sunset_iso"]),
            })

        if name == "get_drive_time":
            from utils.osrm import get_drive_time
            return json.dumps(await get_drive_time(
                args["from_lat"], args["from_lon"],
                args["to_lat"],  args["to_lon"],
            ))

    except Exception as exc:
        logger.warning("tool_exec_error", extra={"tool": name, "error": str(exc)})
        return json.dumps({"error": str(exc)})

    return json.dumps({"error": f"unknown tool: {name}"})


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(interests: list[str], pace: str, style: str, drive_tol_hrs: float) -> str:
    stops_per_day = {"relaxed": "2–3", "balanced": "3–4", "packed": "4–5"}.get(pace, "3–4")
    photo = "photography" in (interests or [])
    return f"""You are TourAI, an expert AI travel agent. Build a complete, personalised trip plan — accommodation, getting there, meals, transit between every stop, and a realistic budget.

Traveller profile:
  Interests: {', '.join(interests) if interests else 'general sightseeing'}
  Pace: {pace} ({stops_per_day} activity stops per day, not counting meals)
  Travelling as: {style}
  Max drive between stops: {drive_tol_hrs} hours

IMPORTANT — tool calling strategy:
  In your FIRST response, call ALL of these tools simultaneously in one message:
    • search_attractions (tailor to interests)
    • get_weather_forecast (all trip dates)
    • search_restaurants
    • search_hotels
    {"• get_golden_hour for each day (photography interest detected)" if photo else ""}
  Do NOT call them one at a time. Return all tool calls together so they run in parallel.

  After receiving results, optionally call get_drive_time to validate long legs.
  Then output the complete plan as a JSON code block.

Planning rules:
- Every day must include breakfast, lunch, and dinner stops (is_meal: true)
- Schedule outdoor/photography spots on clear days; museums/galleries on rainy days
- transit_from_prev.mode: "arrive" for first stop, "walk" ≤15 min, "uber" 15–30 min, "drive" >30 min
- Cluster stops geographically to minimise travel
- Hotel check-in is the first stop on Day 1 (arrival_time: "2:00 PM", poi_type: "accommodation")
- Hotel check-out is the last stop on the final day (arrival_time: "11:00 AM")
- Write tips that feel like insider knowledge, not a Wikipedia summary

Output format — respond with ONLY a JSON code block when ready:

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


_TOOL_LABELS = {
    "search_attractions":   "Exploring attractions",
    "search_restaurants":   "Finding places to eat",
    "search_hotels":        "Searching for accommodation",
    "get_weather_forecast": "Checking the weather forecast",
    "get_golden_hour":      "Calculating golden hour",
    "get_drive_time":       "Checking drive times",
}


# ── Groq streaming helper ─────────────────────────────────────────────────────

class _Msg:
    def __init__(self, content: str, tool_calls: list | None):
        self.content    = content
        self.tool_calls = tool_calls


class _TC:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id

        class _Fn:
            pass
        fn           = _Fn()
        fn.name      = name
        fn.arguments = arguments
        self.function = fn


async def _groq_call(client, system: str, messages: list) -> _Msg:
    """Stream openai/gpt-oss-120b and accumulate chunks into a _Msg."""
    stream = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "system", "content": system}] + messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=1,
        max_completion_tokens=8192,
        top_p=1,
        reasoning_effort="medium",
        stream=True,
    )

    content: str = ""
    tc_map: dict = {}  # index → {id, name, arguments}

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue
        delta = choice.delta

        if delta.content:
            content += delta.content

        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tc_map:
                    tc_map[idx] = {"id": "", "name": "", "arguments": ""}
                if tc_delta.id:
                    tc_map[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc_map[idx]["name"]      += tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc_map[idx]["arguments"] += tc_delta.function.arguments

    tool_calls = (
        [_TC(v["id"], v["name"], v["arguments"]) for v in tc_map.values()]
        if tc_map else None
    )
    return _Msg(content, tool_calls)


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
    from groq import AsyncGroq

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

    user_msg = (
        f"Plan a trip to {destination}.\n"
        f"Dates: {start_date} to {end_date} ({len(dates)} day{'s' if len(dates) > 1 else ''})\n"
        f"Destination coordinates: lat={lat}, lon={lon}\n"
        f"Trip dates list: {dates}\n"
        f"Interests: {', '.join(interests) if interests else 'general sightseeing'}\n"
        f"Flights URL: {flights_url}\n"
        f"Booking URL: {booking_url}"
    )

    messages = [{"role": "user", "content": user_msg}]

    yield _sse({"type": "start", "message": f"Planning your trip to {destination}…"})

    for iteration in range(MAX_ITERATIONS):
        msg = await _groq_call(groq_client, system, messages)

        # No tool calls → agent output the final plan
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

        # ── Execute ALL tool calls in parallel ────────────────────────────────
        # Emit progress for each tool the agent requested
        tool_names = [tc.function.name for tc in msg.tool_calls]
        for tool_name in tool_names:
            label = _TOOL_LABELS.get(tool_name, tool_name.replace("_", " ").title())
            yield _sse({"type": "step", "tool": tool_name, "message": f"{label}…"})

        # Parse all args first
        parsed_calls = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            parsed_calls.append((tc, args))

        # Run all tool calls in parallel
        results = await asyncio.gather(
            *[_exec_tool(tc.function.name, args) for tc, args in parsed_calls],
            return_exceptions=True,
        )

        tool_results = []
        for (tc, _), result in zip(parsed_calls, results):
            if isinstance(result, Exception):
                result = json.dumps({"error": str(result)})

            # Emit a summary for each completed tool
            try:
                parsed = json.loads(result)
                if isinstance(parsed, list):
                    yield _sse({"type": "result", "tool": tc.function.name,
                                "message": f"Found {len(parsed)} results"})
                elif isinstance(parsed, dict) and "duration_min" in parsed:
                    yield _sse({"type": "result", "tool": tc.function.name,
                                "message": f"{parsed.get('duration_min')} min · {parsed.get('distance_km')} km"})
                elif isinstance(parsed, dict) and "error" not in parsed:
                    yield _sse({"type": "result", "tool": tc.function.name, "message": "Done"})
            except Exception:
                pass

            tool_results.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result,
            })

        # Append assistant turn + all tool results before next iteration
        raw_tool_calls = [
            {
                "id":       tc.id,
                "type":     "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc, _ in parsed_calls
        ]
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": raw_tool_calls})
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
