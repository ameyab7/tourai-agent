"""
tests/test_api_production.py — Live tests against the Railway deployment.

Run:
    pytest tests/test_api_production.py -v

These tests hit the real production API — no mocks, no local server.
Requires network access. Expected to be slower than unit tests.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import time
import pytest
from httpx import AsyncClient

PRODUCTION_URL = "https://tourai-agent-production.up.railway.app"

DALLAS_LAT = 32.7787
DALLAS_LON = -96.8083
DALLAS_HDG = 90.0

# ---------------------------------------------------------------------------
# Client fixture — real HTTP, no mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def base_url():
    return PRODUCTION_URL


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=15) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "degraded")
    assert "dependencies" in body
    assert "timestamp" in body

    dep_names = {d["name"] for d in body["dependencies"]}
    assert "osrm" in dep_names


@pytest.mark.asyncio
async def test_health_poi_source_is_geoapify():
    """Confirm Geoapify is the active POI source, not Overpass."""
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=15) as client:
        resp = await client.get("/health")

    deps = {d["name"]: d for d in resp.json()["dependencies"]}
    assert "poi_source" in deps
    assert deps["poi_source"]["detail"] == "geoapify"
    assert deps["poi_source"]["ok"] is True


# ---------------------------------------------------------------------------
# /v1/visible-pois
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_visible_pois_returns_200():
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=30) as client:
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_visible_pois_response_shape():
    """Response has all required fields with correct types."""
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=30) as client:
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    body = resp.json()
    assert isinstance(body["visible_pois"],  list)
    assert isinstance(body["total_checked"], int)
    assert isinstance(body["cache_hit"],     bool)
    assert isinstance(body["correlation_id"], str)
    assert isinstance(body["timestamp"],     str)
    assert body["total_checked"] >= 0


@pytest.mark.asyncio
async def test_visible_pois_returns_dallas_landmarks():
    """Downtown Dallas should return recognisable POIs."""
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=30) as client:
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    body = resp.json()
    assert body["total_checked"] > 0, "Expected POIs in downtown Dallas"
    assert len(body["visible_pois"]) > 0, "Expected at least one visible POI"

    names = [p["name"] for p in body["visible_pois"]]
    print(f"\nVisible POIs: {names}")

    # Each POI must have valid coordinates
    for poi in body["visible_pois"]:
        assert -90  <= poi["lat"] <= 90
        assert -180 <= poi["lon"] <= 180
        assert poi["distance_m"] >= 0
        assert poi["name"] != ""


@pytest.mark.asyncio
async def test_visible_pois_has_correlation_id():
    """Every response must include a correlation_id."""
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=30) as client:
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    assert "X-Request-ID" in resp.headers
    assert resp.json()["correlation_id"] == resp.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_visible_pois_cache_hit_on_second_request():
    """Second identical request should return cache_hit: true."""
    payload = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading": DALLAS_HDG}

    async with AsyncClient(base_url=PRODUCTION_URL, timeout=30) as client:
        resp1 = await client.post("/v1/visible-pois", json=payload)
        resp2 = await client.post("/v1/visible-pois", json=payload)

    assert resp2.json()["cache_hit"] is True


@pytest.mark.asyncio
async def test_visible_pois_invalid_coords_returns_422():
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=15) as client:
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  999.0,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_visible_pois_response_time_cached():
    """Cached response must complete in under 500ms."""
    payload = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading": DALLAS_HDG}

    async with AsyncClient(base_url=PRODUCTION_URL, timeout=30) as client:
        await client.post("/v1/visible-pois", json=payload)   # warm cache

        start = time.perf_counter()
        resp  = await client.post("/v1/visible-pois", json=payload)
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    assert resp.json()["cache_hit"] is True
    assert elapsed_ms < 500, f"Cached response too slow: {elapsed_ms:.0f}ms"


# ---------------------------------------------------------------------------
# /v1/current-street
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_current_street_dallas():
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=15) as client:
        resp = await client.get("/v1/current-street", params={
            "lat": DALLAS_LAT,
            "lon": DALLAS_LON,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["latitude"]  == DALLAS_LAT
    assert body["longitude"] == DALLAS_LON
    assert isinstance(body["street_name"], (str, type(None)))
    print(f"\nStreet: {body['street_name']}")


@pytest.mark.asyncio
async def test_current_street_invalid_coords():
    async with AsyncClient(base_url=PRODUCTION_URL, timeout=15) as client:
        resp = await client.get("/v1/current-street", params={
            "lat": 999.0,
            "lon": DALLAS_LON,
        })

    assert resp.status_code == 422
