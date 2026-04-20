"""api/routes/feedback.py — POST /v1/feedback, GET /v1/feedback

POST: Accept a false-positive or false-negative report from the mobile app,
      run a full visibility diagnosis, store in memory + optional NDJSON file.

GET:  Return all stored feedback entries so the auto-fix agent can pull them
      from the API instead of reading a local file (which is ephemeral in Railway).
"""

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from api.logging_setup import correlation_id
from api.models import FeedbackDiagnosis, FeedbackRequest, FeedbackResponse
from utils.visibility import diagnose_poi

router = APIRouter()
logger = logging.getLogger("tourai.api")

# In-memory store — survives the request lifecycle, lost on redeploy.
# Good enough: the agent polls this endpoint and processes entries promptly.
_feedback_store: list[dict[str, Any]] = []

# Optional NDJSON file — useful locally or with a Railway volume mounted at /data
_FEEDBACK_LOG = Path(os.environ.get("FEEDBACK_LOG_PATH", "feedback_log.ndjson"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/v1/feedback", response_model=FeedbackResponse)
async def post_feedback(body: FeedbackRequest) -> FeedbackResponse:
    """Report a visibility false positive/negative and get a full diagnosis."""
    cid = correlation_id.get("-")

    # Build a minimal POI dict compatible with diagnose_poi / filter_visible
    poi = {
        "id":       body.poi_id,
        "name":     body.poi_name,
        "lat":      body.poi_lat,
        "lon":      body.poi_lon,
        "tags":     body.poi_tags,
        "geometry": body.poi_geometry,
    }

    try:
        trace = diagnose_poi(
            poi          = poi,
            user_lat     = body.latitude,
            user_lon     = body.longitude,
            user_heading = body.heading,
            user_street  = body.user_street,
        )
    except Exception:
        logger.error("feedback_diagnosis_error", extra={"exc": traceback.format_exc()})
        raise HTTPException(status_code=500, detail="Diagnosis failed — check server logs")

    # Did the filter already agree with the user?
    already_fixed = (trace["filter_now_says"] == body.user_says)
    agreement     = "AGREE" if already_fixed else "DISAGREE"

    # Build log entry
    log_entry = {
        "ts":         _now_iso(),
        "cid":        cid,
        "user_lat":   body.latitude,
        "user_lon":   body.longitude,
        "heading":    body.heading,
        "user_street":body.user_street,
        "user_says":  body.user_says,
        "note":       body.note,
        **{f"diag_{k}": v for k, v in trace.items()},
        "already_fixed": already_fixed,
        "agreement":     agreement,
    }
    # Keep in memory (survives across requests, readable via GET /v1/feedback)
    _feedback_store.append(log_entry)

    # Also try to write to NDJSON file (works locally or with a Railway volume)
    try:
        with _FEEDBACK_LOG.open("a") as fh:
            fh.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass  # ephemeral filesystem — not a problem, in-memory store is the source of truth

    logger.info("feedback_received", extra={
        "poi_name":      body.poi_name,
        "user_says":     body.user_says,
        "filter_says":   trace["filter_now_says"],
        "agreement":     agreement,
        "rule":          trace["rule"],
        "size":          trace["size"],
        "distance_m":    trace["distance_m"],
        "angle_deg":     trace["angle_deg"],
        "already_fixed": already_fixed,
    })

    diagnosis = FeedbackDiagnosis(**trace, already_fixed=already_fixed, agreement=agreement)
    return FeedbackResponse(
        status         = "logged",
        diagnosis      = diagnosis,
        correlation_id = cid,
        timestamp      = _now_iso(),
    )


@router.get("/v1/feedback")
async def get_feedback(
    agreement: str | None = Query(None, description="Filter by AGREE or DISAGREE"),
    limit:     int        = Query(100,  ge=1, le=1000),
) -> dict[str, Any]:
    """Return stored feedback entries. Used by the auto-fix agent to pull bugs from Railway."""
    entries = _feedback_store
    if agreement:
        entries = [e for e in entries if e.get("agreement") == agreement.upper()]
    return {
        "count":   len(entries[-limit:]),
        "total":   len(_feedback_store),
        "entries": entries[-limit:],
    }
