"""Acceptance tests for POST /v2/itinerary/{plan_id}/replan.

Uses FastAPI's TestClient with mocked plan_store and narrate_day so
no real network calls or LLM inference are needed.
"""

import json
import os
import sys

# Must set GROQ_API_KEY before importing api modules (Settings validates on load).
os.environ.setdefault("GROQ_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import pipeline as pipeline_module
from api.models import ItineraryRequest
from prefetch.distance import Leg
from prefetch.orchestrator import PrefetchBundle
from storage.plan_store import PlanSnapshot, _serialize_bundle


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_skeleton_dict() -> dict:
    stop = {
        "poi_id": "a1", "name": "Stop B", "poi_type": "museum",
        "lat": 32.78, "lon": -96.81, "arrival_time": "10:00",
        "duration_min": 90, "is_meal": False,
        "transit_from_prev_min": 15, "transit_mode": "walk",
        "skip_if_rushed": False,
    }
    return {
        "days": [
            {"date": "2025-06-01", "weekday": 6, "weather_is_clear": True,
             "stops": [{**stop, "poi_id": "a0", "name": "Stop A", "arrival_time": "09:00",
                        "transit_from_prev_min": 0, "transit_mode": "arrive"}]},
            {"date": "2025-06-02", "weekday": 0, "weather_is_clear": True, "stops": [stop]},
        ],
        "hotel": None,
        "diagnostics": {},
    }


def _make_bundle_dict() -> dict:
    bundle = PrefetchBundle(
        lat=32.77, lon=-96.80, display_name="Dallas",
        attractions=[{"poi_id": "a1", "name": "Stop B", "poi_type": "museum",
                      "lat": 32.78, "lon": -96.81, "tags": {}}],
        restaurants=[{"name": "Local Diner", "cuisine": "american", "lat": 32.77, "lon": -96.80}],
        hotels=[],
        weather=[
            {"date": "2025-06-01", "is_clear": True, "description": "Sunny",
             "temp_high_c": 30, "temp_low_c": 20},
            {"date": "2025-06-02", "is_clear": True, "description": "Sunny",
             "temp_high_c": 31, "temp_low_c": 21},
        ],
        distance_matrix=[[Leg(km=0.0, walking_min=0, driving_min=0)]],
        cache_hits={},
    )
    return _serialize_bundle(bundle)


def _make_snapshot(plan_id: str = "test-id") -> PlanSnapshot:
    return PlanSnapshot(
        plan_id=plan_id,
        user_id=None,
        created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        request=ItineraryRequest(
            destination="Dallas",
            start_date="2025-06-01",
            end_date="2025-06-02",
            interests=["history"],
            travel_style="solo",
            pace="balanced",
            drive_tolerance_hrs=2.0,
        ),
        skeleton_dict=_make_skeleton_dict(),
        bundle_dict=_make_bundle_dict(),
        final_plan={
            "title": "Dallas Weekend",
            "summary": "A great trip.",
            "destination": "Dallas",
            "start_date": "2025-06-01",
            "end_date": "2025-06-02",
            "highlights": [],
            "accommodation": {},
            "budget": {},
            "days": [
                {"date": "2025-06-01", "day_label": "Day 1", "rain_plan": "",
                 "weather": {}, "stops": []},
                {"date": "2025-06-02", "day_label": "Day 2", "rain_plan": "",
                 "weather": {}, "stops": []},
            ],
        },
    )


_MOCK_NARRATION = {
    "day_label": "Day 2 — Re-narrated",
    "rain_plan": "Head indoors",
    "stops": [
        {"poi_id": "a1", "tip": "Mocked tip", "best_time": "Anytime", "crowd_level": "low"},
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(pipeline_module.router)
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_replan_stage_order_and_title():
    client = _make_client()

    with (
        patch("api.replan_pipeline.plan_store") as mock_store,
        patch("api.replan_pipeline.narrate_replanned_day", new_callable=AsyncMock) as mock_narrate,
    ):
        mock_store.load = AsyncMock(return_value=_make_snapshot())
        mock_store.save = AsyncMock()
        mock_narrate.return_value = _MOCK_NARRATION

        response = client.post(
            "/v2/itinerary/test-id/replan",
            json={"reason": "tired", "day_index": 1},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)

    stage_events = [e for e in events if e["type"] == "stage"]
    stages = [e["stage"] for e in stage_events]
    assert stages == ["loading", "mutating", "narrating", "saving"], stages

    complete_events = [e for e in events if e["type"] == "complete"]
    assert len(complete_events) == 1, "expected exactly one complete event"

    # Stages must all appear before complete
    last_stage_pos = max(events.index(e) for e in stage_events)
    complete_pos = events.index(complete_events[0])
    assert last_stage_pos < complete_pos

    # Stub mutator preserves the original plan title
    assert complete_events[0]["plan"]["title"] == "Dallas Weekend"


def test_replan_missing_plan_returns_error_event():
    client = _make_client()

    with patch("api.replan_pipeline.plan_store") as mock_store:
        mock_store.load = AsyncMock(return_value=None)

        response = client.post(
            "/v2/itinerary/missing-id/replan",
            json={"reason": "tired", "day_index": 0},
        )

    assert response.status_code == 200  # SSE errors, not HTTP 500
    events = _parse_sse(response.text)
    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "not found" in error_events[0]["message"].lower()


def test_replan_out_of_range_day_returns_error_event():
    client = _make_client()

    with (
        patch("api.replan_pipeline.plan_store") as mock_store,
        patch("api.replan_pipeline.narrate_replanned_day", new_callable=AsyncMock),
    ):
        mock_store.load = AsyncMock(return_value=_make_snapshot())
        mock_store.save = AsyncMock()

        response = client.post(
            "/v2/itinerary/test-id/replan",
            json={"reason": "tired", "day_index": 99},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "out of range" in error_events[0]["message"].lower()
