"""api/routes/profile.py — POST /v1/profile/setup, GET /v1/profile/{device_id}"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.logging_setup import correlation_id
from api.models import ProfileGetResponse, ProfileSetupRequest, ProfileSetupResponse

router = APIRouter()
logger = logging.getLogger("tourai.api")

_profiles: dict[str, dict[str, Any]] = {}
_PROFILES_FILE = Path(os.environ.get("PROFILES_PATH", "profiles.json"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_from_disk() -> None:
    if _PROFILES_FILE.exists():
        try:
            data = json.loads(_PROFILES_FILE.read_text())
            _profiles.update(data)
        except Exception:
            pass


def _save_to_disk() -> None:
    try:
        _PROFILES_FILE.write_text(json.dumps(_profiles, indent=2))
    except Exception:
        pass


_load_from_disk()


@router.post("/v1/profile/setup", response_model=ProfileSetupResponse)
async def setup_profile(body: ProfileSetupRequest) -> ProfileSetupResponse:
    cid    = correlation_id.get("-")
    now    = _now_iso()
    exists = body.device_id in _profiles

    _profiles[body.device_id] = {
        "device_id":           body.device_id,
        "interests":           body.interests,
        "travel_style":        body.travel_style,
        "pace":                body.pace,
        "drive_tolerance_hrs": body.drive_tolerance_hrs,
        "created_at":          _profiles.get(body.device_id, {}).get("created_at", now),
        "updated_at":          now,
    }
    _save_to_disk()

    logger.info("profile_setup", extra={
        "device_id":    body.device_id,
        "interests":    body.interests,
        "travel_style": body.travel_style,
        "pace":         body.pace,
        "drive_hrs":    body.drive_tolerance_hrs,
        "action":       "updated" if exists else "created",
    })

    return ProfileSetupResponse(
        status    = "updated" if exists else "created",
        device_id = body.device_id,
        timestamp = now,
    )


@router.get("/v1/profile/{device_id}", response_model=ProfileGetResponse)
async def get_profile(device_id: str) -> ProfileGetResponse:
    profile = _profiles.get(device_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileGetResponse(**profile)
