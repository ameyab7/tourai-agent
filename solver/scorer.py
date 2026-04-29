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

import asyncio
import json
import logging
import re

import httpx

from api.config import settings

logger = logging.getLogger("tourai.scorer")

_CEREBRAS_BASE = "https://api.cerebras.ai/v1"
_SCORER_MODEL = "qwen-3-32b"  # small, fast, cheap; swap to whatever Cerebras has

_SCORER_SYSTEM = """You are scoring travel attractions for a specific traveller.

Given a list of attractions and the traveller's interests, return a JSON object
mapping each poi_id to a score from 0.0 (irrelevant) to 1.0 (perfect match).

Rules:
- Iconic must-see landmarks score >= 0.7 even if they don't match interests directly
- Strong interest match scores 0.8 - 1.0
- Weak match scores 0.3 - 0.5
- Tourist traps that match no interests score 0.0 - 0.2
- Return ONLY a JSON object: {"a0": 0.85, "a1": 0.4, ...}
"""


async def score_pois(
    attractions: list[dict],
    interests: list[str],
    timeout_s: float = 8.0,
) -> dict[str, float]:
    """Return {poi_id: score}. On any failure, returns empty dict (caller falls back)."""
    if not attractions:
        return {}

    # Compact representation — we only need name + type for the LLM
    poi_list = [
        {"id": p["poi_id"], "name": p["name"], "type": p["poi_type"]}
        for p in attractions
    ]
    user_msg = (
        f"Interests: {', '.join(interests) if interests else 'general sightseeing'}\n\n"
        f"Attractions:\n{json.dumps(poi_list, ensure_ascii=False)}"
    )

    payload = {
        "model": _SCORER_MODEL,
        "messages": [
            {"role": "system", "content": _SCORER_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.cerebras_api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                f"{_CEREBRAS_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, asyncio.TimeoutError, KeyError) as exc:
        logger.warning("scorer_failed", extra={"error": str(exc)})
        return {}

    # Strip code fences if present, then parse
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("scorer_parse_failed", extra={"error": str(exc), "content": content[:200]})
        return {}

    # Coerce defensively — model might return strings, ints, or floats
    scores: dict[str, float] = {}
    for k, v in raw.items():
        try:
            scores[str(k)] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            continue

    logger.info("scorer_complete", extra={"scored": len(scores), "total": len(attractions)})
    return scores
