"""
tests/test_visibility_accuracy.py

Synthetic benchmark: 60+ scenarios covering all visibility rules, edge cases,
and feature interactions. No external API calls — all ground truth is hand-labelled.

Sections
--------
A  Core distance thresholds   (very_large / large / medium / small)
B  Field-of-view rules        (in-FOV vs behind vs peripheral)
C  Proximity overrides        (< 30m and < 50m early-exit)
D  Occlusion                  (blocked_by suppresses small/medium)
E  Street matching            (normalisation, abbreviations, missing)
F  Landmark boost             (1.5× distance multiplier)
G  Angle confidence           (central / peripheral / behind)
H  Size fallback              (no-tag default = medium)
I  Real-world locations       (NYC, Dallas, Chicago — known landmarks)
J  Ablation                   (feature removed → expected accuracy drop)

Run:
    python tests/test_visibility_accuracy.py
"""

import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.visibility import filter_visible, _streets_match, _size_category

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def poi(id, name, lat, lon, tags, expected, geometry=None):
    return {
        "id": id, "name": name, "lat": lat, "lon": lon,
        "tags": tags, "geometry": geometry or [], "expected": expected,
    }

def scenario(name, user_lat, user_lon, heading, street, pois):
    return {"name": name, "user_lat": user_lat, "user_lon": user_lon,
            "heading": heading, "street": street, "pois": pois}


# ---------------------------------------------------------------------------
# A — Core distance thresholds
# ---------------------------------------------------------------------------

SECTION_A = [
    scenario("A1: very_large in front <1500m → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a1", "Skyscraper 800m", 40.7527, -73.9967,
            {"building:levels": "50"}, "YES"),
    ]),
    scenario("A2: very_large in front >1500m → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a2", "Skyscraper 2000m", 40.7635, -73.9967,
            {"building:levels": "50"}, "NO"),
    ]),
    scenario("A3: very_large behind <800m → YES",
             40.7455, -73.9967, 180.0, "5th Avenue", [
        poi("a3", "Skyscraper 600m behind", 40.7509, -73.9967,
            {"building:levels": "50"}, "YES"),
    ]),
    scenario("A4: very_large behind >800m → NO",
             40.7455, -73.9967, 180.0, "5th Avenue", [
        poi("a4", "Skyscraper 1000m behind", 40.7545, -73.9967,
            {"building:levels": "50"}, "NO"),
    ]),
    scenario("A5: large in front <600m → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a5", "Large Bldg 400m", 40.7491, -73.9967,
            {"building:levels": "12"}, "YES"),
    ]),
    scenario("A6: large in front >600m → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a6", "Large Bldg 700m", 40.7518, -73.9967,
            {"building:levels": "12"}, "NO"),
    ]),
    scenario("A7: medium in front <250m → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a7", "Theatre 150m", 40.7469, -73.9967,
            {"amenity": "theatre"}, "YES"),
    ]),
    scenario("A8: medium in front >250m → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a8", "Theatre 350m", 40.7487, -73.9967,
            {"amenity": "theatre"}, "NO"),
    ]),
    scenario("A9: small same street <80m → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a9", "Small Shop 50m", 40.7460, -73.9967,
            {"amenity": "cafe", "addr:street": "5th Avenue"}, "YES"),
    ]),
    scenario("A10: small same street >80m → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a10", "Small Shop 120m", 40.7466, -73.9967,
            {"amenity": "cafe", "addr:street": "5th Avenue"}, "NO"),
    ]),
    scenario("A11: small different street → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a11", "Small Shop Cross St 40m", 40.7459, -73.9971,
            {"amenity": "cafe", "addr:street": "West 34th Street"}, "NO"),
    ]),
    scenario("A12: height tag drives very_large → YES at 1200m",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a12", "Tower 150m tall 1200m away", 40.7563, -73.9967,
            {"height": "150"}, "YES"),
    ]),
    scenario("A13: height tag drives medium → NO at 300m",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("a13", "Building 15m tall 300m away", 40.7482, -73.9967,
            {"height": "15"}, "NO"),
    ]),
]

# ---------------------------------------------------------------------------
# B — Field-of-view rules
# ---------------------------------------------------------------------------

