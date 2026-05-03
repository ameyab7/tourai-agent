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
import os
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

_DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug_output")

_LOG_BUILTINS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


class _PipelineLogHandler(logging.Handler):
    """Captures log records for one pipeline run into a list."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        entry: dict = {
            "time":    logging.Formatter("%(asctime)s", "%H:%M:%S").formatTime(record, "%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        extra = {
            k: v for k, v in record.__dict__.items()
            if k not in _LOG_BUILTINS and not k.startswith("_")
        }
        if extra:
            entry["extra"] = extra
        self.records.append(entry)


def _write_debug_files(plan_id: str, plan_dict: dict, log_records: list[dict]) -> None:
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    with open(os.path.join(_DEBUG_DIR, f"plan_{plan_id}.json"), "w") as f:
        json.dump(plan_dict, f, indent=2, ensure_ascii=False)
    with open(os.path.join(_DEBUG_DIR, f"logs_{plan_id}.json"), "w") as f:
        json.dump(log_records, f, indent=2, ensure_ascii=False)

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.config import settings
from api.models import ItineraryRequest, ReplanRequest
from cache_module.keys import TTL, skeleton_key
from cache_module.ttl_cache import cache
from narration.narrator import narrate_day, narrate_trip
from prefetch.orchestrator import prefetch_all, AmbiguousDestinationError
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

    _log_handler = _PipelineLogHandler()
    logging.getLogger().addHandler(_log_handler)

    def _emit(payload: dict) -> str:
        return _sse(payload)

    logger.info(f"Starting itinerary pipeline for {destination!r}", extra={"req_id": req_id, "plan_id": plan_id})

    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(destination)}"
    booking_url = (
        f"https://www.booking.com/search.html?ss={quote_plus(destination)}"
        f"&checkin={start_date}&checkout={end_date}"
    )

    yield _emit({"type": "stage", "stage": "start", "req_id": req_id, "plan_id": plan_id,
                 "message": f"Planning your trip to {destination}…"})

    # ── Stage 0: cache lookup ────────────────────────────────────────────────
    skel_key = skeleton_key(destination, start_date, end_date, interests, pace, drive_tol_hrs)
    cached_skeleton_dict = await cache.get(skel_key)

    # ── Stage 1: prefetch ────────────────────────────────────────────────────
    yield _emit({"type": "stage", "stage": "prefetch", "message": "Gathering local data…"})
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    try:
        bundle = await prefetch_all(destination, dates, interests, settings.geoapify_api_key)
    except AmbiguousDestinationError as e:
        logging.getLogger().removeHandler(_log_handler)
        yield _emit({
            "type": "disambiguation",
            "message": f"Multiple places found for '{destination}'. Which one did you mean?",
            "options": [
                {
                    "display_name": opt.get("display_name", ""),
                    "country_code": opt.get("country_code", ""),
                    "state": opt.get("state", ""),
                }
                for opt in e.options
            ],
            "query": e.original_query,
        })
        return

    if bundle is None:
        logging.getLogger().removeHandler(_log_handler)
        yield _emit({"type": "error", "message": f"Could not find destination: {destination!r}"})
        return

    if bundle.fetch_errors:
        critical = ["attractions", "weather"]
        failed = [k for k in critical if k in bundle.fetch_errors]
        if failed:
            logging.getLogger().removeHandler(_log_handler)
            yield _emit({
                "type": "error",
                "message": f"Failed to fetch data: {', '.join(failed)}. Try again.",
                "details": bundle.fetch_errors,
            })
            return

    yield _emit({"type": "stage", "stage": "prefetch_done", "message": (
        f"Found {len(bundle.attractions)} attractions, "
        f"{len(bundle.restaurants)} restaurants, "
        f"{len(bundle.hotels)} hotels"
    )})

    # ── Stage 2: skeleton (cached or built) ──────────────────────────────────
    if cached_skeleton_dict is not None:
        skeleton = _skeleton_from_dict(cached_skeleton_dict)
        wx_by_date = {w.get("date"): w for w in bundle.weather}
        for day in skeleton.days:
            wx = wx_by_date.get(day.date)
            if wx:
                day.weather_is_clear = wx.get("is_clear")
        yield _emit({"type": "stage", "stage": "skeleton_cached",
                     "message": "Reusing cached plan structure…"})
    else:
        yield _emit({"type": "stage", "stage": "scoring",
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

    yield _emit({"type": "stage", "stage": "skeleton_done",
                 "message": f"Built {len(skeleton.days)}-day skeleton"})

    # ── Stage 3: narration (2 days at a time to stay under rate limit) ────────
    yield _emit({"type": "stage", "stage": "narration", "message": "Crafting your itinerary…"})

    trip_result = await narrate_trip(destination, interests, style, skeleton, bundle)
    if trip_result:
        yield _emit({"type": "trip", "trip": {
            "title":      trip_result.get("title"),
            "summary":    trip_result.get("summary"),
            "highlights": trip_result.get("highlights", []),
        }})

    day_results: list[dict | None] = [None] * len(skeleton.days)
    total_days = len(skeleton.days)

    for batch_start in range(0, total_days, 2):
        batch_end = min(batch_start + 2, total_days)
        batch_size = batch_end - batch_start

        yield _emit({"type": "narration_progress", "batch_start": batch_start, "batch_end": batch_end, "total": total_days})

        batch_tasks = [
            narrate_day(i, skeleton.days[i], bundle, interests)
            for i in range(batch_start, batch_end)
        ]
        batch_results = await asyncio.gather(*batch_tasks)

        for idx, result in enumerate(batch_results):
            global_idx = batch_start + idx
            day_results[global_idx] = result
            merged = _merge_day(global_idx, skeleton.days[global_idx], result, bundle)
            yield _emit({"type": "day", "day_index": global_idx, "day": merged.model_dump()})

    # ── Stage 4: validation & assembly ───────────────────────────────────────
    yield _emit({"type": "stage", "stage": "validation", "message": "Finalizing…"})
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
        logger.error(f"Plan validation failed for req {req_id} — could not finalize", extra={"exc": traceback.format_exc()})
        logging.getLogger().removeHandler(_log_handler)
        yield _emit({"type": "error", "message": "Could not finalize the plan."})
        return

    elapsed = round(time.perf_counter() - t_start, 2)
    logger.info(f"Pipeline complete for {destination!r} in {elapsed}s", extra={"req_id": req_id, "plan_id": plan_id})

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
    logging.getLogger().removeHandler(_log_handler)
    _write_debug_files(plan_id, plan_dict, _log_handler.records)

    yield _emit({"type": "complete", "plan": plan_dict, "plan_id": plan_id, "elapsed_s": elapsed})


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
            logger.warning(f"Could not load user profile — using request defaults: {exc}")

    async def _stream():
        try:
            async for chunk in run_pipeline(
                body.destination, body.start_date, body.end_date,
                interests, style, pace, drive_tol, user_id,
            ):
                yield chunk
        except Exception:
            logger.error("Pipeline stream error — sending error event to client", extra={"exc": traceback.format_exc()})
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
            logger.error("Replan stream error — sending error event to client", extra={"exc": traceback.format_exc()})
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
