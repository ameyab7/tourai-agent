"""api/routes/story.py — /v1/story endpoint + story generation helpers."""

import asyncio
import logging
import time
import traceback

from fastapi import APIRouter, HTTPException

from api import metrics
from api.cache import cache, story_cache_key
from api.config import settings
from api.logging_setup import correlation_id
from api.models import StoryRequest, StoryResponse

router  = APIRouter()
logger  = logging.getLogger("tourai.api")

_STORY_TAGS = [
    "description", "wikipedia", "historic", "heritage", "start_date",
    "opening_date", "architect", "artist_name", "operator", "named_after",
    "inscription", "old_name", "height", "building:levels", "denomination",
    "religion", "memorial", "memorial:subject", "artwork_type",
]

_STORY_SYSTEM = (
    "You are TourAI, a world-class local tour guide with deep knowledge of history, "
    "architecture, art, and culture. Write exactly 2-3 captivating sentences about the "
    "given place that a visitor walking past would love to hear. Lead with the most "
    "fascinating specific fact. Use vivid, evocative language. "
    "Write only the story text — no labels, no quotes, no intro, no title."
)

# In-flight futures: prevents N concurrent requests for the same story firing N Groq calls
_story_inflight: dict[str, asyncio.Future] = {}


def _build_story_context(name: str, poi_type: str, tags: dict) -> str:
    lines   = [f"Place: {name}", f"Category: {poi_type}"]
    details = []
    for key in _STORY_TAGS:
        val = tags.get(key)
        if val is not None and val != "":
            details.append(f"  {key}: {str(val)}")
    num    = str(tags.get("addr:housenumber", ""))
    street = str(tags.get("addr:street", ""))
    city   = str(tags.get("addr:city", ""))
    if street:
        addr = f"{(num + ' ' + street).strip()}{', ' + city if city else ''}"
        details.append(f"  address: {addr}")
    if details:
        lines.append("Details:\n" + "\n".join(details))
    return "\n".join(lines)


async def _generate_story(name: str, poi_type: str, tags: dict) -> str:
    from groq import AsyncGroq
    async with metrics.timed("groq_story"):
        client     = AsyncGroq(api_key=settings.groq_api_key)
        completion = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _STORY_SYSTEM},
                {"role": "user",   "content": _build_story_context(name, poi_type, tags)},
            ],
            max_tokens=180,
            temperature=0.8,
        )
    return completion.choices[0].message.content.strip()


@router.post("/v1/story", response_model=StoryResponse)
async def get_story(body: StoryRequest) -> StoryResponse:
    cid       = correlation_id.get("-")
    t0        = time.perf_counter()
    s_key     = story_cache_key(body.poi_name, body.latitude, body.longitude)

    # 1. Persistent cache hit
    if cached := await cache.get(s_key):
        logger.info("story_cache_hit", extra={"poi": body.poi_name})
        return StoryResponse(poi_id=body.poi_id, story=cached, cached=True, correlation_id=cid)

    # 2. Another request is already generating this story — await the same future
    if s_key in _story_inflight:
        try:
            story = await _story_inflight[s_key]
            return StoryResponse(poi_id=body.poi_id, story=story, cached=True, correlation_id=cid)
        except Exception:
            raise HTTPException(status_code=502, detail="Could not generate story right now.")

    # 3. We're first — create a future so concurrent requests can piggyback
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _story_inflight[s_key] = fut

    try:
        story = await _generate_story(body.poi_name, body.poi_type, body.tags)
        await cache.set(s_key, story, ttl=3600)
        fut.set_result(story)
    except Exception:
        fut.set_exception(Exception("story generation failed"))
        logger.error("story_error", extra={"exc": traceback.format_exc(), "poi": body.poi_name})
        metrics.errors_total.labels(endpoint="/v1/story", error_type="groq").inc()
        raise HTTPException(status_code=502, detail="Could not generate story right now.")
    finally:
        _story_inflight.pop(s_key, None)

    logger.info("story_generated", extra={
        "poi":        body.poi_name,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    })
    return StoryResponse(poi_id=body.poi_id, story=story, cached=False, correlation_id=cid)