SECTION_B = [
    scenario("B1: angle=0° (dead ahead) → YES for medium",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b1", "Museum Dead Ahead 200m", 40.7473, -73.9967,
            {"amenity": "museum"}, "YES"),
    ]),
    scenario("B2: angle≈30° (slight left) → YES for medium",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b2", "Library Slight Left 150m", 40.7468, -73.9981,
            {"amenity": "library"}, "YES"),
    ]),
    scenario("B3: angle≈59° (edge of FOV) → YES for large",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b3", "Large Bldg 59deg 300m", 40.7477, -74.0014,
            {"building:levels": "15"}, "YES"),
    ]),
    scenario("B4: angle≈90° (sideways) → NO for medium",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b4", "Theatre Sideways 150m", 40.7455, -73.9940,
            {"amenity": "theatre"}, "NO"),
    ]),
    scenario("B5: angle≈180° (directly behind) → NO for medium",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b5", "Museum Behind 100m", 40.7446, -73.9967,
            {"amenity": "museum"}, "NO"),
    ]),
    scenario("B6: behind + very_large <800m → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b6", "Skyscraper 500m behind", 40.7410, -73.9967,
            {"building:levels": "60"}, "YES"),
    ]),
    scenario("B7: behind + large → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b7", "Large Bldg 300m behind", 40.7428, -73.9967,
            {"building:levels": "15"}, "NO"),
    ]),
    scenario("B8: peripheral very_large (angle=75°) → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("b8", "Skyscraper 75deg 600m", 40.7469, -73.9898,
            {"building:levels": "45"}, "YES"),
    ]),
]

# ---------------------------------------------------------------------------
# C — Proximity overrides
# ---------------------------------------------------------------------------

SECTION_C = [
    scenario("C1: extremely close <30m in FOV → YES regardless of size",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("c1", "Tiny Kiosk 20m", 40.7457, -73.9967,
            {"amenity": "kiosk"}, "YES"),
    ]),
    scenario("C2: very close <50m in FOV + medium → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("c2", "Church 40m", 40.7459, -73.9967,
            {"building": "church"}, "YES"),
    ]),
    scenario("C3: very close <50m but BEHIND → no proximity override",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("c3", "Shop 30m Behind", 40.7452, -73.9967,
            {"amenity": "shop"}, "NO"),
    ]),
    scenario("C4: close <50m sideways (angle=90°) → no proximity override",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("c4", "Shop 40m Sideways", 40.7455, -73.9961,
            {"amenity": "shop"}, "NO"),
    ]),
]

# ---------------------------------------------------------------------------
# D — Occlusion
# ---------------------------------------------------------------------------

SECTION_D = [
    scenario("D1: small medium blocked by large closer building → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        # Blocker: large building 150m ahead in same direction
        poi("d1_blocker", "Large Blocker 150m", 40.7469, -73.9967,
            {"building:levels": "12"}, "YES"),
        # Target: medium building 300m ahead, same direction, blocked
        poi("d1_target", "Museum 300m Blocked", 40.7482, -73.9967,
            {"amenity": "museum"}, "NO"),
    ]),
    scenario("D2: very_large NOT suppressed by occlusion",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("d2_blocker", "Large Blocker 200m", 40.7473, -73.9967,
            {"building:levels": "12"}, "YES"),
        poi("d2_tower", "Skyscraper 500m", 40.7500, -73.9967,
            {"building:levels": "50"}, "YES"),
    ]),
    scenario("D3: different direction — no occlusion effect",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        # Blocker is 5° left; target is 30° right — >15° apart
        poi("d3_blocker", "Left Blocker 200m", 40.7473, -73.9975,
            {"building:levels": "12"}, "YES"),
        poi("d3_target", "Museum 200m Right", 40.7473, -73.9950,
            {"amenity": "museum"}, "YES"),
    ]),
]

# ---------------------------------------------------------------------------
# E — Street matching
# ---------------------------------------------------------------------------

