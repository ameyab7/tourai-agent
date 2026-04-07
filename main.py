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
    {"name": "Dealey Plaza",        "lat": 32.7787, "lon": -96.8083},
    {"name": "Reunion Tower",       "lat": 32.7755, "lon": -96.8088},
    {"name": "Dallas Arts District", "lat": 32.7893, "lon": -96.7988},
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

    # told_pois carries over so the agent never repeats a POI across stops.
    # last_story_time resets each stop — in simulation we're teleporting,
    # not walking, so the 90s cooldown should not apply between locations.
    told_pois: set = set()
    stories_told = 0
    audio_files: list[str] = []

    print("\n" + "="*60)
    print("  TourAI — Dallas Walking Tour")
    print("="*60)

    for stop in STOPS:
        print(f"\n  Approaching: {stop['name']}")
        print(f"  Coordinates: ({stop['lat']}, {stop['lon']})")
        print(f"  {'-'*56}")

        initial_state: TourGuideState = {
            "user_id":          "demo",
            "latitude":         stop["lat"],
            "longitude":        stop["lon"],
            "speed_mps":        0.0,
            "heading":          0.0,
            "nearby_pois":      [],
            "top_poi":          None,
            "should_speak":     False,
            "enriched_context": {},
            "story_text":       "",
            "audio_bytes":      b"",
            "audio_filepath":   "",
            "told_pois":        told_pois,
            "last_story_time":  None,       # reset per stop in simulation
            "interest_profile": INTEREST_PROFILE,
            "search_radius":    300,        # wider radius for simulation
        }

        result = await graph.ainvoke(initial_state)

        # Only told_pois carries over — memory without the cooldown
        told_pois = result.get("told_pois", told_pois)

        story_text     = result.get("story_text", "")
        audio_filepath = result.get("audio_filepath", "")

        if story_text:
            stories_told += 1
            if audio_filepath:
                audio_files.append(audio_filepath)

            print(f"\n  Story:\n")
            for sentence in story_text.split(". "):
                sentence = sentence.strip()
                if sentence:
                    print(f"    {sentence}.")
            if audio_filepath:
                print(f"\n  Audio saved: {audio_filepath}")
        else:
            print(f"\n  (No story triggered at this stop)")

    # Summary
    print(f"\n{'='*60}")
    print(f"  Tour complete. Stories told: {stories_told}. Audio files saved to output/")
    print(f"{'='*60}")
    if audio_files:
        print(f"\n  Play your tour:")
        for f in audio_files:
            print(f"    open \"{f}\"")
    print()


asyncio.run(run_tour())
