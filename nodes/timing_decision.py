# nodes/timing_decision.py
#
# Decides whether it's the right time to tell a story.
# Checks: is there a new unseen POI, and has enough time passed since the last story?

import logging
from datetime import datetime, timezone
from state import TourGuideState

logger = logging.getLogger(__name__)

MIN_SECONDS_BETWEEN_STORIES = 90


async def timing_decision(state: TourGuideState) -> dict:
    top_poi = state.get("top_poi")
    told_pois = state.get("told_pois", set())
    last_story_time = state.get("last_story_time")

    if top_poi is None:
        logger.debug("Timing: no top POI — skip")
        return {"should_speak": False}

    poi_id = top_poi.get("id")
    if poi_id in told_pois:
        logger.debug("Timing: POI '%s' already told — skip", top_poi.get("name"))
        return {"should_speak": False}

    if last_story_time is not None:
        now = datetime.now(timezone.utc)
        elapsed = (now - last_story_time).total_seconds()
        if elapsed < MIN_SECONDS_BETWEEN_STORIES:
            logger.debug(
                "Timing: only %.1fs since last story (min=%ds) — skip",
                elapsed, MIN_SECONDS_BETWEEN_STORIES,
            )
            return {"should_speak": False}

    logger.debug("Timing: should_speak=True for '%s'", top_poi.get("name"))
    return {"should_speak": True}
