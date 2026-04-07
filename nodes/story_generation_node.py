# nodes/story_generation_node.py
#
# Calls the Gemini story generator with the enriched POI context
# and the user's interest profile to produce a personalized narration.

import logging
from state import TourGuideState
from nodes.story_generator import generate_story, StoryGeneratorError

logger = logging.getLogger(__name__)


async def story_generation_node(state: TourGuideState) -> dict:
    if not state.get("should_speak"):
        return {"story_text": ""}

    enriched_context = state.get("enriched_context", {})
    interest_profile = state.get("interest_profile", {})
    told_pois = state.get("told_pois", set())

    # Pass previously told POI names to avoid repetition
    told_stories = list(told_pois)

    logger.debug("Generating story for '%s'", enriched_context.get("name"))

    try:
        story_text = await generate_story(
            enriched_context=enriched_context,
            interest_profile=interest_profile,
            told_stories=told_stories,
        )
    except StoryGeneratorError as e:
        logger.error("Story generation failed: %s", e)
        return {"story_text": ""}
    except ValueError as e:
        logger.error("Invalid input for story generation: %s", e)
        return {"story_text": ""}

    return {"story_text": story_text}
