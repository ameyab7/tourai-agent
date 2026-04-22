# utils/wikipedia.py
#
# Standalone async function for fetching Wikipedia article summaries.
# Used to enrich POIs with descriptive text and gauge their significance
# (longer articles = more notable place).

import logging
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://en.wikipedia.org/w/api.php"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
TIMEOUT_SECONDS = 10
HEADERS = {"User-Agent": "TourAI/1.0 (tour-guide-agent; contact@tourai.app)"}

_NOT_FOUND: dict = {"found": False, "extract": "", "content_length": 0, "thumbnail_url": None}


class WikipediaError(Exception):
    """Raised when the Wikipedia API call fails due to a network or API error."""


async def get_wikipedia_summary(poi_name: str, city: str = "Dallas Texas") -> dict:
    """Fetch a Wikipedia summary for a POI.

    Args:
        poi_name: Name of the point of interest (must not be empty).
        city: City context to disambiguate common place names in the search query.

    Returns:
        Dict with keys: found (bool), title, extract, content_length, thumbnail_url.
        Returns a minimal not-found dict if no article is found.

    Raises:
        ValueError: If poi_name is empty.
        WikipediaError: If a network or API error occurs.
    """
    if not poi_name or not poi_name.strip():
        raise ValueError("poi_name must not be empty")

    poi_name = poi_name.strip()
    logger.debug("Fetching Wikipedia summary for '%s' in '%s'", poi_name, city)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=HEADERS) as client:
            # Step 1: Search for the best matching article
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": f"{poi_name} {city}",
                "format": "json",
                "srlimit": 1,
            }
            search_resp = await client.get(SEARCH_URL, params=search_params)
            search_resp.raise_for_status()

            try:
                search_data = search_resp.json()
            except Exception as e:
                raise WikipediaError(f"Failed to parse Wikipedia search response: {e}") from e

            results = search_data.get("query", {}).get("search", [])
            if not results:
                logger.debug("No Wikipedia article found for '%s'", poi_name)
                return _NOT_FOUND

            top_result = results[0]
            article_title = top_result["title"]
            content_length = top_result.get("size", 0)
            logger.debug("Found Wikipedia article: '%s' (%d chars)", article_title, content_length)

            # Step 2: Fetch the article summary
            encoded_title = urllib.parse.quote(article_title, safe="")
            summary_resp = await client.get(SUMMARY_URL.format(title=encoded_title))

            if summary_resp.status_code == 404:
                logger.warning("Wikipedia summary 404 for '%s'", article_title)
                return {**_NOT_FOUND, "content_length": content_length}

            summary_resp.raise_for_status()

            try:
                summary_data = summary_resp.json()
            except Exception as e:
                raise WikipediaError(
                    f"Failed to parse Wikipedia summary response for '{article_title}': {e}"
                ) from e

    except httpx.TimeoutException:
        raise WikipediaError(f"Wikipedia API timed out after {TIMEOUT_SECONDS}s for '{poi_name}'")
    except httpx.ConnectError as e:
        raise WikipediaError(f"Could not connect to Wikipedia API: {e}") from e
    except httpx.HTTPStatusError as e:
        raise WikipediaError(
            f"Wikipedia API returned HTTP {e.response.status_code} for '{poi_name}'"
        ) from e

    return {
        "found": True,
        "title": summary_data.get("title", article_title),
        "extract": summary_data.get("extract", ""),
        "content_length": content_length,
        "thumbnail_url": summary_data.get("thumbnail", {}).get("source"),
    }
