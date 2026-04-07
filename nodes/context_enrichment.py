# nodes/context_enrichment.py
#
# Enriches the top POI with Wikipedia data and merges it with OSM tags
# into a single context dict that the story generator can use.

import logging
from state import TourGuideState
from enrichment.wikipedia import get_wikipedia_summary, WikipediaEnrichmentError

logger = logging.getLogger(__name__)


async def context_enrichment(state: TourGuideState) -> dict:
    if not state.get("should_speak"):
        return {"enriched_context": {}}

    top_poi = state["top_poi"]
    poi_name = top_poi["name"]

    logger.debug("Enriching context for '%s'", poi_name)

    wiki_extract = ""
    wiki_thumbnail = None

    try:
        wiki = await get_wikipedia_summary(poi_name)
        if wiki["found"]:
            wiki_extract = wiki["extract"]
            wiki_thumbnail = wiki["thumbnail_url"]
            logger.debug(
                "Wikipedia found for '%s' (%d chars)", poi_name, wiki["content_length"]
            )
        else:
            logger.debug("No Wikipedia article for '%s'", poi_name)
    except WikipediaEnrichmentError as e:
        logger.warning("Wikipedia enrichment failed for '%s': %s", poi_name, e)

    enriched_context = {
        "name": poi_name,
        "description": top_poi["tags"].get("description", ""),
        "wiki_extract": wiki_extract,
        "wiki_thumbnail": wiki_thumbnail,
        "tags": top_poi["tags"],
        "poi_type": top_poi["poi_type"],
        "lat": top_poi["lat"],
        "lon": top_poi["lon"],
    }

    return {"enriched_context": enriched_context}