SECTION_E = [
    scenario("E1: exact match → YES for small nearby",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("e1", "Shop on 5th Avenue 60m", 40.7460, -73.9967,
            {"amenity": "shop", "addr:street": "5th Avenue"}, "YES"),
    ]),
    scenario("E2: abbreviation match St vs Street → YES",
             40.7455, -73.9967, 0.0, "Flora Street", [
        poi("e2", "Shop 60m Flora St", 40.7460, -73.9967,
            {"amenity": "shop", "addr:street": "Flora St"}, "YES"),
    ]),
    scenario("E3: directional prefix stripped N Main St vs Main Street → YES",
             40.7455, -73.9967, 0.0, "Main Street", [
        poi("e3", "Shop 60m N Main St", 40.7460, -73.9967,
            {"amenity": "shop", "addr:street": "N Main St"}, "YES"),
    ]),
    scenario("E4: completely different street → NO",
             40.7455, -73.9967, 0.0, "Broadway", [
        poi("e4", "Shop 60m Park Ave", 40.7460, -73.9967,
            {"amenity": "shop", "addr:street": "Park Avenue"}, "NO"),
    ]),
    scenario("E5: missing addr:street + distance <100m → assume same → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("e5", "Unlabelled Shop 60m", 40.7460, -73.9967,
            {"amenity": "shop"}, "YES"),
    ]),
    scenario("E6: missing addr:street + distance >100m → not assumed → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("e6", "Unlabelled Shop 150m", 40.7469, -73.9967,
            {"amenity": "shop"}, "NO"),
    ]),
    scenario("E7: Ave vs Avenue match → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("e7", "Shop on 5th Ave 60m", 40.7460, -73.9967,
            {"amenity": "shop", "addr:street": "5th Ave"}, "YES"),
    ]),
    # Cross-street suppression — medium POIs on a confirmed different street
    scenario("E8: medium artwork on different street 150m → NO (cross-street suppressed)",
             40.7455, -73.9967, 0.0, "Canton Street", [
        poi("e8", "Panda Sculpture", 40.7469, -73.9967,
            {"tourism": "artwork", "addr:street": "Main Street"}, "NO"),
    ]),
    scenario("E9: medium attraction on different street 200m → NO regardless of category",
             40.7455, -73.9967, 0.0, "Canton Street", [
        poi("e9", "Public Fountain", 40.7473, -73.9967,
            {"amenity": "fountain", "addr:street": "Park Avenue"}, "NO"),
    ]),
    scenario("E10: medium POI no addr:street 150m → YES (street unknown, no penalty)",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("e10", "Unlabelled Artwork 150m", 40.7469, -73.9967,
            {"tourism": "artwork"}, "YES"),
    ]),
    scenario("E11: very_large on different street → YES (tall enough to see over buildings)",
             40.7455, -73.9967, 0.0, "Canton Street", [
        poi("e11", "Skyscraper Different Street", 40.7469, -73.9967,
            {"building:levels": "40", "addr:street": "Main Street"}, "YES"),
    ]),
]

# ---------------------------------------------------------------------------
# F — Landmark boost (1.5× distance multiplier)
# ---------------------------------------------------------------------------

SECTION_F = [
    # monument at 350m → medium × 1.5 = 375m threshold → YES
    scenario("F1: monument 350m — landmark boost → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("f1", "War Memorial 350m", 40.7487, -73.9967,
            {"historic": "monument"}, "YES"),
    ]),
    # cathedral at 550m → large × 1.5 = 900m threshold → YES
    scenario("F2: cathedral 550m — landmark boost large → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("f2", "Cathedral 550m", 40.7505, -73.9967,
            {"building": "cathedral"}, "YES"),
    ]),
    # non-landmark theatre at 300m → medium threshold 250m → NO
    scenario("F3: theatre 300m — no landmark boost → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("f3", "Theatre 300m no boost", 40.7482, -73.9967,
            {"amenity": "theatre"}, "NO"),
    ]),
]

# ---------------------------------------------------------------------------
# G — Angle confidence (doesn't change YES/NO, but tests edge angles)
# ---------------------------------------------------------------------------

SECTION_G = [
    scenario("G1: angle=10° central vision — high confidence YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("g1", "Museum 10deg 150m", 40.7468, -73.9970,
            {"amenity": "museum"}, "YES"),
    ]),
    scenario("G2: angle=45° peripheral — YES but lower confidence",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("g2", "Museum 45deg 150m", 40.7468, -73.9945,
            {"amenity": "museum"}, "YES"),
    ]),
    scenario("G3: angle=58° edge of FOV — YES for medium",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("g3", "Museum 58deg 150m", 40.7462, -73.9952,
            {"amenity": "museum"}, "YES"),
    ]),
    scenario("G4: angle=62° just outside FOV — NO for medium",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("g4", "Museum 62deg 150m", 40.7467, -73.9922,
            {"amenity": "museum"}, "NO"),
    ]),
]

# ---------------------------------------------------------------------------
# H — Size fallback (no-tag → medium, not small)
# ---------------------------------------------------------------------------

SECTION_H = [
    scenario("H1: no tags at all 150m ahead → medium default → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("h1", "Unknown Building 150m", 40.7469, -73.9967,
            {}, "YES"),
    ]),
    scenario("H2: no tags 350m ahead → medium default but too far → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("h2", "Unknown Building 350m", 40.7487, -73.9967,
            {}, "NO"),
    ]),
    scenario("H3: no tags behind 100m → medium but behind → NO",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("h3", "Unknown Building Behind 100m", 40.7446, -73.9967,
            {}, "NO"),
    ]),
    scenario("H4: building=office tag (generic) 150m → medium → YES",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("h4", "Office Building 150m", 40.7469, -73.9967,
            {"building": "office"}, "YES"),
    ]),
]

# ---------------------------------------------------------------------------
# I — Real-world locations
# ---------------------------------------------------------------------------

