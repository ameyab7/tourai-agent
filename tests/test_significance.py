import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nodes.significance import score_poi

# ---------------------------------------------------------------------------
# Shared test location — user is standing near Dealey Plaza, Dallas
# ---------------------------------------------------------------------------
USER_LAT = 32.7780
USER_LON = -96.8080
SEARCH_RADIUS = 600  # meters

# ---------------------------------------------------------------------------
# Sample interest profiles
# ---------------------------------------------------------------------------
PROFILES = {
    "History Buff": {
        "history": 0.9,
        "architecture": 0.5,
        "art": 0.3,
        "nature": 0.1,
    },
    "Architecture Fan": {
        "architecture": 0.95,
        "history": 0.4,
        "art": 0.6,
        "nature": 0.2,
    },
    "Art Lover": {
        "art": 0.9,
        "performing_arts": 0.7,
        "history": 0.3,
        "architecture": 0.4,
    },
    "Nature Enthusiast": {
        "nature": 0.9,
        "photography": 0.6,
        "history": 0.2,
        "art": 0.2,
    },
}

# ---------------------------------------------------------------------------
# Mock POIs — ranging from richly tagged to sparse
# ---------------------------------------------------------------------------
POIS = [
    {
        "name": "Dealey Plaza",
        "lat": 32.7780,
        "lon": -96.8083,
        "poi_type": "historic",
        "tags": {
            "historic": "yes",
            "description": "Site of JFK assassination, National Historic Landmark",
            "wikipedia": "en:Dealey Plaza",
            "wikidata": "Q1191478",
            "website": "https://jfk.org",
            "heritage": "National Historic Landmark",
            "start_date": "1936",
        },
        "wiki_content_length": 46542,
    },
    {
        "name": "Reunion Tower",
        "lat": 32.7756,
        "lon": -96.8089,
        "poi_type": "tourism",
        "tags": {
            "tourism": "attraction",
            "architect": "Welton Becket & Associates",
            "website": "https://reuniontower.com",
            "wikidata": "Q1191477",
        },
        "wiki_content_length": 13147,
    },
    {
        "name": "Cathedral Shrine of the Virgin of Guadalupe",
        "lat": 32.7823,
        "lon": -96.8011,
        "poi_type": "building",
        "tags": {
            "building": "cathedral",
            "amenity": "place_of_worship",
            "description": "Historic Catholic cathedral in downtown Dallas",
            "wikipedia": "en:Cathedral Shrine of the Virgin of Guadalupe",
            "website": "https://cathedralguadalupe.org",
        },
        "wiki_content_length": 8200,
    },
    {
        "name": "Nasher Sculpture Center",
        "lat": 32.7897,
        "lon": -96.7998,
        "poi_type": "tourism",
        "tags": {
            "tourism": "artwork",
            "architect": "Renzo Piano",
            "website": "https://nashersculpturecenter.org",
            "wikidata": "Q3338471",
            "image": "nasher.jpg",
        },
        "wiki_content_length": 9500,
    },
    {
        "name": "Some Random Bench",
        "lat": 32.7781,
        "lon": -96.8079,
        "poi_type": "tourism",
        "tags": {
            "tourism": "attraction",
        },
        "wiki_content_length": 0,
    },
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
            widths[i] = max(widths[i], min(len(str(cell)), 45))
    print_separator(widths, "-")
    print_row(headers, widths)
    print_separator(widths, "=")
    for row in rows:
        print_row(row, widths)
        print_separator(widths, "-")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'='*70}")
    print(f"  POI Significance Scoring Test")
    print(f"  User @ ({USER_LAT}, {USER_LON})  |  Search radius: {SEARCH_RADIUS}m")
    print(f"{'='*70}\n")

    for profile_name, profile in PROFILES.items():
        print(f"\n  Profile: {profile_name}")
        print(f"  Interests: {profile}\n")

        rows = []
        results = []

        for poi in POIS:
            try:
                score = score_poi(
                    poi=poi,
                    interest_profile=profile,
                    user_lat=USER_LAT,
                    user_lon=USER_LON,
                    search_radius=SEARCH_RADIUS,
                    wiki_content_length=poi["wiki_content_length"],
                )
                results.append((score, poi["name"]))
                rows.append([poi["name"], poi["poi_type"], poi["wiki_content_length"], f"{score:.4f}"])
            except ValueError as e:
                rows.append([poi["name"], poi["poi_type"], "-", f"ERROR: {e}"])

        # Sort by score descending
        rows.sort(key=lambda r: r[3], reverse=True)

        headers = ["POI Name", "Type", "Wiki Length", "Score"]
        print_table(headers, rows)

        top = max(results, key=lambda x: x[0])
        print(f"\n  Top Pick for {profile_name}: {top[1]} (score={top[0]:.4f})\n")


main()
