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
import os

import aiohttp
import groq

from api.config import settings
from prefetch.orchestrator import PrefetchBundle
from replan.mutator import summarize_mutation
from solver.skeleton import Skeleton, SkeletonDay

logger = logging.getLogger("tourai.narration")

_USE_GEMINI = bool(settings.gemini_api_key)
_GEMINI_MODEL = "gemma-4-26b-a4b-it"
_GROQ_MODEL = "llama-3.3-70b-versatile"

if _USE_GEMINI:
    logger.info(f"Using Gemini API with model {_GEMINI_MODEL}")
else:
    logger.info(f"Using Groq with model {_GROQ_MODEL}")


# ── Per-day prompt ────────────────────────────────────────────────────────────

_DAY_SYSTEM = """You are TourAI, a knowledgeable local friend writing one day of a trip.

Your job is to add warm, specific commentary to a pre-planned day. The schedule
is already fixed — DO NOT change times, durations, or which places are visited.
Output only: tips, meal picks, a day label, rain backup, crowd levels, best times, opening hours.

── TIP RULES ──
Write tips like a local friend texting you before you leave — specific, surprising, actionable.
NEVER describe what a place is. NEVER use tourist-brochure language.
Tell the traveller what to DO, what to NOTICE, or what most people MISS.

Good tips (copy this register exactly):
  ✓ "Skip the main entrance queue — the side gate on Via della Croce opens 15 min early and is always empty."
  ✓ "Stand on the east terrace exactly at 5 PM — the light hits the canyon wall and turns it deep red for about 8 minutes."
  ✓ "The basement level has the oldest mosaics and almost no one goes down there."
  ✓ "Ask for a table by the kitchen pass — you can watch the chefs work and they always send out extra courses."
  ✓ "The gift shop sells a fold-out map of the hidden courtyards that isn't available anywhere else."

Bad tips (never write like this):
  ✗ "A stunning example of baroque architecture with a rich history dating back to the 17th century."
  ✗ "Visitors will enjoy the impressive views and vibrant local atmosphere."
  ✗ "This iconic landmark is a must-see for anyone visiting the city."
  ✗ "A great place to relax and take in the scenery."

── CROWD LEVEL RULES ──
Set crowd_level to "low", "medium", or "high" using this logic:
  - Weekday (Mon–Thu) before 10 AM or after 4 PM → low
  - Weekday midday (10 AM–2 PM) → medium
  - Friday or weekend morning → medium
  - Friday or weekend afternoon (12 PM–5 PM) → high
  - Famous landmarks (museums, cathedrals, main squares) → bump one level higher

── BEST TIME RULES ──
Be specific. Not "morning" — "before 9 AM" or "just after opening".
Reference light conditions, crowd patterns, or temperature when relevant.
Examples: "Golden hour (about 45 min before sunset)", "Right at opening — crowds arrive by 10 AM",
"Midweek afternoon — tour groups are gone by 3 PM"

── OPENING HOURS RULES ──
Use your world knowledge. Be specific: "Open Tue–Sun 10 AM–6 PM, closed Mondays" not "check before visiting".
If you are genuinely uncertain for a specific location, write "Verify hours before visiting".

── MEAL RULES ──
- Pick only from the provided restaurant list
- Never pick global chains (McDonald's, Starbucks, KFC, etc.)
- Match the cuisine to the day's neighbourhood and vibe
- Meal tip: one specific dish, drink, or ordering trick — never "the food is great"

Return ONLY valid JSON matching the schema. No markdown, no commentary outside the JSON.
"""

_DAY_OUTPUT_SCHEMA = """{
  "day_label": "Day 1 — The Strip and Downtown",
  "rain_plan": "If it rains: swap the outdoor walk for the Mob Museum — it's two blocks away and takes 2 hours",
  "stops": [
    {
      "poi_id": "a0",
      "tip": "Go straight to the observation deck before 9 AM — the Strip looks completely different before the crowds arrive and the light is perfect for photos.",
      "best_time": "Before 9 AM for empty floors and morning light",
      "crowd_level": "low",
      "opening_hours_note": "Open daily 9 AM–9 PM, last entry 8:30 PM"
    },
    {
      "poi_id": "meal-dinner-2026-05-01",
      "name": "Lotus of Siam",
      "tip": "Order the Northern Thai menu — it's printed separately and most people never see it. The khao soi is the best in the city."
    }
  ]
}"""