SECTION_I = [
    scenario("I1: NYC — Broadway facing north toward WTC",
             40.7074, -74.0113, 0.0, "Broadway", [
        poi("i1a", "One World Trade Center",
            40.7127, -74.0134,
            {"tourism": "attraction", "height": "541", "building:levels": "104"}, "YES"),
        poi("i1b", "Woolworth Building",
            40.7128, -74.0080,
            {"building": "office", "height": "241"}, "YES"),
        poi("i1c", "Small Cafe Behind",
            40.7050, -74.0113,
            {"amenity": "cafe"}, "NO"),
    ]),
    scenario("I2: NYC — 34th St facing north toward Empire State",
             40.7455, -73.9967, 0.0, "5th Avenue", [
        poi("i2a", "Empire State Building",
            40.7484, -73.9967,
            {"tourism": "attraction", "height": "443", "building:levels": "102"}, "YES"),
        poi("i2b", "Small Shop Behind",
            40.7420, -73.9967,
            {"amenity": "shop"}, "NO"),
        poi("i2c", "Small Office Different Street",
            40.7455, -74.0010,
            {"building": "office"}, "NO"),
    ]),
    scenario("I3: NYC — Times Square facing east toward Chrysler",
             40.7580, -73.9855, 90.0, "42nd Street", [
        poi("i3a", "Chrysler Building",
            40.7516, -73.9755,
            {"building": "office", "height": "319"}, "YES"),
        poi("i3b", "Small Theater Behind",
            40.7580, -73.9950,
            {"amenity": "theatre", "building:levels": "2"}, "NO"),
        poi("i3c", "30 Rockefeller Plaza",
            40.7587, -73.9787,
            {"building": "office", "height": "259", "building:levels": "70"}, "YES"),
    ]),
    scenario("I4: Chicago — Millennium Park facing SW toward Willis Tower",
             41.8827, -87.6233, 225.0, "Michigan Avenue", [
        poi("i4a", "Willis Tower",
            41.8789, -87.6359,
            {"building": "office", "height": "442", "building:levels": "108"}, "YES"),
        poi("i4b", "John Hancock Center",
            41.8988, -87.6236,
            {"building": "office", "height": "344", "building:levels": "100"}, "NO"),
        poi("i4c", "Small Cafe Behind Park",
            41.8790, -87.6233,
            {"amenity": "cafe"}, "NO"),
    ]),
    scenario("I5: Dallas Arts District — Flora St facing southwest",
             32.7895, -96.7971, 225.0, "Flora Street", [
        poi("i5a", "Winspear Opera House",
            32.7879, -96.7981,
            {"amenity": "theatre", "building:levels": "6"}, "YES"),
        poi("i5b", "Wyly Theatre",
            32.7876, -96.7983,
            {"amenity": "theatre", "building:levels": "12"}, "YES"),
        poi("i5c", "Small Shop Far Behind",
            32.7930, -96.7960,
            {"amenity": "shop"}, "NO"),
    ]),
    scenario("I6: SF — Civic Center facing north toward City Hall",
             37.7771, -122.4193, 0.0, "Market Street", [
        poi("i6a", "San Francisco City Hall",
            37.7793, -122.4193,
            {"amenity": "townhall", "building:levels": "5"}, "YES"),
        poi("i6b", "Small Bar Behind",
            37.7750, -122.4193,
            {"amenity": "bar"}, "NO"),
    ]),
    scenario("I7: Boston — Freedom Trail 400m south of Bunker Hill facing N",
             42.3726, -71.0607, 0.0, "State Street", [
        poi("i7a", "Bunker Hill Monument",
            42.3762, -71.0607,
            {"historic": "monument", "height": "67"}, "YES"),
        poi("i7b", "Small Souvenir Shop",
            42.3590, -71.0589,
            {"amenity": "shop"}, "NO"),
    ]),
    scenario("I8: Washington DC — Mall 800m east of Lincoln Memorial facing W",
             38.8893, -77.0410, 270.0, "Constitution Avenue", [
        poi("i8a", "Lincoln Memorial",
            38.8893, -77.0502,
            {"tourism": "attraction", "historic": "memorial", "height": "30"}, "YES"),
        poi("i8b", "Washington Monument",
            38.8895, -77.0353,
            {"tourism": "attraction", "man_made": "tower", "height": "169"}, "YES"),
        poi("i8c", "Small Food Truck",
            38.8921, -77.0340,
            {"amenity": "food_court"}, "NO"),
    ]),
]

# ---------------------------------------------------------------------------
# J — Downtown Dallas specific locations
# ---------------------------------------------------------------------------

