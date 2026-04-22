# test_user_interests.py
#
# Tests OSM data quality by simulating 7 users at the SAME location,
# each with a different interest category. We query Overpass once, then
# filter the results per user based on their interest.
#
# Output:
#   1. Per-user detailed table of matched POIs
#   2. Summary table across all users showing OSM coverage quality
#
# This helps us decide: is OSM good enough, or do we need Google Places?

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers.overpass import OverpassPOIProvider

# ---------------------------------------------------------------------------
# Shared location — all users are at the same coordinates
# ---------------------------------------------------------------------------


LAT = 33.073614
LON = -96.823132
LOCATION = "Legacy Town Center, Plano"
RADIUS = 600

# ---------------------------------------------------------------------------
# 7 users — one per OSM tag category, all at the same location
# ---------------------------------------------------------------------------
USERS = [
    {"name": "Marcus", "persona": "History Buff",       "interest": ["historic"]},
    {"name": "Sofia",  "persona": "Art Lover",          "interest": ["tourism"]},
    {"name": "James",  "persona": "Spiritual Traveler", "interest": ["amenity"]},
    {"name": "Priya",  "persona": "Nature Enthusiast",  "interest": ["leisure"]},
    {"name": "Leo",    "persona": "Architecture Fan",   "interest": ["building", "tourism", "historic"]},
    {"name": "Elena",  "persona": "Coastal Explorer",   "interest": ["man_made"]},
    {"name": "Raj",    "persona": "Adventure Seeker",   "interest": ["natural"]},
]


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

def print_separator(widths, char="-"):
    print("+" + "+".join(char * (w + 2) for w in widths) + "+")


def print_row(values, widths):
    row = "|"
    for val, w in zip(values, widths):
        val = str(val)
        if len(val) > w:
            val = val[:w - 2] + ".."
        row += f" {val:<{w}} |"
    print(row)


def print_table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], min(len(str(cell)), 40))

    print_separator(widths, "-")
    print_row(headers, widths)
    print_separator(widths, "=")
    for row in rows:
        print_row(row, widths)
        print_separator(widths, "-")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    provider = OverpassPOIProvider()

    print(f"\n{'='*70}")
    print(f"  Location: {LOCATION}  ({LAT}, {LON})  |  Radius: {RADIUS}m")
    print(f"  Querying Overpass API...")
    print(f"{'='*70}")

    # Query once — reuse results for all users
    all_pois = await provider.search_nearby(LAT, LON, RADIUS)
    total_found = len(all_pois)
    print(f"\n  Total POIs returned by OSM: {total_found}\n")

    summary_rows = []

    for user in USERS:
        matched = [p for p in all_pois if p["poi_type"] in user["interest"]]
        top_pick = matched[0]["name"] if matched else "—"
        match_pct = f"{round(len(matched)/total_found*100)}%" if total_found else "0%"

        interest_label = " + ".join(user["interest"])
        print(f"\n{'='*70}")
        print(f"  {user['name']} — {user['persona']}  |  Interest: {interest_label}")
        print(f"{'='*70}")

        if matched:
            headers = ["#", "Name", "Category", "Subtype", "Lat", "Lon"]
            rows = []
            for i, poi in enumerate(matched, 1):
                subtype = (
                    poi["tags"].get("tourism")
                    or poi["tags"].get("historic")
                    or poi["tags"].get("amenity")
                    or poi["tags"].get("leisure")
                    or poi["tags"].get("building")
                    or poi["tags"].get("man_made")
                    or poi["tags"].get("natural")
                    or "—"
                )
                rows.append([i, poi["name"], poi["poi_type"], subtype, round(poi["lat"], 5), round(poi["lon"], 5)])
            print_table(headers, rows)
        else:
            print("  No POIs matched this user's interest at this location.\n")

        summary_rows.append([
            user["name"],
            user["persona"],
            interest_label,
            len(matched),
            match_pct,
            top_pick,
        ])

    # Final summary table
    print(f"\n\n{'='*70}")
    print(f"  SUMMARY — OSM Coverage @ {LOCATION}")
    print(f"  Total POIs found: {total_found}  |  Radius: {RADIUS}m")
    print(f"{'='*70}\n")

    summary_headers = ["User", "Persona", "Interests", "Matched", "Match %", "Top Pick"]
    print_table(summary_headers, summary_rows)

    print("\nConclusion:")
    good = [r for r in summary_rows if int(r[3]) >= 3]
    poor = [r for r in summary_rows if int(r[3]) < 3]
    print(f"  Strong OSM coverage ({len(good)}/7 interests): {', '.join(r[0] for r in good) or 'none'}")
    print(f"  Weak OSM coverage   ({len(poor)}/7 interests): {', '.join(r[0] for r in poor) or 'none'}")
    print()


asyncio.run(main())
