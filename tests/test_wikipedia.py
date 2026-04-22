import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from enrichment.wikipedia import get_wikipedia_summary, WikipediaEnrichmentError

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

TEST_POIS = [
    "Reunion Tower",
    "Dealey Plaza",
]


async def main():
    for poi_name in TEST_POIS:
        print(f"\n{'='*60}")
        print(f"  POI: {poi_name}")
        print(f"{'='*60}")

        try:
            result = await get_wikipedia_summary(poi_name)
        except WikipediaEnrichmentError as e:
            print(f"  ERROR: {e}")
            continue

        if not result["found"]:
            print("  No Wikipedia article found.")
            continue

        print(f"  Title          : {result['title']}")
        print(f"  Content Length : {result['content_length']:,} chars")
        print(f"  Thumbnail      : {result['thumbnail_url'] or 'None'}")
        print(f"\n  Extract:")
        print(f"  {result['extract']}")


asyncio.run(main())
