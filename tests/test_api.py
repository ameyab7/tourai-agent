"""
tests/test_api.py — Comprehensive tests for api/main.py

Run all:
    pytest tests/test_api.py -v

Run by category:
    pytest tests/test_api.py -v -m unit
    pytest tests/test_api.py -v -m integration
    pytest tests/test_api.py -v -m load

Requirements:
    pip install pytest pytest-asyncio httpx
"""

import sys
import os

# Project root must be on sys.path before any local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# GROQ_API_KEY is required by Settings at import time — set before importing api.main
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import api.main as api_module
from api.main import app

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

DALLAS_LAT  =  32.7787
DALLAS_LON  = -96.8083
DALLAS_HDG  =  90.0     # heading East

OCEAN_LAT   = 0.0
OCEAN_LON   = 0.0
OCEAN_HDG   = 0.0

# POI dicts as returned by overpass.search_nearby (no distance/angle yet)
SAMPLE_POIS = [
    {
        "id":       123456,
        "name":     "Reunion Tower",
        "lat":      32.7757,
        "lon":      -96.8089,
        "poi_type": "tourism",
        "tags": {
            "tourism":       "attraction",
            "height":        "171",
            "building:levels": "50",
            "wikipedia":     "en:Reunion Tower",
            "addr:street":   "Reunion Boulevard",
        },
        "geometry": [],
    },
    {
        "id":       234567,
        "name":     "Perot Museum of Nature and Science",
        "lat":      32.7866,
        "lon":      -96.8070,
        "poi_type": "tourism",
        "tags": {
            "tourism":     "museum",
            "addr:street": "Field Street",
        },
        "geometry": [],
    },
    {
        "id":       345678,
        "name":     "Dallas City Hall",
        "lat":      32.7762,
        "lon":      -96.7981,
        "poi_type": "amenity",
        "tags": {
            "amenity":     "townhall",
            "addr:street": "Marilla Street",
        },
        "geometry": [],
    },
]

# Same POIs enriched with distance/angle — as returned by visibility.filter_visible
SAMPLE_VISIBLE_POIS = [
    {**p, "distance_m": 200.0 + i * 150, "angle_deg": 30.0 + i * 10}
    for i, p in enumerate(SAMPLE_POIS)
]

SAMPLE_STREET = "Commerce Street"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    """Clear in-memory cache and rate-limit buckets before and after each test."""
    api_module._cache._store.clear()
    api_module._rate_buckets.clear()
    yield
    api_module._cache._store.clear()
    api_module._rate_buckets.clear()


