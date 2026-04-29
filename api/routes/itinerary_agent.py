"""api/routes/itinerary_agent.py — Skeleton-first itinerary pipeline.

Architecture:
  Stage 1 — parallel pre-fetch via prefetch.orchestrator (free, ~2-3 s)
  Stage 2 — deterministic skeleton via solver.skeleton (spatial clustering,
             nearest-neighbour TSP, clock layout) — no LLM, no tokens
  Stage 3 — single Cerebras call for narration only (tips, crowd, hours,
             restaurant names) — LLM does language, not scheduling
"""

import asyncio
import json
import logging
import traceback
import uuid
from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.config import settings
from api.models import ItineraryRequest
from narration.narrator import narrate_all
from prefetch.orchestrator import PrefetchBundle, get_http_client, prefetch_all
from solver.scorer import score_pois
from solver.skeleton import Skeleton, build_skeleton
from validation.validator import assemble_and_validate

router = APIRouter()
logger = logging.getLogger("tourai.api")

# ── Legacy planning prompt (kept for reference) ───────────────────────────────

def _build_system_prompt(interests: list[str], pace: str, style: str, drive_tol_hrs: float) -> str:
    stops_per_day = {"relaxed": "2–3", "balanced": "3–4", "packed": "4–5"}.get(pace, "3–4")
    return f"""/no_think
You are TourAI, an expert AI travel agent and local insider. Build a complete, trustworthy trip plan that makes the traveller feel like they have a knowledgeable local friend guiding them — not a generic tourist package.

Traveller profile:
  Interests: {', '.join(interests) if interests else 'general sightseeing'}
  Pace: {pace} ({stops_per_day} activity stops per day, not counting meals)
  Travelling as: {style}
  Max drive between stops: {drive_tol_hrs} hours

── MUST-SEE RULE ──
Include a "highlights" array of 2–3 iconic, unmissable spots for the destination — places that locals would say "you absolutely have to go." Include these in the day stops too, even if they don't perfectly match stated interests. These are the places a friend back home will ask about.

── PLANNING RULES ──
- Every day must include breakfast, lunch, and dinner stops (is_meal: true)
- Never recommend global fast-food chains (McDonald's, Burger King, KFC, Starbucks, Subway, etc.) — always local restaurants, neighbourhood cafés, or bakeries
- Schedule outdoor/photography spots on clear days; pivot to museums/galleries/indoor on rainy days
- Cluster stops geographically to minimise travel time
- transit_from_prev.mode: "arrive" for first stop, "walk" ≤15 min, "uber" 15–30 min, "drive" >30 min
- Hotel check-in is the first stop on Day 1 (arrival_time: "2:00 PM", poi_type: "accommodation")
- Hotel check-out is the last stop on the final day (arrival_time: "11:00 AM")

── TIP QUALITY ──
Tips must answer WHY this place matters — the story, the history, what makes it special to locals, the one thing to look for. Never generic descriptions. One sentence of real insider knowledge beats three sentences of Wikipedia.

── CROWD & TIMING ──
For every non-meal stop set best_time (e.g. "Before 9 AM to beat crowds", "Sunset for the best light") and crowd_level ("low", "medium", or "high" — based on time of day and day of week in the itinerary).

── OPENING HOURS ──
For every non-meal stop set opening_hours_note (e.g. "Open Tue–Sun 10 AM–6 PM, closed Mondays" or "Open 24/7"). Flag any stop that is commonly closed on the scheduled day.

── SKIP IF RUSHED ──
Mark skip_if_rushed: true on 1 stop per day — the one to drop if they're running late. Never mark must-see highlights as skippable.

── RAIN BACKUP ──
Every day must have a rain_plan — a 1-sentence fallback ("If it rains: head to [indoor alternative] instead of the outdoor stops").

── BUDGET REALISM ──
Budget estimates must be realistic for the destination and travel style. Include a budget_notes field with honest caveats (e.g. "Entrance fees add up fast — book online to skip queues and save 10%").

Respond with ONLY a ```json code block containing:
{{
  title, summary,
  highlights: [{{name, why_cant_skip, emoji}}],
  getting_there: {{notes, drive_time_min, drive_distance_km, flights_url}},
  accommodation: {{recommended_area, area_reason, booking_url, options: [{{name, tier, est_price_usd_per_night}}]}},
  budget: {{accommodation_usd, food_usd, activities_usd, transport_usd, total_usd, notes}},
  days: [{{
    date, day_label,
    weather: {{description, temp_high_c, temp_low_c, is_clear}},
    rain_plan,
    stops: [{{
      name, poi_type, tip, arrival_time, duration_min, is_meal, lat, lon,
      best_time, crowd_level, opening_hours_note, skip_if_rushed,
      transit_from_prev: {{mode, duration_min, notes}}
    }}]
  }}]
}}"""


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"