_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _build_day_prompt(
    day_index: int,
    day: SkeletonDay,
    bundle: PrefetchBundle,
    interests: list[str],
) -> str:
    # Build a tag lookup from the bundle so we can enrich each stop
    tag_lookup: dict[str, dict] = {
        p["poi_id"]: p.get("tags", {}) for p in bundle.attractions
    }

    schedule = []
    for s in day.stops:
        item: dict = {
            "poi_id":       s.poi_id,
            "name":         s.name,
            "type":         s.poi_type,
            "time":         s.arrival_time,
            "duration_min": s.duration_min,
        }
        if s.is_meal:
            item["needs"] = "meal_pick"
            item["meal"]  = s.poi_id.split("-")[1]
        else:
            tags = tag_lookup.get(s.poi_id, {})
            if tags:
                item["tags"] = tags
        schedule.append(item)

    weekday = _WEEKDAY_NAMES[day.weekday] if 0 <= day.weekday <= 6 else "Unknown"

    if day_index < len(bundle.weather):
        wx = bundle.weather[day_index]
        if wx.get("available", True):
            weather_line = (
                f"Weather: {wx.get('description', '?')} "
                f"({'clear' if wx.get('is_clear') else ('not clear' if wx.get('is_clear') is False else 'unknown')})"
            )
        else:
            weather_line = (
                "Weather: Forecast unavailable (trip is more than 16 days out). "
                "Generate a generic rain_plan that works for any weather."
            )
    else:
        weather_line = (
            "Weather: Forecast unavailable (trip is more than 16 days out). "
            "Generate a generic rain_plan that works for any weather."
        )

    restaurants_compact = [
        {"name": r["name"], "cuisine": r.get("cuisine", "")}
        for r in bundle.restaurants[:12]
    ]

    return f"""Day {day_index + 1} of the trip.
Date: {day.date} ({weekday})
{weather_line}
Traveller interests: {', '.join(interests) if interests else 'general sightseeing'}

Schedule (FIXED — do not change stops, times, or durations):
{json.dumps(schedule, ensure_ascii=False, indent=2)}

Restaurants available for meal slots (pick from this list only):
{json.dumps(restaurants_compact, ensure_ascii=False, indent=2)}

Return JSON matching this schema exactly:
{_DAY_OUTPUT_SCHEMA}

Rules:
- Include EVERY stop from the schedule above — one entry per poi_id.
- For meal stops: include "name" (chosen restaurant) and "tip" only.
- For activity stops: include "tip", "best_time", "crowd_level", and "opening_hours_note".
- Use the weekday ({weekday}) and each stop's time to set crowd_level correctly."""


# ── Trip-level prompt ─────────────────────────────────────────────────────────

_TRIP_SYSTEM = """You are TourAI, writing the high-level overview for a trip.

You'll see the full schedule. Your job: title, summary, 2-3 highlights, hotel reasoning, and a realistic budget.

── TITLE RULES ──
Make it specific and evocative — reference the actual stops, cuisine, or mood of THIS trip.
Good: "Three Days of Neon, Buffets and Desert Drives in Las Vegas"
Good: "A Long Weekend Eating and Walking Through Lisbon's Seven Hills"
Bad:  "An Amazing Trip to Las Vegas"
Bad:  "Exploring the Best of Paris"

── SUMMARY RULES ──
One sentence. What is the emotional arc of this trip? What will the traveller remember?
Reference actual stops or themes from the schedule — not generic city descriptions.

── WHY_CANT_SKIP RULES ──
This is the ONE thing a friend back home will ask about. Make it visceral and specific.
Good: "The light through the stained glass at 11 AM turns the whole nave gold — nothing else in the city comes close."
Good: "You can see four states from the rim and the silence is genuinely startling after the casino noise."
Bad:  "An iconic landmark with historical significance that every visitor should experience."
Bad:  "A must-see attraction that represents the best of what the city has to offer."

── BUDGET RULES ──
Base estimates on the destination's real cost of living.
Las Vegas, NYC, Paris → expensive tier. Lisbon, Mexico City, Bangkok → budget-friendly.
accommodation_usd = realistic nightly rate × number of nights
food_usd = meals per day × cost per meal × days (vary by destination)
notes = one honest caveat specific to this destination ("Resort fees in Vegas add $40-60/night on top of the room rate")

Return ONLY valid JSON, no markdown.
"""

