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
import time

import httpx
from groq import AsyncGroq

from api.config import settings


def _ollama_api_url() -> str:
    base = settings.ollama_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/api/chat"

logger = logging.getLogger("tourai.scorer")

_SCORER_MODEL = "llama-3.3-70b-versatile"

_SCORER_SYSTEM = """You score travel POIs for a user based on their interests. Return ONLY a raw JSON object, no markdown, no code fences.
Format: {"poi_id": score, ...} using the exact IDs from the input.

Scoring rules:
- 1.0 = perfect match for an interest
- 0.7 = strong match
- 0.5 = partial or indirect match
- 0.3 = weak but real connection
- 0.1 = minimal relevance

MANDATORY RULES:
- NEVER score every POI 0.0 — always differentiate.
- The best POI in the list must score at least 0.4.
- Parks and nature areas match "social", "outdoors", "adventure", or "photography" interests at 0.4+.
- Any named attraction or landmark scores at least 0.5 regardless of interests.
- Iconic landmarks (national parks, famous monuments, Grand Canyon etc.) always score 0.75+."""


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that some models wrap JSON in."""
    text = text.strip()
    if text.startswith("```"):
        text = text[text.find("\n") + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


async def score_pois(
    attractions: list[dict],
    interests: list[str],
    timeout_s: float = 60.0 if settings.ollama_base_url else 8.0,
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
        t0 = time.perf_counter()
        if settings.ollama_base_url:
            payload = {
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": _SCORER_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                "stream": False,
                "think": False,
                "format": "json",
                "options": {"temperature": 0.3, "num_predict": 512},
            }
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(_ollama_api_url(), json=payload)
                resp.raise_for_status()
            raw = resp.json()
            logger.info(f"[scorer] ollama raw response: {raw}")
            content = _strip_fences(raw.get("message", {}).get("content", ""))
        else:
            groq_client = AsyncGroq(api_key=settings.groq_api_key)
            resp = await groq_client.chat.completions.create(
                model=_SCORER_MODEL,
                messages=[
                    {"role": "system", "content": _SCORER_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=300,
                timeout=timeout_s,
            )
            content = _strip_fences(resp.choices[0].message.content or "")
        logger.info(f"[scorer] gemma response: {time.perf_counter() - t0:.1f}s")
    except Exception as exc:
        logger.warning(f"POI scorer call failed — falling back: {exc}")
        return {}

    try:
        raw = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        logger.warning(f"POI scorer invalid JSON — falling back: {exc}")
        logger.warning(f"[scorer] Failed content was: {repr(content)}")
        return {}

    scores: dict[str, float] = {}
    for k, v in raw.items():
        try:
            scores[str(k)] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            continue

    logger.info(f"Scored {len(scores)}/{len(attractions)} POIs")
    return scores