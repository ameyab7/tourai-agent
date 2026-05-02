"""tourai/solver/scorer.py

The ONE place where an LLM helps with selection: scoring POIs against user
interests. This is taste judgment — what an LLM is actually good at.

Output: a {poi_id: 0..1} dict the skeleton solver consumes.

Why a separate stage:
  - Tiny prompt, tiny output — fast and cheap (Cerebras Qwen 32B in <2s)
  - Independently retryable / cacheable
  - If it fails, we fall back to the keyword heuristic and the system still works
"""

from __future__ import annotations

import json
import logging
import re

from groq import AsyncGroq

from api.config import settings

logger = logging.getLogger("tourai.scorer")

_SCORER_MODEL = "openai/gpt-oss-120b"

_SCORER_SYSTEM = """You are scoring travel attractions for a specific traveller.

Given a list of attractions and the traveller's interests, return a JSON object
mapping each poi_id to a score from 0.0 (irrelevant) to 1.0 (perfect match).

── SCORING RULES ──
- Iconic unmissable landmarks (Eiffel Tower, Grand Canyon, Colosseum, etc.) score >= 0.75 regardless of interests
- Strong interest match → 0.8–1.0
- Partial or loose interest match → 0.5–0.7
- Unrelated but decent attraction → 0.3–0.5
- Generic chain, tourist trap, or irrelevant → 0.0–0.2
- Use the full 0.0–1.0 range — do NOT cluster scores around 0.5

── INTEREST → POI TYPE MAPPINGS ──
Use these to inform scoring when the interest matches the type:
  photography   → viewpoint, rooftop, canyon, bridge, waterfront, park, monument
  history       → museum, monument, castle, ruins, historic_site, cathedral
  art           → gallery, museum, street_art, sculpture, theatre
  nature        → park, trail, beach, garden, waterfall, nature_reserve
  food          → market, food_hall, restaurant (local, not chains), cafe
  architecture  → cathedral, castle, bridge, monument, historic_building
  hiking        → trail, park, viewpoint, nature_reserve, beach
  nightlife     → bar, rooftop, entertainment, live_music
  wellness      → spa, park, garden, yoga_studio

── FEW-SHOT EXAMPLES ──
Traveller interests: photography, nature
  "Golden Gate Bridge" (viewpoint)     → 0.95  # iconic + perfect interest match
  "Muir Woods" (trail)                 → 0.88  # strong nature + photography
  "SFMOMA" (museum)                    → 0.35  # art museum, weak match
  "Westfield Mall" (shopping)          → 0.05  # irrelevant

Traveller interests: history, architecture
  "Colosseum" (monument)               → 0.98  # iconic + perfect match
  "Vatican Museums" (museum)           → 0.90  # strong history match
  "Trastevere neighbourhood" (park)    → 0.45  # interesting but loose match
  "McDonald's" (restaurant)            → 0.02  # irrelevant chain

Return ONLY a JSON object: {"a0": 0.85, "a1": 0.4, ...}
"""


async def score_pois(
    attractions: list[dict],
    interests: list[str],
    timeout_s: float = 8.0,
) -> dict[str, float]:
    """Return {poi_id: score}. On any failure, returns empty dict (caller falls back)."""
    if not attractions:
        return {}

    poi_list = [
        {
            "id":   p["poi_id"],
            "name": p["name"],
            "type": p["poi_type"],
            "tags": {k: v for k, v in p.get("tags", {}).items()
                     if k in {"cuisine", "historic", "outdoor_seating", "natural",
                               "tourism", "sport", "leisure", "artwork_type",
                               "architectural_style", "access"}},
        }
        for p in attractions
    ]
    user_msg = (
        f"Interests: {', '.join(interests) if interests else 'general sightseeing'}\n\n"
        f"Attractions:\n{json.dumps(poi_list, ensure_ascii=False)}"
    )

    try:
        client = AsyncGroq(api_key=settings.groq_api_key)
        resp = await client.chat.completions.create(
            model=_SCORER_MODEL,
            messages=[
                {"role": "system", "content": _SCORER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
            timeout=timeout_s,
        )
        content = resp.choices[0].message.content
    except Exception as exc:
        logger.warning(f"POI scorer call failed — falling back to keyword heuristic: {exc}")
        return {}

    # Strip code fences if present, then parse
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning(f"POI scorer returned invalid JSON — falling back to keyword heuristic: {exc} | content: {content[:200]}")
        return {}

    # Coerce defensively — model might return strings, ints, or floats
    scores: dict[str, float] = {}
    for k, v in raw.items():
        try:
            scores[str(k)] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            continue

    logger.info(f"Scored {len(scores)}/{len(attractions)} attractions against user interests")
    return scores
