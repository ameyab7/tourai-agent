"""api/routes/ask.py — /v1/ask endpoint."""

import logging
import time
import traceback

from fastapi import APIRouter, HTTPException

from api import metrics
from api.config import settings
from api.logging_setup import correlation_id
from api.models import AskRequest, AskResponse

import os
if os.environ.get("GEOAPIFY_API_KEY"):
    from utils import geoapify as poi_source
else:
    from utils import overpass as poi_source  # type: ignore[no-redef]

router = APIRouter()
logger = logging.getLogger("tourai.api")

_SYSTEM_PROMPT = (
    "You are TourAI, a knowledgeable and friendly local tour guide. "
    "Answer questions about the user's surroundings concisely and engagingly. "
    "Focus on historical facts, architectural details, and local stories. "
    "Only mention places that appear in the provided POI list. "
    "If no POI list is provided, say you don't have specific information about "
    "what's immediately around the user right now. "
    "Keep answers under 3 sentences unless more detail is specifically asked for."
)


@router.post("/v1/ask", response_model=AskResponse)
async def ask(body: AskRequest) -> AskResponse:
    cid = correlation_id.get("-")
    t0  = time.perf_counter()

    # If app sent no POI context, do a live lookup so Groq has real data
    nearby = body.context.get("nearby_pois", [])
    if not nearby:
        try:
            async with metrics.timed("geoapify_ask"):
                raw_pois = await poi_source.search_nearby(body.latitude, body.longitude, 300)
            nearby = [
                {"name": p["name"], "type": p.get("poi_type", "unknown"), "distance_m": 0}
                for p in raw_pois[:8]
            ]
            logger.info("ask_poi_fallback", extra={"fetched": len(nearby)})
        except Exception:
            logger.warning("ask_poi_fallback_failed")

    if nearby:
        poi_lines = "\n".join(
            f"  - {p.get('name', '?')} ({p.get('type', '?')})" for p in nearby[:8]
        )
        poi_context = f"\n\nVerified nearby points of interest from live map data:\n{poi_lines}"
    else:
        poi_context = ""

    user_message = (
        f"The user is at coordinates ({body.latitude:.5f}, {body.longitude:.5f}).{poi_context}\n\n"
        f"Question: {body.question}"
    )

    try:
        from groq import AsyncGroq
        async with metrics.timed("groq_ask"):
            client     = AsyncGroq(api_key=settings.groq_api_key)
            completion = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                max_tokens=200,
                temperature=0.7,
            )
        answer = completion.choices[0].message.content.strip()
    except Exception:
        logger.error("ask_error", extra={"exc": traceback.format_exc()})
        metrics.errors_total.labels(endpoint="/v1/ask", error_type="groq").inc()
        raise HTTPException(status_code=502, detail="Could not generate an answer right now.")

    logger.info("ask_answered", extra={
        "lat":         round(body.latitude, 5),
        "lon":         round(body.longitude, 5),
        "question":    body.question,
        "answer":      answer,
        "nearby_pois": [p.get("name") for p in body.context.get("nearby_pois", [])],
        "elapsed_ms":  round((time.perf_counter() - t0) * 1000),
    })
    return AskResponse(answer=answer, question=body.question, correlation_id=cid)
