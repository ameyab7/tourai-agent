from __future__ import annotations

from datetime import datetime
from typing import TypedDict


class TourGuideState(TypedDict):
    user_id: str
    latitude: float
    longitude: float
    speed_mps: float
    heading: float
    nearby_pois: list
    top_poi: dict | None
    should_speak: bool
    enriched_context: dict
    story_text: str
    audio_bytes: bytes
    told_pois: set
    last_story_time: datetime | None
    interest_profile: dict
    search_radius: float
    audio_filepath: str
