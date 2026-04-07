# nodes/poi_discovery.py
#
# Discovers nearby points of interest using the Overpass/OSM provider.
# Uses a tight 150m radius to find only immediately relevant POIs.

import logging
from state import TourGuideState
from providers.overpass import OverpassPOIProvider
from providers.base import POIProviderError

logger = logging.getLogger(__name__)

SEARCH_RADIUS_METERS = 150

_provider = OverpassPOIProvider()


async def poi_discovery(state: TourGuideState) -> dict:
    lat = state["latitude"]
    lon = state["longitude"]

    logger.debug("Discovering POIs near (%.6f, %.6f) radius=%dm", lat, lon, SEARCH_RADIUS_METERS)

    try:
        pois = await _provider.search_nearby(lat, lon, SEARCH_RADIUS_METERS)
    except POIProviderError as e:
        logger.error("POI discovery failed: %s", e)
        pois = []
    except ValueError as e:
        logger.error("Invalid coordinates for POI discovery: %s", e)
        pois = []

    logger.debug("Discovered %d POIs", len(pois))
    return {"nearby_pois": pois}
