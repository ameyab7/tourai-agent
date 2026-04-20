"""api/routes/feedback.py — POST /v1/feedback

Accepts a false-positive or false-negative report from the mobile app,
runs a full visibility diagnosis, and returns a structured trace explaining
exactly which rule caused the filter to agree or disagree with the user.
"""

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.logging_setup import correlation_id
from api.models import FeedbackDiagnosis, FeedbackRequest, FeedbackResponse
from utils.visibility import diagnose_poi

router = logger = None  # forward declarations

router = APIRouter()
logger = logging.getLogger("tourai.api")

# Append-only NDJSON log so feedback can be replayed / analysed offline
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

    # Persist to feedback log
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
    try:
        with _FEEDBACK_LOG.open("a") as fh:
            fh.write(json.dumps(log_entry) + "\n")
    except Exception:
        logger.warning("feedback_log_write_failed", extra={"exc": traceback.format_exc()})

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
