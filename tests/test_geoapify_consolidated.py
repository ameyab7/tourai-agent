"""tests/test_geoapify_consolidated.py

Acceptance tests for the consolidated utils/geoapify_places.py module.

Run:
    venv/bin/python -m pytest tests/test_geoapify_consolidated.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import unittest.mock as mock

import httpx
import pytest

from utils.geoapify_places import _geoapify_to_poi, fetch_pois


# ── Feature builder ───────────────────────────────────────────────────────────

def _feature(
    name: str,
    lat: float,
    lon: float,
    categories: list[str],
    raw_tags: dict,
    place_id: str = "test_place_id",
) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "place_id":   place_id,
            "name":       name,
            "categories": categories,
            "datasource": {"raw": raw_tags},
        },
    }


# ── Unit tests (no HTTP) ──────────────────────────────────────────────────────

def test_enrichment_filters_generic_attraction():
    """tourism=attraction with no enrichment tags → _geoapify_to_poi returns None."""
    feat = _feature(
        name="Random Roadside Attraction",
        lat=32.0, lon=-96.0,
        categories=["tourism.attraction"],
        raw_tags={"tourism": "attraction"},
    )
    assert _geoapify_to_poi(feat) is None


def test_enrichment_keeps_attraction_with_wikidata():
    """Same feature but with a wikidata tag → passes filter, poi_type = 'attraction'."""
    feat = _feature(
        name="Dealey Plaza",
        lat=32.7789, lon=-96.8089,
        categories=["tourism.attraction"],
        raw_tags={"tourism": "attraction", "wikidata": "Q1085506"},
    )
    result = _geoapify_to_poi(feat)
    assert result is not None
    assert result["name"] == "Dealey Plaza"
    assert result["poi_type"] == "attraction"
    assert result["tags"]["wikidata"] == "Q1085506"


def test_castle_subcategory_mapped():
    """Category tourism.sights.castle → poi_type = 'castle' (most-specific match wins)."""
    feat = _feature(
        name="Edinburgh Castle",
        lat=55.9486, lon=-3.1999,
        categories=["tourism.sights.castle", "tourism.sights"],
        raw_tags={"historic": "castle", "wikidata": "Q23436"},
    )
    result = _geoapify_to_poi(feat)
    assert result is not None
    assert result["poi_type"] == "castle"


# ── Integration test with mocked HTTP ────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_by_coord_and_name():
    """Two features with the same name + coords (rounded to 4 d.p.) → only one returned."""
    f1 = _feature(
        name="Central Park",
        lat=40.78530, lon=-73.96530,
        categories=["leisure.park"],
        raw_tags={"leisure": "park"},
        place_id="place_id_1",
    )
    f2 = _feature(
        name="central park",        # same when lowercased
        lat=40.78531,               # rounds to 40.7853 at 4 d.p.
        lon=-73.96531,              # rounds to -73.9653 at 4 d.p.
        categories=["leisure.park"],
        raw_tags={"leisure": "park"},
        place_id="place_id_2",
    )

    payload = json.dumps({"type": "FeatureCollection", "features": [f1, f2]}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)

    # Capture the real class before patching so make_client doesn't call the Mock.
    _real_client = httpx.AsyncClient

    def make_client(**kwargs):
        kwargs.pop("transport", None)
        return _real_client(transport=transport, **kwargs)

    with mock.patch("utils.geoapify_places.httpx.AsyncClient", side_effect=make_client):
        result = await fetch_pois(40.785, -73.965, 1000, "test_api_key", limit=10)

    assert len(result) == 1
    assert result[0]["name"] == "Central Park"
