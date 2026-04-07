# nodes/significance_filter.py
#
# Scores each nearby POI and selects the most relevant one for this user.
# Fetches Wikipedia content_length for each named POI to inform significance scoring.
# Filters out POIs below the significance threshold and picks the top scorer.

import logging
from state import TourGuideState
from nodes.significance import score_poi
from enrichment.wikipedia import get_wikipedia_summary, WikipediaEnrichmentError

logger = logging.getLogger(__name__)

SIGNIFICANCE_THRESHOLD = 0.45


async def significance_filter(state: TourGuideState) -> dict:
    pois = state.get("nearby_pois", [])
    interest_profile = state.get("interest_profile", {})
    lat = state["latitude"]
    lon = state["longitude"]
    search_radius = state.get("search_radius", 150)

    if not pois:
        logger.debug("No POIs to score")
        return {"top_poi": None}

    scored = []

    for poi in pois:
        wiki_content_length = 0
        try:
            wiki = await get_wikipedia_summary(poi["name"])
            if wiki["found"]:
                wiki_content_length = wiki["content_length"]
        except WikipediaEnrichmentError as e:
            logger.warning("Wikipedia lookup failed for '%s': %s", poi["name"], e)

        try:
            score = score_poi(
                poi=poi,
                interest_profile=interest_profile,
                user_lat=lat,
                user_lon=lon,
                search_radius=search_radius,
                wiki_content_length=wiki_content_length,
            )
        except ValueError as e:
            logger.warning("Could not score POI '%s': %s", poi["name"], e)
            continue

        if score >= SIGNIFICANCE_THRESHOLD:
            scored.append((score, poi))
            logger.debug("POI '%s' scored %.4f", poi["name"], score)

    if not scored:
        logger.debug("No POIs passed significance threshold (%.2f)", SIGNIFICANCE_THRESHOLD)
        return {"top_poi": None}

    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_poi = scored[0]
    logger.debug("Top POI: '%s' (score=%.4f)", top_poi["name"], top_score)

    return {"top_poi": top_poi}