SECTION_J = [
    scenario("J1: Dallas — Main Street facing east toward Pegasus Sign",
             32.7807, -96.8015, 90.0, "Main Street", [
        poi("j1a", "Pegasus Sign Magnolia Building",
            32.7807, -96.7987,
            {"tourism": "attraction", "building:levels": "29", "height": "131"}, "YES"),
        poi("j1b", "Adolphus Hotel",
            32.7799, -96.7995,
            {"tourism": "hotel", "building:levels": "22", "historic": "yes"}, "YES"),
        poi("j1c", "Small Coffee Shop Behind",
            32.7807, -96.8040,
            {"amenity": "cafe"}, "NO"),
        poi("j1d", "Bank of America Plaza",
            32.7812, -96.7958,
            {"building": "office", "height": "280", "building:levels": "72"}, "YES"),
    ]),
    
    scenario("J2: Dallas — Elm Street facing west toward Dealey Plaza",
             32.7790, -96.8060, 270.0, "Elm Street", [
        poi("j2a", "Dealey Plaza",
            32.7790, -96.8083,
            {"tourism": "attraction", "historic": "yes"}, "YES"),
        poi("j2b", "Sixth Floor Museum",
            32.7798, -96.8083,
            {"tourism": "museum", "building:levels": "7"}, "YES"),
        poi("j2c", "Old Red Courthouse",
            32.7786, -96.8079,
            {"tourism": "museum", "historic": "yes", "building:levels": "5"}, "YES"),
        poi("j2d", "Small Souvenir Shop Behind",
            32.7790, -96.8035,
            {"amenity": "shop"}, "NO"),
    ]),
    
    scenario("J3: Dallas — Commerce Street facing SE toward Farmers Market",
             32.7805, -96.7998, 135.0, "Commerce Street", [
        poi("j3a", "Dallas Farmers Market",
            32.7754, -96.7907,
            {"amenity": "marketplace", "building": "retail"}, "NO"),  # >250m away
        poi("j3b", "Comerica Bank Tower",
            32.7815, -96.7968,
            {"building": "office", "height": "222", "building:levels": "60"}, "YES"),
        poi("j3c", "Small Deli",
            32.7799, -96.7985,
            {"amenity": "restaurant"}, "NO"),   # 138m, restaurant=small needs <80m
        poi("j3d", "Thanks-Giving Square",
            32.7816, -96.7976,
            {"leisure": "park", "tourism": "attraction"}, "NO"),  # 239m at 75.7° — outside 60° FOV
    ]),
    
    scenario("J4: Dallas — Reunion Tower area facing north toward skyline",
             32.7755, -96.8038, 0.0, "Reunion Boulevard", [
        poi("j4a", "Reunion Tower",
            32.7755, -96.8038,
            {"tourism": "attraction", "man_made": "tower", "height": "171"}, "YES"),
        poi("j4b", "Hyatt Regency Dallas",
            32.7761, -96.8041,
            {"tourism": "hotel", "building:levels": "28"}, "YES"),
        poi("j4c", "Union Station",
            32.7760, -96.8062,
            {"public_transport": "station", "historic": "yes"}, "NO"),  # 231m at 76.1° — sideways, outside FOV
        poi("j4d", "Skyline Skyscrapers 800m North",
            32.7820, -96.7980,
            {"building:levels": "50"}, "YES"),  # very_large behind? Let's verify heading 0°
        poi("j4e", "Small Parking Booth",
            32.7770, -96.8038,
            {"amenity": "parking"}, "NO"),  # behind relative to heading
    ]),
    
    scenario("J5: Dallas — Deep Ellum facing east from Good-Latimer",
             32.7840, -96.7860, 90.0, "Elm Street", [
        poi("j5a", "Deep Ellum Distillery",
            32.7840, -96.7825,
            {"tourism": "attraction", "amenity": "bar"}, "NO"),   # amenity=bar → small, 327m > 80m
        poi("j5b", "Bomb Factory Venue",
            32.7835, -96.7818,
            {"amenity": "theatre", "building:levels": "3"}, "NO"),  # medium theatre, 396m > 250m
        poi("j5c", "Small Mural Wall",
            32.7840, -96.7840,
            {"tourism": "artwork"}, "YES"),  # 186m ahead, artwork → medium default, <250m
        poi("j5d", "Klyde Warren Park",
            32.7885, -96.8015,
            {"leisure": "park", "tourism": "attraction"}, "NO"),  # far, different direction
    ]),
    
    scenario("J6: Dallas — Klyde Warren Park facing east",
             32.7890, -96.8010, 90.0, "Woodall Rodgers Freeway", [
        poi("j6a", "Dallas Museum of Art",
            32.7878, -96.8008,
            {"tourism": "museum", "building:levels": "4"}, "NO"),  # behind
        poi("j6b", "Perot Museum of Nature and Science",
            32.7867, -96.8067,
            {"tourism": "museum", "building:levels": "5"}, "NO"),  # behind + different angle
        poi("j6c", "Nasher Sculpture Center",
            32.7880, -96.7998,
            {"tourism": "museum", "building:levels": "2"}, "YES"),
        poi("j6d", "Small Food Truck East",
            32.7890, -96.7995,
            {"amenity": "food_court"}, "NO"),   # food_court → small, 140m > 80m
        poi("j6e", "Trammell Crow Center",
            32.7885, -96.7970,
            {"building": "office", "height": "209", "building:levels": "50"}, "YES"),
    ]),
    
    scenario("J7: Dallas — American Airlines Center area facing south",
             32.7905, -96.8100, 180.0, "Victory Avenue", [
        poi("j7a", "American Airlines Center",
            32.7905, -96.8103,
            {"amenity": "stadium"}, "YES"),  # stadium → very_large; no building:levels (5 floors would override to medium)
        poi("j7b", "W Dallas Victory Hotel",
            32.7895, -96.8098,
            {"tourism": "hotel", "building:levels": "33"}, "YES"),
        poi("j7c", "Perot Museum North View",
            32.7870, -96.8070,
            {"tourism": "museum", "height": "43"}, "YES"),  # 43m tall → large; 480m < 600m large threshold
        poi("j7d", "Small Bar Behind",
            32.7920, -96.8100,
            {"amenity": "bar"}, "NO"),  # behind
    ]),
    
    scenario("J8: Dallas — Arts District facing SW from Ross Avenue",
             32.7875, -96.7985, 225.0, "Ross Avenue", [
        poi("j8a", "Cathedral Shrine of the Virgin of Guadalupe",
            32.7882, -96.8000,
            {"building": "cathedral", "amenity": "place_of_worship"}, "NO"),  # 160m at 74° — outside 60° FOV for SW heading
        poi("j8b", "Moody Performance Hall",
            32.7870, -96.7988,
            {"amenity": "theatre", "building:levels": "4"}, "YES"),
        poi("j8c", "AT&T Performing Arts Center",
            32.7875, -96.7995,
            {"amenity": "theatre", "tourism": "attraction"}, "YES"),
        poi("j8d", "Small Shop Different Street",
            32.7875, -96.7960,
            {"amenity": "shop"}, "NO"),
    ]),
    
    scenario("J9: Dallas — Trinity River levee facing downtown skyline",
             32.7775, -96.8200, 90.0, "Continental Avenue", [
        poi("j9a", "Margaret Hunt Hill Bridge",
            32.7790, -96.8180,
            {"man_made": "bridge", "tourism": "attraction", "height": "131"}, "YES"),  # 131m tower → very_large, 251m in_fov
        poi("j9b", "Downtown Skyline 1.4km",
            32.7790, -96.8050,
            {"building:levels": "70"}, "YES"),  # 1408m < 1500m very_large threshold
        poi("j9c", "Trinity Overlook Park",
            32.7775, -96.8150,
            {"leisure": "park"}, "NO"),  # 467m, park → medium, too far
        poi("j9d", "Small Trail Marker Behind",
            32.7765, -96.8200,
            {"tourism": "information"}, "NO"),
    ]),
    
    scenario("J10: Dallas — Cedars neighborhood facing north toward downtown",
             32.7700, -96.7950, 0.0, "South Ervay Street", [
        poi("j10a", "South Side Ballroom",
             32.7705, -96.7945,
             {"amenity": "theatre", "building:levels": "2"}, "YES"),
        poi("j10b", "Downtown Skyline 1km",
             32.7800, -96.7980,
             {"building:levels": "70"}, "YES"),  # very_large, ahead
        poi("j10c", "Alamo Drafthouse Cinema",
             32.7710, -96.7955,
             {"amenity": "cinema", "building:levels": "3"}, "YES"),
        poi("j10d", "Small Coffee Shop Far",
             32.7800, -96.7950,
             {"amenity": "cafe"}, "NO"),  # medium >250m
    ]),
]
# ---------------------------------------------------------------------------
# All scenarios
# ---------------------------------------------------------------------------


