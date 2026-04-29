"""tourai/narration/narrator.py

Stage 3: narrate the skeleton. Two parallel work streams:

  A) Per-day narration: tips, rain_plan, day_label, restaurant picks for meals
  B) Trip-level narration: title, summary, highlights, area description, budget notes

Both use Groq (Llama 3.3 70B) for warm prose. Cerebras would also work but Groq's
model is tuned better for creative writing IMO. Easy to swap.

Each call is small and focused — schema validation is reliable on small outputs.
If day 3 fails, we retry day 3, not the whole plan.
"""

from __future__ import annotations

import asyncio
import json
import logging

from groq import AsyncGroq

from api.config import settings
from prefetch.orchestrator import PrefetchBundle
from replan.mutator import summarize_mutation
from solver.skeleton import Skeleton, SkeletonDay

logger = logging.getLogger("tourai.narration")

_NARRATION_MODEL = "llama-3.3-70b-versatile"


# ── Per-day prompt ────────────────────────────────────────────────────────────

_DAY_SYSTEM = """You are TourAI, a knowledgeable local friend writing one day of a trip.

Your job is to add warm, specific commentary to a pre-planned day. The schedule
is already decided — DO NOT change times, durations, or which places are visited.
Only add prose: tips, picks for meal stops, a day label, and a rain backup.

Rules for tips:
- Answer WHY this place matters — story, history, what locals love
- One sentence of insider knowledge beats three sentences of Wikipedia
- Never generic ("a beautiful park") — always specific ("the bench under the
  third cypress tree has the best skyline view at sunset")

Rules for meal picks:
- You will be given a list of nearby restaurants
- Pick the most fitting option for each meal slot in this day
- Never pick global chains (McDonald's, Starbucks, etc.)
- Match the cuisine to the day's vibe

Return ONLY valid JSON, no markdown.
"""

_DAY_OUTPUT_SCHEMA = """{
  "day_label": "Day 1 — Arrival & First Impressions",
  "rain_plan": "If it rains: head to [indoor alternative] instead of the outdoor stops",
  "stops": [
    {"poi_id": "a0", "tip": "...", "best_time": "Before 9 AM to beat crowds", "crowd_level": "low"},
    {"poi_id": "meal-lunch-2026-05-01", "name": "Pizzeria da Michele", "tip": "..."}
  ]
}"""


def _build_day_prompt(
    day_index: int,
    day: SkeletonDay,
    bundle: PrefetchBundle,
    interests: list[str],
) -> str:
    schedule = []
    for s in day.stops:
        item: dict = {
            "poi_id": s.poi_id,
            "name": s.name,
            "type": s.poi_type,
            "time": s.arrival_time,
            "duration_min": s.duration_min,
        }
        if s.is_meal:
            item["needs"] = "meal_pick"
            item["meal"] = s.poi_id.split("-")[1]  # breakfast/lunch/dinner
        schedule.append(item)

    weather_line = (
        f"Weather: {bundle.weather[day_index].get('description', '?')} "
        f"({'clear' if day.weather_is_clear else 'not clear'})"
        if day_index < len(bundle.weather)
        else "Weather: unavailable"
    )

    restaurants_compact = [
        {"name": r["name"], "cuisine": r.get("cuisine", "")}
        for r in bundle.restaurants[:8]
    ]

    return f"""Day {day_index + 1} of the trip.
Date: {day.date}
{weather_line}
Traveller interests: {', '.join(interests) if interests else 'general sightseeing'}

Schedule (FIXED — do not change):
{json.dumps(schedule, ensure_ascii=False, indent=2)}

Nearby restaurants to choose from for meal slots:
{json.dumps(restaurants_compact, ensure_ascii=False, indent=2)}

Return JSON matching this schema:
{_DAY_OUTPUT_SCHEMA}

For EVERY stop in the schedule above, include a stops entry with the same poi_id.
For meal stops, also include a 'name' field with the chosen restaurant."""


# ── Trip-level prompt ─────────────────────────────────────────────────────────

_TRIP_SYSTEM = """You are TourAI, writing the high-level overview for a trip.

You'll see the full schedule. Write the title, summary, must-see highlights,
accommodation reasoning, and budget notes. Warm, specific, never generic.

Return ONLY valid JSON, no markdown.
"""

