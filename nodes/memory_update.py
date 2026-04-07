# nodes/memory_update.py
#
# Updates agent memory after a story has been told:
# - Marks the POI as told so it won't be repeated
# - Records the time of the last story for cooldown timing

import logging
from datetime import datetime, timezone
from state import TourGuideState

logger = logging.getLogger(__name__)


async def memory_update(state: TourGuideState) -> dict:
    if not state.get("should_speak"):
        return {}

    top_poi = state.get("top_poi", {})
    poi_id = top_poi.get("id")
    poi_name = top_poi.get("name", "unknown")
    story_text = state.get("story_text", "")
    now = datetime.now(timezone.utc)

    told_pois = set(state.get("told_pois", set()))
    if poi_id:
        told_pois.add(poi_id)

    print(f"\n{'='*60}")
    print(f"  Story told: {poi_name}")
    print(f"  Time      : {now.strftime('%H:%M:%S UTC')}")
    print(f"  Words     : {len(story_text.split())}")
    print(f"  Total POIs told so far: {len(told_pois)}")
    print(f"{'='*60}\n")

    logger.debug("Memory updated — told_pois now has %d entries", len(told_pois))

    return {
        "told_pois": told_pois,
        "last_story_time": now,
    }
