import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nodes.story_generator import generate_story, StoryGeneratorError

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

ENRICHED_CONTEXT = {
    "name": "Reunion Tower",
    "description": (
        "Reunion Tower is a 561 ft (171 m) observation tower in Dallas, "
        "one of the city's most recognizable landmarks, located at 300 Reunion Boulevard."
    ),
    "wiki_extract": (
        "Reunion Tower is a 561 ft (171 m) observation tower in Dallas, and one of the city's "
        "most recognizable landmarks. The tower is located at 300 Reunion Boulevard in the Reunion "
        "district of downtown Dallas, which is named after the mid-nineteenth century commune La Reunion. "
        "A free-standing structure until the construction of an addition to the Hyatt Regency Dallas "
        "and surrounding complex in 1998, the tower is the city's 15th tallest occupiable structure. "
        "It was designed by architectural firm Welton Becket & Associates."
    ),
    "tags": {
        "tourism": "attraction",
        "architect": "Welton Becket & Associates",
        "website": "https://reuniontower.com",
        "wikidata": "Q1191477",
    },
}

INTEREST_PROFILE = {
    "architecture": 0.9,
    "photography": 0.8,
}


async def main():
    print(f"\n{'='*60}")
    print(f"  Story Generator Test — Reunion Tower")
    print(f"  Interests: architecture (90%), photography (80%)")
    print(f"{'='*60}\n")

    try:
        story = await generate_story(
            enriched_context=ENRICHED_CONTEXT,
            interest_profile=INTEREST_PROFILE,
            told_stories=[],
        )
    except StoryGeneratorError as e:
        print(f"ERROR: {e}")
        return

    word_count = len(story.split())
    print(f"  Generated Story ({word_count} words):\n")
    print(f"  {story}")
    print()


asyncio.run(main())