@pytest_asyncio.fixture
async def client():
    """Async test client backed by the ASGI app — no real network."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest.fixture
def mock_deps():
    """Patch all three external utils at once. Returns the three mocks."""
    with (
        patch.object(api_module.poi_source,  "search_nearby",  new_callable=AsyncMock) as m_overpass,
        patch.object(api_module.osrm,        "get_current_street", new_callable=AsyncMock) as m_osrm,
        patch.object(api_module.visibility,  "filter_visible", new_callable=AsyncMock) as m_vis,
    ):
        m_overpass.return_value = SAMPLE_POIS
        m_osrm.return_value     = SAMPLE_STREET
        m_vis.return_value      = (SAMPLE_VISIBLE_POIS, [])
        yield m_overpass, m_osrm, m_vis


# ---------------------------------------------------------------------------
# Unit tests — /v1/visible-pois
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_success(client, mock_deps):
    """Valid Dallas coordinates return a well-formed response."""
    resp = await client.post("/v1/visible-pois", json={
        "latitude":  DALLAS_LAT,
        "longitude": DALLAS_LON,
        "heading":   DALLAS_HDG,
    })

    assert resp.status_code == 200
    body = resp.json()
    assert "visible_pois"  in body
    assert "street_name"   in body
    assert "total_checked" in body
    assert "cache_hit"     in body
    assert "timestamp"     in body

    assert body["street_name"]   == SAMPLE_STREET
    assert body["total_checked"] == len(SAMPLE_POIS)
    assert body["cache_hit"]     is False
    assert len(body["visible_pois"]) == len(SAMPLE_VISIBLE_POIS)

    # Verify POI shape
    poi = body["visible_pois"][0]
    for field in ("id", "name", "lat", "lon", "poi_type", "distance_m", "angle_deg", "tags"):
        assert field in poi, f"missing field: {field}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_invalid_lat(client):
    """Latitude outside [-90, 90] returns 422."""
    resp = await client.post("/v1/visible-pois", json={
        "latitude":  999.0,
        "longitude": DALLAS_LON,
        "heading":   DALLAS_HDG,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_invalid_lon(client):
    """Longitude outside [-180, 180] returns 422."""
    resp = await client.post("/v1/visible-pois", json={
        "latitude":  DALLAS_LAT,
        "longitude": 999.0,
        "heading":   DALLAS_HDG,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_invalid_heading(client):
    """Heading >= 360 returns 422."""
    resp = await client.post("/v1/visible-pois", json={
        "latitude":  DALLAS_LAT,
        "longitude": DALLAS_LON,
        "heading":   400.0,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_missing_fields(client):
    """Missing required fields returns 422."""
    resp = await client.post("/v1/visible-pois", json={"latitude": DALLAS_LAT})
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_empty_area(client):
    """Ocean coordinates with no POIs return empty visible_pois list."""
    with (
        patch.object(api_module.poi_source,   "search_nearby",     new_callable=AsyncMock, return_value=[]),
        patch.object(api_module.osrm,       "get_current_street", new_callable=AsyncMock, return_value=None),
        patch.object(api_module.visibility, "filter_visible",     new_callable=AsyncMock, return_value=([], [])),
    ):
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  OCEAN_LAT,
            "longitude": OCEAN_LON,
            "heading":   OCEAN_HDG,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["visible_pois"]  == []
    assert body["total_checked"] == 0
    assert body["street_name"]   is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_overpass_failure_returns_502(client):
    """Overpass error surfaces as 502, not an internal stacktrace."""
    with (
        patch.object(api_module.poi_source, "search_nearby",
                     new_callable=AsyncMock, side_effect=RuntimeError("connection refused")),
    ):
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    assert resp.status_code == 502
    body = resp.json()
    # Internal error detail must not leak
    assert "connection refused" not in body.get("detail", "")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_visibility_failure_returns_502(client):
    """Visibility filter error surfaces as 502."""
    with (
        patch.object(api_module.poi_source,   "search_nearby",      new_callable=AsyncMock, return_value=SAMPLE_POIS),
        patch.object(api_module.osrm,       "get_current_street", new_callable=AsyncMock, return_value=SAMPLE_STREET),
        patch.object(api_module.visibility, "filter_visible",     new_callable=AsyncMock, side_effect=RuntimeError("groq timeout")),
    ):
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    assert resp.status_code == 502
    assert "groq timeout" not in resp.json().get("detail", "")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_visible_pois_osrm_failure_is_nonfatal(client):
    """OSRM street lookup failure does not fail the endpoint — returns street_name=null."""
    with (
        patch.object(api_module.poi_source,   "search_nearby",      new_callable=AsyncMock, return_value=SAMPLE_POIS),
        patch.object(api_module.osrm,       "get_current_street", new_callable=AsyncMock, side_effect=RuntimeError("osrm down")),
        patch.object(api_module.visibility, "filter_visible",     new_callable=AsyncMock, return_value=(SAMPLE_VISIBLE_POIS, [])),
    ):
        resp = await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   DALLAS_HDG,
        })

    assert resp.status_code == 200
    assert resp.json()["street_name"] is None


# ---------------------------------------------------------------------------
# Unit tests — /v1/current-street
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_current_street_success(client):
    with patch.object(api_module.osrm, "get_current_street",
                      new_callable=AsyncMock, return_value=SAMPLE_STREET):
        resp = await client.get("/v1/current-street", params={
            "lat": DALLAS_LAT,
            "lon": DALLAS_LON,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["street_name"] == SAMPLE_STREET
    assert body["latitude"]    == DALLAS_LAT
    assert body["longitude"]   == DALLAS_LON


@pytest.mark.asyncio
@pytest.mark.unit
async def test_current_street_unnamed_road(client):
    """Unnamed roads return street_name=null with 200 (not an error)."""
    with patch.object(api_module.osrm, "get_current_street",
                      new_callable=AsyncMock, return_value=None):
        resp = await client.get("/v1/current-street", params={
            "lat": DALLAS_LAT,
            "lon": DALLAS_LON,
        })

    assert resp.status_code == 200
    assert resp.json()["street_name"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_current_street_invalid_lat(client):
    resp = await client.get("/v1/current-street", params={"lat": 999.0, "lon": DALLAS_LON})
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_current_street_invalid_lon(client):
    resp = await client.get("/v1/current-street", params={"lat": DALLAS_LAT, "lon": 999.0})
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.unit
async def test_current_street_osrm_error_returns_502(client):
    with patch.object(api_module.osrm, "get_current_street",
                      new_callable=AsyncMock, side_effect=RuntimeError("timeout")):
        resp = await client.get("/v1/current-street", params={
            "lat": DALLAS_LAT,
            "lon": DALLAS_LON,
        })

    assert resp.status_code == 502
    assert "timeout" not in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# Unit tests — /health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_health_all_healthy(client):
    """Both Overpass and OSRM up → status: healthy."""
    overpass_resp = MagicMock(status_code=200)
    osrm_resp     = MagicMock(status_code=200)
    osrm_resp.json.return_value = {"code": "Ok"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)
    mock_client.get        = AsyncMock(side_effect=[overpass_resp, osrm_resp])

    with patch.object(api_module.httpx, "AsyncClient", return_value=mock_client):
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert len(body["dependencies"]) == 2
    assert all(d["ok"] for d in body["dependencies"])
    assert "timestamp" in body


@pytest.mark.asyncio
@pytest.mark.unit
async def test_health_degraded(client):
    """One dep down → status: degraded."""
    overpass_resp = MagicMock(status_code=200)

    mock_client_overpass = AsyncMock()
    mock_client_overpass.__aenter__ = AsyncMock(return_value=mock_client_overpass)
    mock_client_overpass.__aexit__  = AsyncMock(return_value=False)
    mock_client_overpass.get        = AsyncMock(return_value=overpass_resp)

    mock_client_osrm = AsyncMock()
    mock_client_osrm.__aenter__ = AsyncMock(return_value=mock_client_osrm)
    mock_client_osrm.__aexit__  = AsyncMock(return_value=False)
    mock_client_osrm.get        = AsyncMock(side_effect=Exception("OSRM unreachable"))

    with patch.object(api_module.httpx, "AsyncClient",
                      side_effect=[mock_client_overpass, mock_client_osrm]):
        resp = await client.get("/health")

    body = resp.json()
    assert body["status"] == "degraded"
    statuses = {d["name"]: d["ok"] for d in body["dependencies"]}
    assert statuses["overpass"] is True
    assert statuses["osrm"]     is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_health_unhealthy(client):
    """Both deps down → status: unhealthy."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)
    mock_client.get        = AsyncMock(side_effect=Exception("all down"))

    with patch.object(api_module.httpx, "AsyncClient", return_value=mock_client):
        resp = await client.get("/health")

    assert resp.json()["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.integration
async def test_end_to_end_flow(client, mock_deps):
    """
    Full GPS → visible POIs flow.
    Verifies the complete response contract including POI field types.
    """
    resp = await client.post("/v1/visible-pois", json={
        "latitude":  DALLAS_LAT,
        "longitude": DALLAS_LON,
        "heading":   DALLAS_HDG,
        "radius":    400.0,
    })

    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape
    assert isinstance(body["visible_pois"],  list)
    assert isinstance(body["total_checked"], int)
    assert isinstance(body["cache_hit"],     bool)
    assert isinstance(body["timestamp"],     str)
    assert body["cache_hit"] is False

    # Each POI has correct types
    for poi in body["visible_pois"]:
        assert isinstance(poi["id"],         (int, str))
        assert isinstance(poi["name"],       str)
        assert isinstance(poi["lat"],        float)
        assert isinstance(poi["lon"],        float)
        assert isinstance(poi["distance_m"], float)
        assert isinstance(poi["angle_deg"],  float)
        assert isinstance(poi["tags"],       dict)
        assert -90  <= poi["lat"] <= 90
        assert -180 <= poi["lon"] <= 180

    # All three utils were called exactly once
    m_overpass, m_osrm, m_vis = mock_deps
    m_overpass.assert_awaited_once()
    m_osrm.assert_awaited_once()
    m_vis.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cache_hit(client, mock_deps):
    """
    Two requests with identical coords should hit the cache on the second call.
    Overpass/visibility must only be called once total.
    """
    payload = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading": DALLAS_HDG}

    resp1 = await client.post("/v1/visible-pois", json=payload)
    resp2 = await client.post("/v1/visible-pois", json=payload)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    assert resp1.json()["cache_hit"] is False
    assert resp2.json()["cache_hit"] is True

    m_overpass, _, m_vis = mock_deps
    # External calls only happened on the first request
    assert m_overpass.await_count == 1
    assert m_vis.await_count      == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cache_different_heading_buckets(client, mock_deps):
    """
    Headings in different 22.5° buckets should produce separate cache entries.
    """
    payload_east  = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading":  90.0}
    payload_south = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading": 180.0}

    resp1 = await client.post("/v1/visible-pois", json=payload_east)
    resp2 = await client.post("/v1/visible-pois", json=payload_south)

    assert resp1.json()["cache_hit"] is False
    assert resp2.json()["cache_hit"] is False  # different bucket → cache miss

    m_overpass, _, _ = mock_deps
    # POI data is cached by location only, so overpass is only called once
    assert m_overpass.await_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_poi_cache_reused_across_headings(client, mock_deps):
    """
    Overpass is called once even when heading changes — POI cache is heading-independent.
    """
    m_overpass, _, _ = mock_deps

    for heading in [0.0, 45.0, 90.0, 135.0]:
        await client.post("/v1/visible-pois", json={
            "latitude":  DALLAS_LAT,
            "longitude": DALLAS_LON,
            "heading":   heading,
        })

    assert m_overpass.await_count == 1  # cached after the first call


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rate_limit(client, mock_deps):
    """
    101st request from the same IP within one minute should return 429.
    We pre-fill the bucket to avoid making 100 actual HTTP calls.
    """
    now = time.monotonic()
    # ASGITransport presents as 127.0.0.1 — pre-fill 100 hits to trigger the limit
    api_module._rate_buckets["127.0.0.1"] = [now - i * 0.1 for i in range(100)]

    resp = await client.post("/v1/visible-pois", json={
        "latitude":  DALLAS_LAT,
        "longitude": DALLAS_LON,
        "heading":   DALLAS_HDG,
    })

    assert resp.status_code == 429
    assert resp.json()["detail"] == "Too many requests"
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rate_limit_resets_after_window(client, mock_deps):
    """
    Old hits (> 60s ago) do not count toward the rate limit.
    """
    now = time.monotonic()
    # All 100 hits are > 60 seconds old — should have expired from the window
    api_module._rate_buckets["127.0.0.1"] = [now - 61 - i for i in range(100)]

    resp = await client.post("/v1/visible-pois", json={
        "latitude":  DALLAS_LAT,
        "longitude": DALLAS_LON,
        "heading":   DALLAS_HDG,
    })

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Load tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.load
async def test_concurrent_requests(client, mock_deps):
    """50 simultaneous requests all return 200."""
    payload = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading": DALLAS_HDG}

    responses = await asyncio.gather(*[
        client.post("/v1/visible-pois", json=payload)
        for _ in range(50)
    ])

    statuses = [r.status_code for r in responses]
    assert all(s == 200 for s in statuses), f"Non-200 statuses: {set(statuses)}"


@pytest.mark.asyncio
@pytest.mark.load
async def test_response_time_cached(client, mock_deps):
    """
    Cached response should complete in < 500ms.
    Pre-warm the cache with a first request, then time the second.
    """
    payload = {"latitude": DALLAS_LAT, "longitude": DALLAS_LON, "heading": DALLAS_HDG}

    await client.post("/v1/visible-pois", json=payload)   # warm cache

    start = time.perf_counter()
    resp  = await client.post("/v1/visible-pois", json=payload)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    assert resp.json()["cache_hit"] is True
    assert elapsed_ms < 500, f"Cached response too slow: {elapsed_ms:.0f}ms"


@pytest.mark.asyncio
@pytest.mark.load
async def test_response_time_fresh(client, mock_deps):
    """
    Fresh request (mocked deps, no network) should complete in < 2000ms.
    """
    payload = {"latitude": DALLAS_LAT + 10, "longitude": DALLAS_LON + 10, "heading": 45.0}

    start    = time.perf_counter()
    resp     = await client.post("/v1/visible-pois", json=payload)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    assert elapsed_ms < 2000, f"Fresh response too slow: {elapsed_ms:.0f}ms"


@pytest.mark.asyncio
@pytest.mark.load
async def test_concurrent_different_locations(client, mock_deps):
    """
    50 requests with distinct coordinates all succeed and produce separate cache entries.
    """
    payloads = [
        {"latitude": DALLAS_LAT + i * 0.01, "longitude": DALLAS_LON + i * 0.01, "heading": float(i * 7 % 359)}
        for i in range(50)
    ]

    responses = await asyncio.gather(*[
        client.post("/v1/visible-pois", json=p) for p in payloads
    ])

    assert all(r.status_code == 200 for r in responses)
    # All should be cache misses since every location is unique
    assert all(not r.json()["cache_hit"] for r in responses)
