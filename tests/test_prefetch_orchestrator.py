"""tests/test_prefetch_orchestrator.py

Acceptance tests for the bug fixes in prefetch/orchestrator.py.

Run:
    venv/bin/python -m pytest tests/test_prefetch_orchestrator.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import unittest.mock as mock

import pytest

from prefetch.orchestrator import _attractions, _poi_radius, _DEFAULT_RADIUS, _EXTRA_LARGE_RADIUS
from utils.poi_ranker import rank_pois


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_poi(poi_type: str = "park", name: str = "Test Park") -> dict:
    return {
        "id":       "x1",
        "name":     name,
        "poi_type": poi_type,
        "lat":      0.0,
        "lon":      0.0,
        "tags":     {},
    }


def _enough_pois(n: int = 20, poi_type: str = "museum") -> list[dict]:
    return [
        {"id": f"p{i}", "name": f"Place {i}", "poi_type": poi_type, "lat": 0.0, "lon": 0.0, "tags": {}}
        for i in range(n)
    ]


# ── Bug 1: cache key must embed the actual fetch radius ───────────────────────

@pytest.mark.asyncio
async def test_attractions_cache_key_matches_radius():
    """Cache key must contain the real fetch radius (20000), not the old hardcoded 6000."""
    captured_key: list[str] = []

    async def fake_get(key: str):
        captured_key.append(key)
        return None  # cache miss → triggers fetch

    async def fake_set(key: str, value, ttl: int):
        captured_key.append(key)

    with mock.patch("prefetch.orchestrator.cache.get", side_effect=fake_get), \
         mock.patch("prefetch.orchestrator.cache.set", side_effect=fake_set), \
         mock.patch("utils.geoapify_places.fetch_pois",
                    return_value=_enough_pois(20)):
        await _attractions(48.85, 2.35, ["history"], "fake_key", display_name="Paris")

    assert captured_key, "cache.get was never called"
    assert any("20000" in k for k in captured_key), (
        f"Expected '20000' in cache key, got: {captured_key}"
    )
    assert not any("6000" in k for k in captured_key), (
        f"Old hardcoded 6000 still appears in cache key: {captured_key}"
    )


# ── Bug 2 / Bug 3: radius selection ──────────────────────────────────────────

def test_extra_large_city_uses_30km():
    assert _poi_radius("Las Vegas") == _EXTRA_LARGE_RADIUS == 30_000


def test_extra_large_city_case_insensitive():
    assert _poi_radius("LAS VEGAS, NV") == _EXTRA_LARGE_RADIUS


def test_default_city_uses_20km():
    assert _poi_radius("Lisbon") == _DEFAULT_RADIUS == 20_000


def test_international_city_uses_default():
    assert _poi_radius("Tokyo") == _DEFAULT_RADIUS
    assert _poi_radius("Paris") == _DEFAULT_RADIUS
    assert _poi_radius("Barcelona") == _DEFAULT_RADIUS


@pytest.mark.asyncio
async def test_extra_large_city_fetch_uses_30km():
    """_attractions for Las Vegas must call fetch_pois with radius_m=30000."""
    fetch_calls: list[int] = []

    async def fake_fetch(lat, lon, radius_m, api_key, limit=100):
        fetch_calls.append(radius_m)
        return _enough_pois(20)

    with mock.patch("prefetch.orchestrator.cache.get", return_value=None), \
         mock.patch("prefetch.orchestrator.cache.set", new_callable=mock.AsyncMock), \
         mock.patch("utils.geoapify_places.fetch_pois", side_effect=fake_fetch):
        await _attractions(36.17, -115.14, ["nightlife"], "key", display_name="Las Vegas")

    assert fetch_calls, "fetch_pois was never called"
    assert fetch_calls[0] == 30_000, f"Expected 30000, got {fetch_calls[0]}"


@pytest.mark.asyncio
async def test_default_city_fetch_uses_20km():
    """_attractions for a non-sprawling city must call fetch_pois with radius_m=20000."""
    fetch_calls: list[int] = []

    async def fake_fetch(lat, lon, radius_m, api_key, limit=100):
        fetch_calls.append(radius_m)
        return _enough_pois(20)

    with mock.patch("prefetch.orchestrator.cache.get", return_value=None), \
         mock.patch("prefetch.orchestrator.cache.set", new_callable=mock.AsyncMock), \
         mock.patch("utils.geoapify_places.fetch_pois", side_effect=fake_fetch):
        await _attractions(38.71, -9.14, ["history"], "key", display_name="Lisbon")

    assert fetch_calls[0] == 20_000, f"Expected 20000, got {fetch_calls[0]}"


# ── Bug 4: warning when still thin after refetch ─────────────────────────────

@pytest.mark.asyncio
async def test_thin_refetch_logs_warning(caplog):
    """5 POIs on first call, 8 after refetch → both below _MIN_ATTRACTIONS → WARNING logged."""
    call_count = 0

    async def fake_fetch(lat, lon, radius_m, api_key, limit=100):
        nonlocal call_count
        call_count += 1
        n = 5 if call_count == 1 else 8
        return _enough_pois(n, poi_type="museum")

    with mock.patch("prefetch.orchestrator.cache.get", return_value=None), \
         mock.patch("prefetch.orchestrator.cache.set", new_callable=mock.AsyncMock), \
         mock.patch("utils.geoapify_places.fetch_pois", side_effect=fake_fetch), \
         caplog.at_level(logging.WARNING, logger="tourai.prefetch"):
        await _attractions(38.71, -9.14, ["history"], "key", display_name="Lisbon")

    assert call_count == 2, f"Expected 2 fetch calls (initial + refetch), got {call_count}"
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("prefetch_thin_after_refetch" in m for m in warning_msgs), (
        f"Expected 'prefetch_thin_after_refetch' WARNING, got: {warning_msgs}"
    )


@pytest.mark.asyncio
async def test_thin_refetch_no_warning_when_enough(caplog):
    """Refetch that returns >= _MIN_ATTRACTIONS must NOT log the thin warning."""
    call_count = 0

    async def fake_fetch(lat, lon, radius_m, api_key, limit=100):
        nonlocal call_count
        call_count += 1
        n = 5 if call_count == 1 else 20
        return _enough_pois(n, poi_type="museum")

    with mock.patch("prefetch.orchestrator.cache.get", return_value=None), \
         mock.patch("prefetch.orchestrator.cache.set", new_callable=mock.AsyncMock), \
         mock.patch("utils.geoapify_places.fetch_pois", side_effect=fake_fetch), \
         caplog.at_level(logging.WARNING, logger="tourai.prefetch"):
        await _attractions(38.71, -9.14, ["history"], "key", display_name="Lisbon")

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("prefetch_thin_after_refetch" in m for m in warning_msgs), (
        f"Unexpected thin warning: {warning_msgs}"
    )


# ── Bug 5: max_per_type cap ───────────────────────────────────────────────────

def test_max_per_type_three():
    """rank_pois with max_per_type=3: 10 parks in → at most 3 parks out."""
    parks = [
        {"id": f"p{i}", "name": f"Park {i}", "poi_type": "park",
         "lat": float(i) * 0.01, "lon": float(i) * 0.01, "tags": {}}
        for i in range(10)
    ]
    result = rank_pois(parks, ["nature"], 0.0, 0.0, limit=25, max_per_type=3)
    park_count = sum(1 for p in result if p["poi_type"] == "park")
    assert park_count <= 3, f"Expected ≤3 parks, got {park_count}"
    assert len(result) <= 3
