import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import search_pois, enrich_poi, get_weather, get_user_profile, get_session_history

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

LAT = 32.7787
LON = -96.8083
LOCATION = "Dealey Plaza, Dallas"


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def main():
    print(f"\n{'='*60}")
    print(f"  TourAI Tools Test — {LOCATION}")
    print(f"{'='*60}")

    # ------------------------------------------------------------------
    # 1. search_pois
    # ------------------------------------------------------------------
    section("Tool 1: search_pois (radius=200m)")
    result = search_pois.invoke({
        "latitude": LAT,
        "longitude": LON,
        "radius": 200,
    })
    print(result)

    # ------------------------------------------------------------------
    # 2. enrich_poi
    # ------------------------------------------------------------------
    section("Tool 2: enrich_poi — Reunion Tower")
    poi_tags = '{"tourism": "attraction", "wikidata": "Q1191477", "architect": "Welton Becket & Associates"}'
    result = enrich_poi.invoke({
        "poi_name": "Reunion Tower",
        "poi_tags": poi_tags,
        "city": "Dallas",
        "sources": "wikipedia,wikidata",
    })
    print(result)

    # ------------------------------------------------------------------
    # 3. get_weather
    # ------------------------------------------------------------------
    section("Tool 3: get_weather")
    result = get_weather.invoke({
        "latitude": LAT,
        "longitude": LON,
    })
    print(result)

    # ------------------------------------------------------------------
    # 4. get_user_profile
    # ------------------------------------------------------------------
    section("Tool 4: get_user_profile")
    result = get_user_profile.invoke({"user_id": "demo"})
    print(result)

    # ------------------------------------------------------------------
    # 5. get_session_history
    # ------------------------------------------------------------------
    section("Tool 5: get_session_history (empty)")
    result = get_session_history.invoke({"session_id": "session_001"})
    print(result)

    print(f"\n{'='*60}")
    print(f"  All tools tested successfully.")
    print(f"{'='*60}\n")


main()