_TRIP_OUTPUT_SCHEMA = """{
  "title": "Three Days of Tacos and Trails in Austin",
  "summary": "A laid-back long weekend mixing live music, breakfast tacos, and Hill Country views",
  "highlights": [
    {"name": "Barton Springs Pool", "why_cant_skip": "...", "emoji": "🌊"}
  ],
  "accommodation_reason": "South Congress puts you walking distance to the food trucks and a short Uber from downtown shows.",
  "budget": {
    "accommodation_usd": 450,
    "food_usd": 240,
    "activities_usd": 80,
    "transport_usd": 60,
    "total_usd": 830,
    "notes": "Music venue covers add up — buy tickets in advance to skip walk-up surcharges."
  }
}"""


def _build_trip_prompt(
    destination: str,
    interests: list[str],
    style: str,
    skeleton: Skeleton,
    bundle: PrefetchBundle,
) -> str:
    overview = []
    for day in skeleton.days:
        names = [s.name for s in day.stops if not s.is_meal and s.poi_type != "accommodation"]
        overview.append({"date": day.date, "stops": names})

    return f"""Destination: {destination}
Travelling as: {style}
Interests: {', '.join(interests) if interests else 'general sightseeing'}
Hotel: {skeleton.hotel['name'] if skeleton.hotel else 'TBD'}

Trip overview (already planned):
{json.dumps(overview, ensure_ascii=False, indent=2)}

Return JSON matching this schema:
{_TRIP_OUTPUT_SCHEMA}

The highlights array must include 2-3 of the most iconic stops from the overview above."""


# ── LLM call helper ───────────────────────────────────────────────────────────

async def _call_groq(system: str, user: str, max_tokens: int, label: str) -> dict | None:
    client = AsyncGroq(api_key=settings.groq_api_key)
    try:
        resp = await client.chat.completions.create(
            model=_NARRATION_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        if resp.choices[0].finish_reason == "length":
            logger.warning("narration_truncated", extra={"label": label, "tokens": max_tokens})
        return json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("narration_parse_failed", extra={"label": label, "error": str(exc)})
        return None
    except Exception as exc:
        logger.warning("narration_call_failed", extra={"label": label, "error": str(exc)})
        return None


# ── Public entry points ───────────────────────────────────────────────────────

async def narrate_day(
    day_index: int,
    day: SkeletonDay,
    bundle: PrefetchBundle,
    interests: list[str],
) -> dict | None:
    prompt = _build_day_prompt(day_index, day, bundle, interests)
    return await _call_groq(_DAY_SYSTEM, prompt, max_tokens=1500, label=f"day_{day_index}")


async def narrate_replanned_day(
    day_index: int,
    day: SkeletonDay,
    bundle: PrefetchBundle,
    interests: list[str],
    mutation_log: dict,
) -> dict | None:
    reason = mutation_log.get("reason", "unknown")
    summary = summarize_mutation(mutation_log)

    system = (
        _DAY_SYSTEM.rstrip()
        + f"\n\nThis day was just changed because of: {reason}. The day_label and one of "
        "the tips should naturally acknowledge the change without making it dramatic. "
        "Examples: 'Day 2 — Indoor edition (since the rain rolled in)', or "
        "'Day 3 — A slower pace today'. Don't over-apologize or over-explain. "
        "One light reference is enough."
    )
    user = (
        f"[REPLAN] This day was just regenerated. Reason: {reason}. Changes: {summary}\n\n"
        + _build_day_prompt(day_index, day, bundle, interests)
    )
    return await _call_groq(system, user, max_tokens=1500, label=f"replan_day_{day_index}")


async def narrate_trip(
    destination: str,
    interests: list[str],
    style: str,
    skeleton: Skeleton,
    bundle: PrefetchBundle,
) -> dict | None:
    prompt = _build_trip_prompt(destination, interests, style, skeleton, bundle)
    return await _call_groq(_TRIP_SYSTEM, prompt, max_tokens=1200, label="trip")


async def narrate_all(
    destination: str,
    interests: list[str],
    style: str,
    skeleton: Skeleton,
    bundle: PrefetchBundle,
) -> tuple[dict | None, list[dict | None]]:
    """Run trip-level + all per-day narrations concurrently.

    Returns whatever succeeded; failures are None and _merge_plan handles fallbacks.
    """
    trip_task = asyncio.create_task(
        narrate_trip(destination, interests, style, skeleton, bundle)
    )
    day_tasks = [
        asyncio.create_task(narrate_day(i, day, bundle, interests))
        for i, day in enumerate(skeleton.days)
    ]
    trip_result = await trip_task
    day_results = await asyncio.gather(*day_tasks)
    return trip_result, list(day_results)
