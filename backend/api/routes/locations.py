"""api/routes/locations.py — Location search autocomplete."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from api.config import settings
from utils.google_places import search_destinations

router = APIRouter()
logger = logging.getLogger("tourai.api")


class LocationSearchRequest(BaseModel):
    query: str


class LocationOption(BaseModel):
    display_name: str
    short_name: str
    lat: float
    lon: float


class LocationSearchResponse(BaseModel):
    options: list[LocationOption]


@router.post("/v1/locations/search", response_model=LocationSearchResponse)
async def search_locations(body: LocationSearchRequest) -> LocationSearchResponse:
    """Return location suggestions for autocomplete."""
    if len(body.query.strip()) < 2:
        return LocationSearchResponse(options=[])

    results = await search_destinations(body.query, limit=8)

    options = [
        LocationOption(
            display_name=r["display_name"],
            short_name=r.get("short_name", r["display_name"].split(",")[0]),
            lat=r["lat"],
            lon=r["lon"],
        )
        for r in results
    ]

    logger.info(f"Location search: {body.query!r} -> {len(options)} results")
    return LocationSearchResponse(options=options)