# ── Main generator ────────────────────────────────────────────────────────────

async def _run_planner(
    bundle: PrefetchBundle,
    start_date: str,
    end_date: str,
    interests: list[str],
    style: str,
    pace: str,
    drive_tol: float,
):
    destination = bundle.display_name
    req_id = uuid.uuid4().hex[:8]
    logger.info("planner_request_start", extra={"req_id": req_id, "destination": destination})

    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(destination)}"
    booking_url = (
        f"https://www.booking.com/search.html?ss={quote_plus(destination)}"
        f"&checkin={start_date}&checkout={end_date}"
    )

    d0    = date.fromisoformat(start_date)
    d1    = date.fromisoformat(end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    yield _sse({"type": "start", "message": f"Planning your trip to {destination}…"})
    yield _sse({"type": "result", "tool": "prefetch", "message": (
        f"Found {len(bundle.attractions)} attractions · "
        f"{len(bundle.restaurants)} restaurants · "
        f"{len(bundle.hotels)} hotels"
    )})

    # ── Stage 2: score POIs (fast Cerebras call) + build skeleton ──────────
    # score_pois runs concurrently while we emit the step message; if it
    # times out or fails, build_skeleton falls back to the keyword heuristic.
    yield _sse({"type": "step", "tool": "score_pois", "message": "Matching attractions to your interests…"})
    poi_scores = await score_pois(bundle.attractions, interests)
    if poi_scores:
        logger.info("scorer_used", extra={"req_id": req_id, "scored": len(poi_scores)})
    else:
        logger.info("scorer_fallback_heuristic", extra={"req_id": req_id})

    skeleton = build_skeleton(bundle, start_date, end_date, interests, pace, drive_tol, poi_scores=poi_scores or None)
    logger.info("skeleton_complete", extra={
        "req_id": req_id, **skeleton.diagnostics,
    })
    yield _sse({"type": "step", "tool": "narrate", "message": "Writing your trip narrative…"})

    # ── Stage 3: parallel narration (Groq — one call per day + one trip-level) ─
    try:
        trip_narration, day_narrations = await narrate_all(
            destination, interests, style, skeleton, bundle
        )
        plan = await assemble_and_validate(
            destination, start_date, end_date, interests,
            skeleton, trip_narration, day_narrations, bundle,
        )
    except Exception as exc:
        logger.error("narration_failed", extra={"req_id": req_id, "error": str(exc)})
        yield _sse({"type": "error", "message": "Narration failed. Please try again."})
        return

    logger.info("plan_built", extra={"req_id": req_id, "days": len(plan.days)})
    plan_dict = plan.model_dump()
    plan_dict["getting_there"] = {"flights_url": flights_url}
    plan_dict["accommodation"]["booking_url"] = booking_url
    yield _sse({"type": "complete", "plan": plan_dict})


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v1/itinerary/stream")
async def stream_itinerary(
    body: ItineraryRequest,
    authorization: str | None = Header(default=None),
):
    interests = list(body.interests)
    style     = body.travel_style
    pace      = body.pace
    drive_tol = body.drive_tolerance_hrs

    if authorization and authorization.startswith("Bearer "):
        try:
            from api.supabase_client import get_supabase
            token = authorization.removeprefix("Bearer ").strip()

            def _load_profile() -> dict | None:
                sb   = get_supabase()
                user = sb.auth.get_user(token).user
                if not user:
                    return None
                result = (
                    sb.table("profiles")
                    .select("interests,travel_style,pace,drive_tolerance_hrs")
                    .eq("user_id", str(user.id))
                    .execute()
                )
                return result.data[0] if result.data else None

            p = await asyncio.to_thread(_load_profile)
            if p:
                if not interests:     interests = p.get("interests") or []
                if style is None:     style     = p.get("travel_style")
                if pace is None:      pace      = p.get("pace")
                if drive_tol is None: drive_tol = p.get("drive_tolerance_hrs")
        except Exception as exc:
            logger.warning("profile_load_failed", extra={"error": str(exc)})

    # Apply hardcoded defaults only after profile merge — never override explicit user input
    if style is None:    style     = "solo"
    if pace is None:     pace      = "balanced"
    if drive_tol is None: drive_tol = 2.0

    d0    = date.fromisoformat(body.start_date)
    d1    = date.fromisoformat(body.end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    bundle = await prefetch_all(body.destination, dates, interests, settings.geoapify_api_key)
    if bundle is None:
        async def _err():
            yield _sse({"type": "error", "message": f"Could not find destination: {body.destination!r}"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    async def _stream():
        try:
            async for chunk in _run_planner(bundle, body.start_date, body.end_date,
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
