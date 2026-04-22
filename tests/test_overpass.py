import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers.overpass import OverpassPOIProvider
from providers.base import POIProviderError

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


async def main():
    provider = OverpassPOIProvider()

    try:
        pois = await provider.search_nearby(lat=32.784555, lon=-96.795781, radius=500)
    except POIProviderError as e:
        print(f"ERROR: {e}")
        return
    except ValueError as e:
        print(f"Invalid input: {e}")
        return

    print(f"Found {len(pois)} POIs near Dallas (32.784555, -96.795781):\n")
    for poi in pois:
        print(f"  [{poi['poi_type']}] {poi['name']}")
        print(f"    id={poi['id']}, lat={poi['lat']}, lon={poi['lon']}")
        print(f"    tags={poi['tags']}")
        print()


asyncio.run(main())
