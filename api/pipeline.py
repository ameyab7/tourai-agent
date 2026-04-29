"""tourai/api/pipeline.py

The top-level pipeline that wires Stages 0-4 with progressive SSE streaming.

Streaming strategy:
  - "stage" events for each pipeline stage (UI shows progress)
  - "day" events as each day's narration completes (UI fills in days progressively)
  - "trip" event when trip-level narration completes
  - "complete" event with the validated full plan at the end
  - "error" event on fatal failures

The user sees Day 1 within ~5-7s of submitting, even though the full plan takes
12-18s. This is the perceived-latency win.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.config import settings
from api.models import ItineraryRequest, ReplanRequest
from cache.keys import TTL, skeleton_key
from cache.ttl_cache import cache
from narration.narrator import narrate_day, narrate_trip
from prefetch.orchestrator import prefetch_all
from solver.scorer import score_pois
from solver.skeleton import Skeleton, SkeletonDay, SkeletonStop, build_skeleton
from storage.plan_store import PlanSnapshot, _serialize_bundle, plan_store
from validation.validator import _merge_day, assemble_and_validate

router = APIRouter()
logger = logging.getLogger("tourai.pipeline")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _skeleton_to_dict(skel: Skeleton) -> dict:
    """Serialize skeleton for cache storage."""
    return {
        "days": [
            {
                "date": d.date,
                "weekday": d.weekday,
                "weather_is_clear": d.weather_is_clear,
                "stops": [
                    {
                        "poi_id": s.poi_id, "name": s.name, "poi_type": s.poi_type,
                        "lat": s.lat, "lon": s.lon, "arrival_time": s.arrival_time,
                        "duration_min": s.duration_min, "is_meal": s.is_meal,
                        "transit_from_prev_min": s.transit_from_prev_min,
                        "transit_mode": s.transit_mode, "skip_if_rushed": s.skip_if_rushed,
                    }
                    for s in d.stops
                ],
            }
            for d in skel.days
        ],
        "hotel": skel.hotel,
        "diagnostics": skel.diagnostics,
    }


def _skeleton_from_dict(d: dict) -> Skeleton:
    return Skeleton(
        days=[
            SkeletonDay(
                date=day["date"],
                weekday=day["weekday"],
                weather_is_clear=day.get("weather_is_clear"),
                stops=[SkeletonStop(**s) for s in day["stops"]],
            )
            for day in d["days"]
        ],
        hotel=d.get("hotel"),
        diagnostics=d.get("diagnostics", {}),
    )


# ── The pipeline ──────────────────────────────────────────────────────────────

async def run_pipeline(
    destination: str,
    start_date: str,
    end_date: str,
    interests: list[str],
    style: str,
    pace: str,
    drive_tol_hrs: float,
    user_id: str | None = None,
):
    """Async generator yielding SSE events through Stages 0-4."""
    plan_id = uuid.uuid4().hex
    req_id = plan_id[:8]
    t_start = time.perf_counter()
    logger.info("pipeline_start", extra={"req_id": req_id, "plan_id": plan_id, "destination": destination})

    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(destination)}"
    booking_url = (
        f"https://www.booking.com/search.html?ss={quote_plus(destination)}"
        f"&checkin={start_date}&checkout={end_date}"
    )

    yield _sse({"type": "stage", "stage": "start", "req_id": req_id, "plan_id": plan_id,
                "message": f"Planning your trip to {destination}…"})

    # ── Stage 0: cache lookup ────────────────────────────────────────────────
    skel_key = skeleton_key(destination, start_date, end_date, interests, pace, drive_tol_hrs)
    cached_skeleton_dict = await cache.get(skel_key)

    # ── Stage 1: prefetch ────────────────────────────────────────────────────
    yield _sse({"type": "stage", "stage": "prefetch", "message": "Gathering local data…"})
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    bundle = await prefetch_all(destination, dates, interests, settings.geoapify_api_key)
    if bundle is None:
        yield _sse({"type": "error", "message": f"Could not find destination: {destination!r}"})
        return

    yield _sse({"type": "stage", "stage": "prefetch_done", "message": (
        f"Found {len(bundle.attractions)} attractions, "
        f"{len(bundle.restaurants)} restaurants, "
        f"{len(bundle.hotels)} hotels"
    )})

    # ── Stage 2: skeleton (cached or built) ──────────────────────────────────
    if cached_skeleton_dict is not None:
        skeleton = _skeleton_from_dict(cached_skeleton_dict)
        # Re-attach weather flags from current bundle (skeleton cache is stale on weather)
        wx_by_date = {w.get("date"): w for w in bundle.weather}
        for day in skeleton.days:
            wx = wx_by_date.get(day.date)
            if wx:
                day.weather_is_clear = wx.get("is_clear")
        yield _sse({"type": "stage", "stage": "skeleton_cached",
                    "message": "Reusing cached plan structure…"})
    else:
        yield _sse({"type": "stage", "stage": "scoring",
                    "message": "Matching attractions to your interests…"})
        scores = await score_pois(bundle.attractions, interests)
        skeleton = build_skeleton(
            bundle=bundle,
            start_date=start_date,
            end_date=end_date,
            interests=interests,
            pace=pace,
            drive_tol_hrs=drive_tol_hrs,
            poi_scores=scores or None,
        )
        await cache.set(skel_key, _skeleton_to_dict(skeleton), TTL.SKELETON)

    yield _sse({"type": "stage", "stage": "skeleton_done",
                "message": f"Built {len(skeleton.days)}-day skeleton"})

    # ── Stage 3: narration (parallel, stream as each completes) ──────────────
    yield _sse({"type": "stage", "stage": "narration", "message": "Crafting your itinerary…"})

    trip_task = asyncio.create_task(narrate_trip(destination, interests, style, skeleton, bundle))
    day_tasks: list[asyncio.Task] = [
        asyncio.create_task(narrate_day(i, day, bundle, interests))
        for i, day in enumerate(skeleton.days)
    ]

    day_results: list[dict | None] = [None] * len(day_tasks)
    pending: set[asyncio.Task] = set(day_tasks) | {trip_task}
    trip_result: dict | None = None

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for finished in done:
            result = await finished
            if finished is trip_task:
                trip_result = result
                if trip_result:
                    yield _sse({"type": "trip", "trip": {
                        "title":      trip_result.get("title"),
                        "summary":    trip_result.get("summary"),
                        "highlights": trip_result.get("highlights", []),
                    }})
            else:
                idx = day_tasks.index(finished)
                day_results[idx] = result
                merged = _merge_day(idx, skeleton.days[idx], day_results[idx], bundle)
                yield _sse({"type": "day", "day_index": idx, "day": merged.model_dump()})

    # ── Stage 4: validation & assembly ───────────────────────────────────────
    yield _sse({"type": "stage", "stage": "validation", "message": "Finalizing…"})
    try:
        final_plan = await assemble_and_validate(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            interests=interests,
            skeleton=skeleton,
            trip_narration=trip_result,
            day_narrations=day_results,
            bundle=bundle,
        )
    except Exception:
        logger.error("validation_failed", extra={"req_id": req_id, "exc": traceback.format_exc()})
        yield _sse({"type": "error", "message": "Could not finalize the plan."})
        return

    elapsed = round(time.perf_counter() - t_start, 2)
    logger.info("pipeline_complete", extra={"req_id": req_id, "plan_id": plan_id, "elapsed_s": elapsed})

    plan_dict = final_plan.model_dump()
    plan_dict["getting_there"] = {"flights_url": flights_url}
    plan_dict["accommodation"]["booking_url"] = booking_url

    snapshot = PlanSnapshot(
        plan_id=plan_id,
        user_id=user_id,
        created_at=datetime.now(tz=timezone.utc),
        request=ItineraryRequest(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            interests=interests,
            travel_style=style,
            pace=pace,
            drive_tolerance_hrs=drive_tol_hrs,
        ),
        skeleton_dict=_skeleton_to_dict(skeleton),
        bundle_dict=_serialize_bundle(bundle),
        final_plan=plan_dict,
    )
    await plan_store.save(plan_id, snapshot)

    yield _sse({"type": "complete", "plan": plan_dict, "plan_id": plan_id, "elapsed_s": elapsed})


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v2/itinerary/stream")
async def stream_itinerary_v2(
    body: ItineraryRequest,
    authorization: str | None = Header(default=None),
):
    interests = list(body.interests or [])
    style     = body.travel_style      if body.travel_style      is not None else "solo"
    pace      = body.pace              if body.pace              is not None else "balanced"
    drive_tol = body.drive_tolerance_hrs if body.drive_tolerance_hrs is not None else 2.0

    user_id: str | None = None
    if authorization and authorization.startswith("Bearer "):
        try:
            profile = await asyncio.to_thread(_load_profile_sync, authorization)
            if profile:
                user_id = profile.get("user_id")
                if not interests:
                    interests = profile.get("interests") or []
                if body.travel_style is None:
                    style = profile.get("travel_style") or style
                if body.pace is None:
                    pace = profile.get("pace") or pace
                if body.drive_tolerance_hrs is None:
                    drive_tol = float(profile.get("drive_tolerance_hrs") or drive_tol)
        except Exception as exc:
            logger.warning("profile_load_failed", extra={"error": str(exc)})

    async def _stream():
        try:
            async for chunk in run_pipeline(
                body.destination, body.start_date, body.end_date,
                interests, style, pace, drive_tol, user_id,
            ):
                yield chunk
        except Exception:
            logger.error("pipeline_stream_error", extra={"exc": traceback.format_exc()})
            yield _sse({"type": "error", "message": "Something went wrong."})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/v2/itinerary/{plan_id}/replan")
async def replan_itinerary(plan_id: str, body: ReplanRequest):
    from api.replan_pipeline import run_replan_pipeline

    async def _stream():
        try:
            async for chunk in run_replan_pipeline(plan_id, body):
                yield chunk
        except Exception:
            logger.error("replan_stream_error", extra={"exc": traceback.format_exc()})
            yield _sse({"type": "error", "message": "Something went wrong."})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _load_profile_sync(authorization: str) -> dict | None:
    """Sync Supabase call — must be run via asyncio.to_thread."""
    from api.supabase_client import get_supabase
    sb = get_supabase()
    token = authorization.removeprefix("Bearer ").strip()
    user = sb.auth.get_user(token).user
    if not user:
        return None
    result = (
        sb.table("profiles")
        .select("interests,travel_style,pace,drive_tolerance_hrs")
        .eq("user_id", str(user.id))
        .execute()
    )
    profile = result.data[0] if result.data else {}
    return {**profile, "user_id": str(user.id)}
