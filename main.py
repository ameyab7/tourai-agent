import asyncio
import logging
import os

from graph import build_graph
from state import TourGuideState

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ---------------------------------------------------------------------------
# Simulated GPS stops — a walk through downtown Dallas
# ---------------------------------------------------------------------------
STOPS = [
    {"name": "Dealey Plaza",       "lat": 32.7787, "lon": -96.8083},
    {"name": "Reunion Tower",      "lat": 32.7755, "lon": -96.8088},
    {"name": "Dallas Arts District","lat": 32.7893, "lon": -96.7988},
]

INTEREST_PROFILE = {
    "history":      0.9,
    "architecture": 0.8,
    "photography":  0.7,
    "art":          0.5,
}


async def run_tour():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    graph = build_graph()

    told_pois = set()
    last_story_time = None
    stories_told = 0

    print("\n" + "="*60)
    print("  TourAI — Dallas Walking Tour")
    print("="*60)

    for stop in STOPS:
        print(f"\n  Approaching: {stop['name']} ({stop['lat']}, {stop['lon']})")
        print(f"  {'-'*56}")

        initial_state: TourGuideState = {
            "user_id":         "demo",
            "latitude":        stop["lat"],
            "longitude":       stop["lon"],
            "speed_mps":       0.0,
            "heading":         0.0,
            "nearby_pois":     [],
            "top_poi":         None,
            "should_speak":    False,
            "enriched_context": {},
            "story_text":      "",
            "audio_bytes":     b"",
            "told_pois":       told_pois,
            "last_story_time": last_story_time,
            "interest_profile": INTEREST_PROFILE,
            "search_radius":   150,
        }

        result = await graph.ainvoke(initial_state)

        # Carry over memory between stops
        told_pois = result.get("told_pois", told_pois)
        last_story_time = result.get("last_story_time", last_story_time)

        story_text = result.get("story_text", "")
        audio_filepath = result.get("audio_filepath", "")

        if story_text:
            stories_told += 1
            print(f"\n  Story:\n")
            for line in story_text.split(". "):
                if line:
                    print(f"    {line.strip()}.")
            if audio_filepath:
                print(f"\n  Audio: {audio_filepath}")
        else:
            print("  (No story triggered at this stop)")

    print(f"\n{'='*60}")
    print(f"  Tour complete.")
    print(f"  Stories told : {stories_told}")
    print(f"  Audio files  : {OUTPUT_DIR}")
    print(f"{'='*60}\n")


asyncio.run(run_tour())