_TRIP_OUTPUT_SCHEMA = """{
  "title": "Three Days of Neon, Buffets and Desert Drives in Las Vegas",
  "summary": "A long weekend that swings between casino floors, roadside geology and some of the best Thai food in America.",
  "highlights": [
    {
      "name": "The Neon Museum",
      "why_cant_skip": "The boneyard at dusk turns into something genuinely surreal — 200 dead signs lit up against a desert sky. Nothing else in Vegas feels this quiet or this strange.",
      "emoji": "🌟"
    }
  ],
  "accommodation_reason": "The Golden Gate puts you at the walkable end of Fremont Street — you can reach the Neon Museum on foot and avoid paying for Uber every night.",
  "budget": {
    "accommodation_usd": 420,
    "food_usd": 280,
    "activities_usd": 120,
    "transport_usd": 80,
    "total_usd": 900,
    "notes": "Resort fees add $35-55/night on top of the listed room rate — factor this in when booking. The Neon Museum sells out; book online in advance."
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

async def _call_groq(system: str, user: str, max_tokens: int, label: str, model: str = _GROQ_MODEL, temperature: float = 0.5, max_retries: int = 3) -> dict | None:
    last_error = None

    for attempt in range(max_retries):
        try:
            if _USE_GEMINI:
                return await _call_gemini(system, user, max_tokens, label, temperature)
            else:
                client = groq.AsyncGroq(api_key=settings.groq_api_key)
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                content = resp.choices[0].message.content
                if resp.choices[0].finish_reason == "length":
                    logger.warning(f"Narration for {label!r} was cut off at {max_tokens} tokens — response may be incomplete")
                return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(f"Narration for {label!r} returned invalid JSON — {exc}")
            return None
        except Exception as exc:
            last_error = exc
            error_str = str(exc)
            if "429" in error_str or "rate_limit" in error_str.lower():
                wait_time = (2 ** attempt) * 5
                logger.warning(f"Rate limit for {label!r}, retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
                continue
            logger.warning(f"Narration API call failed for {label!r} — {exc}")
            return None

    logger.error(f"Narration for {label!r} failed after {max_retries} retries — {last_error}")
    return None


async def _call_gemini(system: str, user: str, max_tokens: int, label: str, temperature: float = 0.5) -> dict | None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEMINI_MODEL}:generateContent?key={settings.gemini_api_key}"
    prompt = f"{system}\n\n{user}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                error = await resp.text()
                logger.warning(f"Gemini API error for {label}: {resp.status} — {error}")
                return None
            data = await resp.json()

            if "candidates" not in data or not data["candidates"]:
                logger.warning(f"Gemini no response for {label}")
                return None

            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(content)

    logger.error(f"Narration for {label!r} failed after {max_retries} retries — {last_error}")
    return None


# ── Public entry points ───────────────────────────────────────────────────────

async def narrate_day(
    day_index: int,
    day: SkeletonDay,
    bundle: PrefetchBundle,
    interests: list[str],
) -> dict | None:
    prompt = _build_day_prompt(day_index, day, bundle, interests)
    return await _call_groq(_DAY_SYSTEM, prompt, max_tokens=2500, label=f"day_{day_index}")


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
    return await _call_groq(system, user, max_tokens=2500, label=f"replan_day_{day_index}", model=_REPLAN_MODEL, temperature=0.7)


async def narrate_trip(
    destination: str,
    interests: list[str],
    style: str,
    skeleton: Skeleton,
    bundle: PrefetchBundle,
) -> dict | None:
    prompt = _build_trip_prompt(destination, interests, style, skeleton, bundle)
    return await _call_groq(_TRIP_SYSTEM, prompt, max_tokens=2000, label="trip")


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
