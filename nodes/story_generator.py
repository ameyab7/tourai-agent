# nodes/story_generator.py
#
# Generates a personalized, spoken tour guide story for a POI using Gemini.
#
# What it does:
#   1. Loads GEMINI_API_KEY from .env
#   2. Builds a prompt from the enriched POI context and the user's interest profile
#   3. Passes previously told story subjects so Gemini avoids repeating them
#   4. Returns a 80-140 word conversational story ready for text-to-speech
#
# The system prompt enforces tone and format constraints so every story
# sounds like a real tour guide — warm, specific, and memorable.

import logging
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a friendly, casual tour guide talking directly to a visitor. "
    "Speak like a knowledgeable local friend — simple, clear, and conversational. "
    "Avoid technical jargon, complex vocabulary, and academic language. "
    "Use short sentences. Keep the tone warm and easy to follow. "
    "Personalize the story to the traveler's interests. "
    "Keep stories between 80-140 words. "
    "End with one fun or surprising fact that will stick in their memory. "
    "Never start with 'Welcome to'. "
    "No lists. No flowery or over-the-top language."
)


class StoryGeneratorError(Exception):
    """Raised when story generation fails."""


def _build_human_message(
    enriched_context: dict,
    interest_profile: dict,
    told_stories: list[str],
) -> str:
    poi_name    = enriched_context.get("name", "this place")
    description = enriched_context.get("description", "")
    wiki        = enriched_context.get("wiki_extract", "")
    tags        = enriched_context.get("tags", {})

    interests_str = ", ".join(
        f"{cat} ({int(w * 100)}%)" for cat, w in sorted(interest_profile.items(), key=lambda x: -x[1])
    )

    avoided_str = (
        "None yet."
        if not told_stories
        else "\n".join(f"- {s}" for s in told_stories)
    )

    return f"""Generate a tour guide story for the following place.

POI NAME: {poi_name}

DESCRIPTION: {description or "Not available."}

WIKIPEDIA SUMMARY: {wiki or "Not available."}

ADDITIONAL TAGS: {tags}

TRAVELER INTERESTS (category: weight): {interests_str}

PREVIOUSLY TOLD STORIES (avoid repeating these subjects):
{avoided_str}

Write the story now. Do not include a title or heading."""


async def generate_story(
    enriched_context: dict,
    interest_profile: dict,
    told_stories: list[str] = [],
) -> str:
    """Generate a personalized tour guide story for a POI.

    Args:
        enriched_context: Dict containing POI name, description, wiki_extract, and tags.
        interest_profile: Dict mapping interest category to weight (0.0-1.0).
        told_stories: List of story subjects already told to this user — used to
            avoid repetition across consecutive narrations.

    Returns:
        Generated story text (80-140 words).

    Raises:
        StoryGeneratorError: If the API key is missing or the LLM call fails.
        ValueError: If enriched_context is missing the required 'name' key.
    """
    if not enriched_context.get("name"):
        raise ValueError("enriched_context must include a 'name' key")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise StoryGeneratorError(
            "GEMINI_API_KEY is not set. Add it to your .env file."
        )

    poi_name = enriched_context["name"]
    logger.debug("Generating story for '%s'", poi_name)

    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=api_key,
        temperature=0.85,
    )

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_build_human_message(enriched_context, interest_profile, told_stories)),
    ]

    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        raise StoryGeneratorError(
            f"Gemini API call failed for '{poi_name}': {e}"
        ) from e

    # Some Gemini models return content as a list of blocks, others as a plain string
    content = response.content
    if isinstance(content, list):
        story = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    else:
        story = content.strip()

    if not story:
        raise StoryGeneratorError(f"Gemini returned an empty response for '{poi_name}'")

    logger.debug("Story generated for '%s' (%d words)", poi_name, len(story.split()))
    return story
