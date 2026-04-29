"""Tests for narrate_replanned_day and summarize_mutation."""

import json
import os
import sys

os.environ.setdefault("GROQ_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from narration.narrator import narrate_replanned_day
from prefetch.distance import Leg
from prefetch.orchestrator import PrefetchBundle
from replan.mutator import summarize_mutation
from solver.skeleton import SkeletonDay, SkeletonStop


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_day() -> SkeletonDay:
    return SkeletonDay(
        date="2025-06-01",
        weekday=6,
        weather_is_clear=False,
        stops=[
            SkeletonStop(
                poi_id="museum1", name="Art Museum", poi_type="museum",
                lat=0.0, lon=0.0, arrival_time="10:00", duration_min=90,
                is_meal=False, transit_from_prev_min=0, transit_mode="arrive",
                skip_if_rushed=False,
            ),
        ],
    )


def _make_bundle() -> PrefetchBundle:
    return PrefetchBundle(
        lat=0.0, lon=0.0, display_name="TestCity",
        attractions=[{"poi_id": "museum1", "name": "Art Museum", "poi_type": "museum",
                      "lat": 0.0, "lon": 0.0, "tags": {}}],
        restaurants=[{"name": "Local Bistro", "cuisine": "french", "lat": 0.0, "lon": 0.0}],
        hotels=[],
        weather=[{"date": "2025-06-01", "is_clear": False, "description": "Rainy",
                  "temp_high_c": 18, "temp_low_c": 12}],
        distance_matrix=[[Leg(km=0.0, walking_min=0, driving_min=0)]],
        cache_hits={},
    )


def _make_groq_mock(content: dict) -> tuple[MagicMock, AsyncMock]:
    """Returns (MockGroqClass, mock_client_instance)."""
    choice = MagicMock()
    choice.message.content = json.dumps(content)
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=resp)

    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls, mock_client


# ── summarize_mutation ────────────────────────────────────────────────────────

def test_summarize_bad_weather_with_swaps():
    log = {"reason": "bad_weather", "swaps": [{"out": "A", "in": "B"}, {"out": "C", "in": "D"}], "unswappable": []}
    s = summarize_mutation(log)
    assert "2" in s
    assert "outdoor" in s.lower() or "indoor" in s.lower()


def test_summarize_running_late_with_drop():
    log = {"reason": "running_late", "dropped": ["Stop X"], "shifted_by_min": 30}
    s = summarize_mutation(log)
    assert "30" in s
    assert "1 stop" in s.lower() or "dropped" in s.lower()


def test_summarize_tired():
    log = {"reason": "tired", "dropped": ["A", "B"], "added_rest": True}
    s = summarize_mutation(log)
    assert "lighter" in s.lower() or "rest" in s.lower()
    assert "2" in s


def test_summarize_place_closed():
    log = {"reason": "place_closed", "swaps": [{"out": "X", "in": "Y"}], "dropped": []}
    s = summarize_mutation(log)
    assert "1" in s
    assert "closed" in s.lower() or "venue" in s.lower()


# ── narrate_replanned_day ─────────────────────────────────────────────────────

_REPLAN_RESPONSE = {
    "day_label": "Day 1 — Indoor edition (since the rain rolled in)",
    "rain_plan": "Already indoors — keep the plan!",
    "stops": [
        {"poi_id": "museum1", "tip": "The main hall is quiet at 10 AM.", "best_time": "Morning", "crowd_level": "low"},
    ],
}

_BAD_WEATHER_LOG = {
    "reason": "bad_weather",
    "swaps": [{"out": "City Park", "in": "Art Museum"}],
    "unswappable": [],
}


async def test_narrate_replanned_day_sends_correct_prompts():
    """System prompt contains replan instruction; user prompt has [REPLAN] + summary."""
    mock_cls, mock_client = _make_groq_mock(_REPLAN_RESPONSE)

    with patch("narration.narrator.AsyncGroq", mock_cls):
        result = await narrate_replanned_day(
            day_index=0,
            day=_make_day(),
            bundle=_make_bundle(),
            interests=["art", "history"],
            mutation_log=_BAD_WEATHER_LOG,
        )

    assert result is not None
    assert result["day_label"] == _REPLAN_RESPONSE["day_label"]

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    messages = call_kwargs["messages"]
    system = messages[0]["content"]
    user   = messages[1]["content"]

    # System prompt contains the change-acknowledgment instruction
    assert "bad_weather" in system
    assert "One light reference is enough" in system

    # User prompt carries [REPLAN] header and a human summary
    assert user.startswith("[REPLAN]")
    assert "bad_weather" in user
    assert "outdoor" in user.lower() or "indoor" in user.lower()  # from summarize_mutation


async def test_narrate_replanned_day_returns_none_on_parse_failure():
    """Returns None (not raises) when the LLM returns invalid JSON."""
    choice = MagicMock()
    choice.message.content = "not json {"
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=resp)

    with patch("narration.narrator.AsyncGroq", MagicMock(return_value=mock_client)):
        result = await narrate_replanned_day(
            day_index=0,
            day=_make_day(),
            bundle=_make_bundle(),
            interests=[],
            mutation_log=_BAD_WEATHER_LOG,
        )

    assert result is None


async def test_narrate_replanned_day_preserves_temperature():
    """Temperature must remain 0.7 — same warm tone as fresh narration."""
    mock_cls, mock_client = _make_groq_mock(_REPLAN_RESPONSE)

    with patch("narration.narrator.AsyncGroq", mock_cls):
        await narrate_replanned_day(
            day_index=0,
            day=_make_day(),
            bundle=_make_bundle(),
            interests=[],
            mutation_log=_BAD_WEATHER_LOG,
        )

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.7


async def test_narrate_replanned_day_does_not_affect_narrate_day():
    """Calling narrate_replanned_day must not change the narrate_day system prompt."""
    from narration.narrator import _DAY_SYSTEM

    original_system = _DAY_SYSTEM
    mock_cls, _ = _make_groq_mock(_REPLAN_RESPONSE)

    with patch("narration.narrator.AsyncGroq", mock_cls):
        await narrate_replanned_day(
            day_index=0,
            day=_make_day(),
            bundle=_make_bundle(),
            interests=[],
            mutation_log=_BAD_WEATHER_LOG,
        )

    assert _DAY_SYSTEM == original_system, "_DAY_SYSTEM must not be mutated"