ALL_SCENARIOS = (
    SECTION_A + SECTION_B + SECTION_C + SECTION_D +
    SECTION_E + SECTION_F + SECTION_G + SECTION_H + SECTION_I + SECTION_J
)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict) -> dict:
    pois     = [{k: v for k, v in p.items() if k != "expected"} for p in scenario["pois"]]
    expected = {str(p["id"]): p["expected"] for p in scenario["pois"]}

    t0 = time.perf_counter()
    visible, rejected = filter_visible(
        pois,
        scenario["user_lat"],
        scenario["user_lon"],
        scenario["heading"],
        scenario["street"],
    )
    elapsed = time.perf_counter() - t0

    actual: dict[str, str] = {}
    for p in visible:
        actual[str(p["id"])] = "YES"
    for p in rejected:
        actual[str(p["id"])] = "NO"

    details = []
    for p in scenario["pois"]:
        pid  = str(p["id"])
        exp  = expected[pid]
        act  = actual.get(pid, "?")
        # Get confidence from result
        conf = next(
            (r.get("confidence", "?") for r in visible + rejected if str(r["id"]) == pid),
            "?",
        )
        details.append({"name": p["name"], "expected": exp, "actual": act,
                         "ok": act == exp, "confidence": conf})

    correct = sum(1 for d in details if d["ok"])
    return {"scenario": scenario["name"], "correct": correct,
            "total": len(details), "elapsed": elapsed, "details": details}


