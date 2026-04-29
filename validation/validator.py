"""tourai/validation/validator.py

Stage 4: assemble narrated output into the final plan, validate it, and repair
common failures deterministically.

Repair strategy (in escalating cost):
  1. Fill missing fields with defaults (free)
  2. Clamp invalid values (free)
  3. Re-narrate just the failing day (caller decides)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from narration.narrator import narrate_day
from prefetch.orchestrator import PrefetchBundle
from solver.skeleton import Skeleton, SkeletonDay

logger = logging.getLogger("tourai.validation")


# ── Output models ────────────────────────────────────────────────────────────

class FinalStop(BaseModel):
    poi_id: str
    name: str
    poi_type: str
    arrival_time: str
    duration_min: int
    is_meal: bool
    lat: float
    lon: float
    tip: str = ""
    best_time: str = ""
    crowd_level: str = "medium"
    skip_if_rushed: bool = False
    transit_from_prev: dict = Field(default_factory=lambda: {"mode": "walk", "duration_min": 0})

    @field_validator("crowd_level")
    @classmethod
    def _valid_crowd(cls, v: str) -> str:
        return v if v in {"low", "medium", "high"} else "medium"


class FinalDay(BaseModel):
    date: str
    day_label: str
    rain_plan: str = ""
    weather: dict = Field(default_factory=dict)
    stops: list[FinalStop]


class FinalPlan(BaseModel):
    title: str
    summary: str
    destination: str
    start_date: str
    end_date: str
    highlights: list[dict] = Field(default_factory=list)
    accommodation: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)
    days: list[FinalDay]


# ── Defensive coercion ────────────────────────────────────────────────────────

def _to_int(v: Any, default: int) -> int:
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        return int(m.group()) if m else default
    return default


def _to_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v).strip() or default


# ── Day-level merge ──────────────────────────────────────────────────────────

def _merge_day(
    day_index: int,
    skeleton_day: SkeletonDay,
    narration: dict | None,
    bundle: PrefetchBundle,
) -> FinalDay:
    """Merge skeleton (truth for structure) with narration (prose enrichment)."""
    narrated_stops_by_id: dict[str, dict] = {}
    if narration:
        for s in narration.get("stops", []):
            poi_id = s.get("poi_id")
            if poi_id:
                narrated_stops_by_id[poi_id] = s

    final_stops: list[FinalStop] = []
    for sk_stop in skeleton_day.stops:
        narr = narrated_stops_by_id.get(sk_stop.poi_id, {})

        # For meals, narration provides the actual restaurant name + lat/lon
        if sk_stop.is_meal and narr.get("name"):
            chosen_name = _to_str(narr["name"])
            rest = next(
                (r for r in bundle.restaurants if r["name"].lower() == chosen_name.lower()),
                None,
            )
            name = chosen_name
            lat = rest["lat"] if rest else bundle.lat
            lon = rest["lon"] if rest else bundle.lon
        else:
            name = sk_stop.name
            lat = sk_stop.lat
            lon = sk_stop.lon

        final_stops.append(FinalStop(
            poi_id=sk_stop.poi_id,
            name=name,
            poi_type=sk_stop.poi_type,
            arrival_time=sk_stop.arrival_time,
            duration_min=_to_int(sk_stop.duration_min, 60),
            is_meal=sk_stop.is_meal,
            lat=lat,
            lon=lon,
            tip=_to_str(narr.get("tip"), ""),
            best_time=_to_str(narr.get("best_time"), ""),
            crowd_level=_to_str(narr.get("crowd_level"), "medium"),
            skip_if_rushed=bool(sk_stop.skip_if_rushed),
            transit_from_prev={
                "mode": sk_stop.transit_mode,
                "duration_min": _to_int(sk_stop.transit_from_prev_min, 0),
            },
        ))

    weather = bundle.weather[day_index] if day_index < len(bundle.weather) else {}

    return FinalDay(
        date=skeleton_day.date,
        day_label=_to_str(narration.get("day_label") if narration else None, f"Day {day_index + 1}"),
        rain_plan=_to_str(narration.get("rain_plan") if narration else None, ""),
        weather={
            "description": weather.get("description", ""),
            "temp_high_c": weather.get("temp_high_c"),
            "temp_low_c":  weather.get("temp_low_c"),
            "is_clear":    weather.get("is_clear"),
        },
        stops=final_stops,
    )


# ── Full assembly ────────────────────────────────────────────────────────────

async def assemble_and_validate(
    destination: str,
    start_date: str,
    end_date: str,
    interests: list[str],
    skeleton: Skeleton,
    trip_narration: dict | None,
    day_narrations: list[dict | None],
    bundle: PrefetchBundle,
    *,
    repair_retries: int = 1,
) -> FinalPlan:
    """Merge skeleton + narration into a validated FinalPlan.

    On per-day narration failure, retries that day's narration once before
    falling back to a sparse (no tips) day. Trip-level failures get sparse
    defaults — not retried, because they're cheap to regenerate.
    """
    retry_indices = [i for i, n in enumerate(day_narrations) if n is None]
    if retry_indices and repair_retries > 0:
        logger.info("repair_retrying_days", extra={"days": retry_indices})
        retried = await asyncio.gather(*[
            narrate_day(i, skeleton.days[i], bundle, interests)
            for i in retry_indices
        ])
        for idx, result in zip(retry_indices, retried):
            day_narrations[idx] = result

    final_days = [
        _merge_day(i, skeleton.days[i], day_narrations[i], bundle)
        for i in range(len(skeleton.days))
    ]

    trip = trip_narration or {}
    plan = FinalPlan(
        title=_to_str(trip.get("title"), f"Your trip to {destination}"),
        summary=_to_str(trip.get("summary"), ""),
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        highlights=trip.get("highlights") or [],
        accommodation={
            "name":   skeleton.hotel["name"] if skeleton.hotel else "",
            "stars":  skeleton.hotel.get("stars", "") if skeleton.hotel else "",
            "reason": _to_str(trip.get("accommodation_reason"), ""),
        },
        budget=trip.get("budget") or {},
        days=final_days,
    )

    _audit_constraints(plan, drive_tol_min=skeleton.diagnostics.get("drive_tol_min", 120))
    return plan


def _audit_constraints(plan: FinalPlan, drive_tol_min: int) -> None:
    """Log violations so you can monitor quality in production."""
    issues: list[str] = []
    for day in plan.days:
        meals = sum(1 for s in day.stops if s.is_meal)
        if meals < 1:
            issues.append(f"{day.date}: no meals")
        for s in day.stops:
            if s.transit_from_prev["duration_min"] > drive_tol_min:
                issues.append(
                    f"{day.date} {s.name}: drive {s.transit_from_prev['duration_min']}min"
                    f" > tol {drive_tol_min}"
                )
    if issues:
        logger.warning("plan_audit_issues", extra={"issues": issues})
