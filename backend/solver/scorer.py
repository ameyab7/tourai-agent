"""tourai/solver/scorer.py

The ONE place where an LLM helps with selection: scoring POIs against user
interests. This is taste judgment — what an LLM is actually good at.

Output: a {poi_id: 0..1} dict the skeleton solver consumes.

Why a separate stage:
  - Tiny prompt, tiny output — fast and cheap
  - Independently retryable / cacheable
  - If it fails, we fall back to the keyword heuristic and the system still works
"""

from __future__ import annotations

import json
import logging

from groq import AsyncGroq

from api.config import settings

logger = logging.getLogger("tourai.scorer")

_SCORER_MODEL = "llama-3.3-70b-versatile"

_SCORER_SYSTEM = """You score travel POIs for a user. Return JSON: {"a0": 0.9, "a1": 0.3, ...}
- 1.0 = perfect interest match
- 0.5 = partial match
- 0.0 = irrelevant
Iconic landmarks (Eiffel Tower, Grand Canyon) always score 0.75+.
Only return the JSON, nothing else."""


async def score_pois(
    attractions: list[dict],
    interests: list[str],
    timeout_s: float = 8.0,
) -> dict[str, float]:
    """Return {poi_id: score}. On any failure, returns empty dict (caller falls back)."""
    if not attractions:
        return {}

    poi_list = [
        {"id": p["poi_id"], "name": p["name"], "type": p["poi_type"]}
        for p in attractions[:20]
    ]
    user_msg = (
        f"User interests: {', '.join(interests) if interests else 'general sightseeing'}\n"
        f"POIs: {json.dumps(poi_list, ensure_ascii=False)}"
    )

    try:
        client = AsyncGroq(api_key=settings.groq_api_key)
        resp = await client.chat.completions.create(
            model=_SCORER_MODEL,
            messages=[
                {"role": "system", "content": _SCORER_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=300,
            timeout=timeout_s,
        )
        content = resp.choices[0].message.content
    except Exception as exc:
        logger.warning(f"POI scorer call failed — falling back: {exc}")
        return {}

    try:
        raw = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        logger.warning(f"POI scorer invalid JSON — falling back: {exc}")
        return {}

    scores: dict[str, float] = {}
    for k, v in raw.items():
        try:
            scores[str(k)] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            continue

    logger.info(f"Scored {len(scores)}/{len(attractions)} POIs")
    return scores