def run_sections(scenarios: list[dict], label: str) -> tuple[int, int, float]:
    total_correct = total_pois = 0
    total_time    = 0.0
    failures      = []

    for s in scenarios:
        r = run_scenario(s)
        total_correct += r["correct"]
        total_pois    += r["total"]
        total_time    += r["elapsed"]
        if r["correct"] < r["total"]:
            for d in r["details"]:
                if not d["ok"]:
                    failures.append((r["scenario"], d["name"], d["expected"], d["actual"]))

    acc  = total_correct / total_pois * 100 if total_pois else 0
    mark = "✓" if not failures else "✗"
    print(f"  {mark} {label:<50} {total_correct:>3}/{total_pois}  ({acc:5.1f}%)")
    for scenario_name, poi_name, exp, act in failures:
        print(f"       ✗ [{scenario_name}] {poi_name}: expected {exp}, got {act}")

    return total_correct, total_pois, total_time


# ---------------------------------------------------------------------------
# Ablation tests — disable one feature at a time, measure accuracy drop
# ---------------------------------------------------------------------------

def ablation_no_proximity(pois, user_lat, user_lon, heading, street):
    """Monkey-patch: skip proximity overrides."""
    import utils.visibility as v
    orig = v._is_visible
    def patched(size, distance_m, angle_deg, same_street, blocked_by, poi_type, aspect_conf=1.0, cross_street=False):
        # Skip proximity block — jump straight to distance rules
        in_fov     = angle_deg < 60
        from utils.visibility import _angle_confidence, _is_landmark
        angle_conf = _angle_confidence(angle_deg)
        dist_mult  = 1.5 if _is_landmark(poi_type) else 1.0
        if size == "very_large":
            visible = distance_m < 1500 * dist_mult if in_fov else distance_m < 800 * dist_mult
            conf    = (0.95 if in_fov else 0.75) * angle_conf if visible else 0.9
            return (visible, round(conf * aspect_conf, 2))
        if size == "large":
            visible = in_fov and distance_m < 600 * dist_mult
            return (visible, round(0.85 * angle_conf * aspect_conf, 2))
        if size == "medium":
            visible = in_fov and distance_m < 250 * dist_mult
            return (visible, round(0.80 * angle_conf * aspect_conf, 2))
        visible = in_fov and distance_m < 80 and same_street
        return (visible, round(0.75 * angle_conf * aspect_conf, 2))
    v._is_visible = patched
    result = filter_visible(pois, user_lat, user_lon, heading, street)
    v._is_visible = orig
    return result


def ablation_no_occlusion(pois, user_lat, user_lon, heading, street):
    """Monkey-patch: skip occlusion suppression."""
    import utils.visibility as v
    orig = v._add_occlusion_hints
    def patched(ps):
        for p in ps:
            p["blocked_by"] = []
        return ps
    v._add_occlusion_hints = patched
    result = filter_visible(pois, user_lat, user_lon, heading, street)
    v._add_occlusion_hints = orig
    return result


def ablation_small_default(pois, user_lat, user_lon, heading, street):
    """Monkey-patch: revert size default to 'small'."""
    import utils.visibility as v
    orig = v._size_category
    def patched(tags, geometry):
        result = orig(tags, geometry)
        return "small" if result == "medium" and not tags else result
    v._size_category = patched
    result = filter_visible(pois, user_lat, user_lon, heading, street)
    v._size_category = orig
    return result


def run_ablation(name: str, ablation_fn, scenarios: list[dict]) -> tuple[int, int]:
    correct = total = 0
    for s in scenarios:
        pois     = [{k: v for k, v in p.items() if k != "expected"} for p in s["pois"]]
        expected = {str(p["id"]): p["expected"] for p in s["pois"]}
        visible, rejected = ablation_fn(
            pois, s["user_lat"], s["user_lon"], s["heading"], s["street"]
        )
        actual = {str(p["id"]): "YES" for p in visible}
        actual.update({str(p["id"]): "NO" for p in rejected})
        for pid, exp in expected.items():
            total   += 1
            correct += (actual.get(pid) == exp)
    acc = correct / total * 100 if total else 0
    print(f"    {name:<40} {correct:>3}/{total}  ({acc:5.1f}%)")
    return correct, total


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

