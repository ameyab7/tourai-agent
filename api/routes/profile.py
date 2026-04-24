"""api/routes/profile.py — POST /v1/profile/setup, GET /v1/profile/me"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.logging_setup import correlation_id
from api.models import ProfileGetResponse, ProfileSetupRequest, ProfileSetupResponse
from api.supabase_client import get_supabase

router = APIRouter()
logger = logging.getLogger("tourai.api")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/v1/profile/setup", response_model=ProfileSetupResponse)
async def setup_profile(
    body: ProfileSetupRequest,
    user=Depends(get_current_user),
) -> ProfileSetupResponse:
    sb  = get_supabase()
    now = _now_iso()

    existing = sb.table("profiles").select("id").eq("user_id", str(user.id)).execute()
    exists   = len(existing.data) > 0

    payload = {
        "user_id":             str(user.id),
        "device_id":           body.device_id,
        "interests":           body.interests,
        "travel_style":        body.travel_style,
        "pace":                body.pace,
        "drive_tolerance_hrs": body.drive_tolerance_hrs,
        "updated_at":          now,
    }

    sb.table("profiles").upsert(payload, on_conflict="user_id").execute()

    logger.info("profile_setup", extra={
        "user_id":      str(user.id),
        "device_id":    body.device_id,
        "interests":    body.interests,
        "travel_style": body.travel_style,
        "pace":         body.pace,
        "drive_hrs":    body.drive_tolerance_hrs,
        "action":       "updated" if exists else "created",
    })

    return ProfileSetupResponse(
        status    = "updated" if exists else "created",
        user_id   = str(user.id),
        device_id = body.device_id,
        timestamp = now,
    )


@router.get("/v1/profile/me", response_model=ProfileGetResponse)
async def get_my_profile(user=Depends(get_current_user)) -> ProfileGetResponse:
    sb     = get_supabase()
    result = sb.table("profiles").select("*").eq("user_id", str(user.id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    row = result.data[0]
    return ProfileGetResponse(
        user_id             = row["user_id"],
        device_id           = row.get("device_id"),
        interests           = row["interests"],
        travel_style        = row["travel_style"],
        pace                = row["pace"],
        drive_tolerance_hrs = row["drive_tolerance_hrs"],
        created_at          = str(row["created_at"]),
        updated_at          = str(row["updated_at"]),
    )
