import asyncio

from providers.overpass import OverpassPOIProvider


async def main():
    provider = OverpassPOIProvider()
    pois = await provider.search_nearby(lat=32.784555, lon=-96.795781, radius=500)

    print(f"Found {len(pois)} POIs near Dallas (32.784555, -96.795781):\n")
    for poi in pois:
        print(f"  [{poi['poi_type']}] {poi['name']}")
        print(f"    id={poi['id']}, lat={poi['lat']}, lon={poi['lon']}")
        print(f"    tags={poi['tags']}")
        print()


asyncio.run(main())
