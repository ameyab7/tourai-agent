import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.overpass import search_nearby, OverpassError
from utils.wikipedia import get_wikipedia_summary, WikipediaError
from utils.weather import get_current_weather, WeatherError
from utils import tts  # just verify import works

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

LAT = 32.7787
LON = -96.8083
LOCATION = "Dealey Plaza, Dallas"


async def main():
    print(f"\n{'='*60}")
    print(f"  Utils Test — {LOCATION}")
    print(f"  Coordinates: ({LAT}, {LON})")
    print(f"{'='*60}")

    # ------------------------------------------------------------------
    # 1. Overpass
    # ------------------------------------------------------------------
    print(f"\n  [1] Overpass POI Search (radius=200m)")
    print(f"  {'-'*56}")
    try:
        pois = await search_nearby(lat=LAT, lon=LON, radius=200)
        print(f"  Found {len(pois)} POIs:")
        for poi in pois:
            print(f"    [{poi['poi_type']}] {poi['name']}")
    except OverpassError as e:
        print(f"  ERROR: {e}")

    # ------------------------------------------------------------------
    # 2. Wikipedia
    # ------------------------------------------------------------------
    print(f"\n  [2] Wikipedia Summary — 'Dealey Plaza'")
    print(f"  {'-'*56}")
    try:
        wiki = await get_wikipedia_summary("Dealey Plaza")
        if wiki["found"]:
            print(f"  Title  : {wiki['title']}")
            print(f"  Length : {wiki['content_length']:,} chars")
            print(f"  Extract: {wiki['extract'][:200]}...")
        else:
            print("  No article found.")
    except WikipediaError as e:
        print(f"  ERROR: {e}")

    # ------------------------------------------------------------------
    # 3. Weather
    # ------------------------------------------------------------------
    print(f"\n  [3] Current Weather")
    print(f"  {'-'*56}")
    try:
        weather = await get_current_weather(lat=LAT, lon=LON)
        print(f"  Condition   : {weather['condition']}")
        print(f"  Temperature : {weather['temperature_c']}°C")
        print(f"  Feels like  : {weather['feels_like_c']}°C")
        print(f"  Wind speed  : {weather['wind_speed_kmh']} km/h")
        print(f"  Daylight    : {weather['is_daylight']}")
    except WeatherError as e:
        print(f"  ERROR: {e}")

    # ------------------------------------------------------------------
    # 4. TTS import check
    # ------------------------------------------------------------------
    print(f"\n  [4] TTS Import Check")
    print(f"  {'-'*56}")
    print(f"  utils.tts imported OK — synthesize function: {tts.synthesize}")

    print()


asyncio.run(main())