def run_unit_tests() -> tuple[int, int]:
    passed = total = 0

    def check(label, result, expected):
        nonlocal passed, total
        total += 1
        ok = result == expected
        if ok:
            passed += 1
        else:
            print(f"    FAIL [{label}]: got {result!r}, expected {expected!r}")

    # Street matching
    check("exact match",            _streets_match("Main Street", "Main Street"),  True)
    check("St vs Street",           _streets_match("Main St", "Main Street"),      True)
    check("Ave vs Avenue",          _streets_match("5th Ave", "5th Avenue"),       True)
    check("directional stripped",   _streets_match("N Main St", "Main Street"),    True)
    check("partial contains",       _streets_match("Main", "Main Street"),         True)
    check("different streets",      _streets_match("Broadway", "Park Avenue"),     False)
    check("empty vs something",     _streets_match("", "Broadway"),                False)
    check("Flora St vs Flora Street", _streets_match("Flora St", "Flora Street"),  True)
    check("Blvd vs Boulevard",      _streets_match("Oak Blvd", "Oak Boulevard"),   True)

    # Size category
    check("50 floors → very_large",  _size_category({"building:levels": "50"}, []), "very_large")
    check("12 floors → large",       _size_category({"building:levels": "12"}, []), "large")
    check("5 floors → medium",       _size_category({"building:levels": "5"},  []), "medium")
    check("height 200m → very_large",_size_category({"height": "200"}, []),         "very_large")
    check("height 50m → large",      _size_category({"height": "50"},  []),         "large")
    check("height 15m → medium",     _size_category({"height": "15"},  []),         "medium")
    check("theatre tag → medium",    _size_category({"amenity": "theatre"}, []),    "medium")
    check("stadium tag → very_large",_size_category({"amenity": "stadium"}, []),    "very_large")
    check("no data → medium",        _size_category({}, []),                        "medium")
    check("attraction → medium",     _size_category({"tourism": "attraction"}, []), "medium")

    return passed, total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\n" + "═" * 70)
    print("  TourAI Visibility Benchmark")
    print("═" * 70)
    print(f"  Scenarios : {len(ALL_SCENARIOS)}")
    print(f"  Total POIs: {sum(len(s['pois']) for s in ALL_SCENARIOS)}")

    # ── Unit tests ────────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  Unit Tests")
    print(f"{'─'*70}")
    u_pass, u_total = run_unit_tests()
    print(f"  Result: {u_pass}/{u_total} passed")

    # ── Section results ───────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  Scenario Sections")
    print(f"{'─'*70}")

    sections = [
        ("A  Core distance thresholds   ", SECTION_A),
        ("B  Field-of-view rules        ", SECTION_B),
        ("C  Proximity overrides        ", SECTION_C),
        ("D  Occlusion                  ", SECTION_D),
        ("E  Street matching            ", SECTION_E),
        ("F  Landmark boost             ", SECTION_F),
        ("G  Angle confidence           ", SECTION_G),
        ("H  Size fallback              ", SECTION_H),
        ("I  Real-world locations       ", SECTION_I),
        ("J  Downtown Dallas locations  ", SECTION_J),
    ]

    grand_correct = grand_total = 0
    for label, sec in sections:
        c, t, _ = run_sections(sec, label)
        grand_correct += c
        grand_total   += t

    grand_acc = grand_correct / grand_total * 100
    print(f"\n  OVERALL: {grand_correct}/{grand_total}  ({grand_acc:.1f}%)")

    # ── Ablation tests ────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  Ablation Tests  (feature removed → accuracy should drop)")
    print(f"{'─'*70}")
    print(f"  Baseline: {grand_correct}/{grand_total} ({grand_acc:.1f}%)")
    print()

    baseline_correct = grand_correct

    c, t = run_ablation("Without proximity overrides", ablation_no_proximity, SECTION_C)
    drop = baseline_correct / grand_total * 100 - c / t * 100
    print(f"      → Section C accuracy delta: {drop:+.1f}%")

    c, t = run_ablation("Without occlusion suppression", ablation_no_occlusion, SECTION_D)
    drop = (grand_correct / grand_total - c / t) * 100
    print(f"      → Section D accuracy delta: {drop:+.1f}%")

    c, t = run_ablation("With 'small' default (reverted)", ablation_small_default, SECTION_H)
    drop = (grand_correct / grand_total - c / t) * 100
    print(f"      → Section H accuracy delta: {drop:+.1f}%")

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    mark = "✓ PASS" if grand_acc >= 90 else "✗ FAIL"
    print(f"  {mark}  {grand_correct}/{grand_total} correct  ({grand_acc:.1f}%)  target ≥ 90%")
    print("═" * 70)
    print()


if __name__ == "__main__":
    main()
