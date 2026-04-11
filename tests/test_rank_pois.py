# tests/test_rank_pois.py
#
# Unit tests for the rank_pois tool.
# Verifies proximity-first tier ranking and significance scoring.
# No external API calls — all POI data is hardcoded.

import sys
import os
import json
import re
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import rank_pois, _haversine_meters

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

USER_LAT = 32.7792
USER_LON = -96.8075

USER_INTERESTS = json.dumps({
    "history":      0.9,
    "architecture": 0.8,
    "photography":  0.7,
    "food":         0.5,
    "art":          0.4,
    "nature":       0.3,
})

# 6 POIs designed to exercise every tier and scoring dimension.
# Coordinates are offset from the user position to produce the target distances.
# At lat ~32.78°: 1m ≈ 0.00000899° lat, 1m ≈ 0.00001071° lon
POIS = [
    {
        # POI A — IMMEDIATE (15m north). Medium tag richness, has wikipedia.
        # Expected: IMMEDIATE tier, significance ≈ 0.71
        "id": "poi_a",
        "name": "JFK Memorial",
        "lat": 32.77934,   # +15m north
        "lon": -96.8075,
        "tags": {
            "historic":  "memorial",
            "name":      "John F. Kennedy Memorial",
            "wikipedia": "en:John F. Kennedy Memorial (Dallas)",
        },
    },
    {
        # POI B — NEAR (104m east). Highest tag richness, strong interest match.
        # Expected: NEAR tier, significance ≈ 0.89
        "id": "poi_b",
        "name": "The Sixth Floor Museum",
        "lat": 32.7792,
        "lon": -96.80639,  # +104m east
        "tags": {
            "tourism":     "museum",
            "name":        "The Sixth Floor Museum at Dealey Plaza",
            "wikipedia":   "en:Sixth Floor Museum",
            "wikidata":    "Q2388937",
            "website":     "https://www.jfk.org",
            "description": "Museum about the assassination of President Kennedy",
        },
    },
    {
        # POI C — NEAR (85m south). High tag richness, architecture match.
        # Expected: NEAR tier, significance ≈ 0.83
        "id": "poi_c",
        "name": "Old Red Courthouse",
        "lat": 32.77844,   # -85m south
        "lon": -96.8075,
        "tags": {
            "historic":   "building",
            "name":       "Old Red Museum",
            "architect":  "M.A. Orlopp",
            "start_date": "1892",
            "wikipedia":  "en:Old Red Museum",
        },
    },
    {
        # POI D — IMMEDIATE (40m north). Two wiki tags, historic.
        # Expected: IMMEDIATE tier, significance ≈ 0.77 (ranks above JFK Memorial)
        "id": "poi_d",
        "name": "Dealey Plaza",
        "lat": 32.77956,   # +40m north
        "lon": -96.8075,
        "tags": {
            "historic":  "memorial",
            "name":      "Dealey Plaza",
            "wikipedia": "en:Dealey Plaza",
            "wikidata":  "Q731635",
        },
    },
    {
        # POI E — IMMEDIATE (30m east). No tourism/historic tags, no wikipedia.
        # Expected: significance ≈ 0.10 — below minimum threshold, should be filtered out
        "id": "poi_e",
        "name": "Main Street Parking Garage",
        "lat": 32.7792,
        "lon": -96.80718,  # +30m east
        "tags": {
            "building": "parking",
            "name":     "Main Street Garage",
        },
    },
    {
        # POI F — FAR (450m north). Very high tag richness, but too far away.
        # Expected: FAR tier, significance ≈ 0.89 — ranks LAST despite high significance
        "id": "poi_f",
        "name": "Dallas Museum of Art",
        "lat": 32.78325,   # +450m north
        "lon": -96.8075,
        "tags": {
            "tourism":     "museum",
            "name":        "Dallas Museum of Art",
            "wikipedia":   "en:Dallas Museum of Art",
            "wikidata":    "Q1353957",
            "website":     "https://dma.org",
            "description": "Major encyclopedic art museum in Dallas",
        },
    },
]

POIS_JSON = json.dumps(POIS)

# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def parse_output(output: str) -> tuple[list[dict], list[str]]:
    """Parse rank_pois string output into structured data.

    Returns:
        ranked  — list of dicts: {rank, name, tier, significance, distance_m}
        dropped — list of names that were filtered out
    """
    ranked: list[dict] = []
    dropped: list[str] = []
    current_tier = None

    for line in output.splitlines():
        line = line.strip()

        # Tier header lines
        if line.startswith("IMMEDIATE"):
            current_tier = "IMMEDIATE"
            continue
        if line.startswith("NEAR"):
            current_tier = "NEAR"
            continue
        if line.startswith("FAR"):
            current_tier = "FAR"
            continue

        # Filtered-out line: "(Filtered out — below significance threshold: X, Y)"
        if line.startswith("(Filtered out"):
            names_part = re.sub(r"\(Filtered out[^:]*:\s*", "", line).rstrip(")")
            dropped = [n.strip() for n in names_part.split(",") if n.strip()]
            continue

        # Ranked POI line: "1. JFK Memorial — Significance: 0.71 (...) — 15m away"
        m = re.match(r"^(\d+)\.\s+(.+?)\s+—\s+Significance:\s+([\d.]+).*?(\d+)m away", line)
        if m:
            ranked.append({
                "rank":         int(m.group(1)),
                "name":         m.group(2).strip(),
                "tier":         current_tier,
                "significance": float(m.group(3)),
                "distance_m":   int(m.group(4)),
            })

    return ranked, dropped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poi_actual_distance(poi: dict) -> float:
    return _haversine_meters(USER_LAT, USER_LON, poi["lat"], poi["lon"])


def _find(ranked: list[dict], name: str) -> dict | None:
    for r in ranked:
        if r["name"] == name:
            return r
    return None


def _assert(condition: bool, label: str, expected: str, actual: str) -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    else:
        print(f"  FAIL  {label}")
        print(f"          Expected : {expected}")
        print(f"          Actual   : {actual}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Sanity-check that our hardcoded coordinates produce the intended distances
    print("Coordinate verification (haversine):")
    for poi in POIS:
        d = _poi_actual_distance(poi)
        print(f"  {poi['name']:<28} {d:.1f}m")

    print()

    # Run rank_pois
    output = rank_pois.invoke({
        "pois_json":           POIS_JSON,
        "user_interests_json": USER_INTERESTS,
        "user_lat":            USER_LAT,
        "user_lon":            USER_LON,
    })

    print("rank_pois output:\n")
    print(output)
    print()

    ranked, dropped = parse_output(output)

    # Expected scores (pre-computed, see module docstring)
    # JFK Memorial   : richness=0.2, interest=0.9(history), wiki=1.0 → 0.71
    # Dealey Plaza   : richness=0.4, interest=0.9(history), wiki=1.0 → 0.77
    # Parking Garage : richness=0.0, interest=0.2(no match), wiki=0.0 → 0.10 (filtered)
    # Sixth Floor    : richness=0.8, interest=0.9(history), wiki=1.0 → 0.89
    # Old Red        : richness=0.6, interest=0.9(history), wiki=1.0 → 0.83
    # DMA            : richness=0.8, interest=0.9(history), wiki=1.0 → 0.89

    results = []
    print("=" * 55)
    print("  ASSERTIONS")
    print("=" * 55)

    # TEST 1 — IMMEDIATE tier comes first
    first = ranked[0] if ranked else None
    results.append(_assert(
        first is not None and first["tier"] == "IMMEDIATE",
        "TEST 1 — IMMEDIATE tier POI is ranked #1",
        "first ranked POI tier == IMMEDIATE",
        f"first ranked POI: {first}",
    ))

    # TEST 2 — Significance breaks ties within IMMEDIATE: Dealey > JFK > Garage
    dealey = _find(ranked, "Dealey Plaza")
    jfk    = _find(ranked, "JFK Memorial")
    garage = _find(ranked, "Main Street Parking Garage")

    # Garage should be filtered out entirely
    garage_absent = garage is None and "Main Street Parking Garage" in dropped
    results.append(_assert(
        garage_absent,
        "TEST 2 — Parking garage filtered out (significance below threshold)",
        "Main Street Parking Garage absent from ranked list and present in filtered-out list",
        f"in ranked={garage is not None}, in dropped={'Main Street Parking Garage' in dropped}",
    ))

    # Within IMMEDIATE, Dealey (0.77) should rank above JFK (0.71)
    if dealey and jfk:
        results.append(_assert(
            dealey["rank"] < jfk["rank"],
            "TEST 2b — Within IMMEDIATE: Dealey Plaza (sig 0.77) ranks above JFK Memorial (sig 0.71)",
            f"Dealey rank < JFK rank",
            f"Dealey rank={dealey['rank']}, JFK rank={jfk['rank']}",
        ))
    else:
        results.append(_assert(
            False, "TEST 2b — Dealey and JFK both present in ranked output",
            "both present", f"Dealey={dealey}, JFK={jfk}",
        ))

    # TEST 3 — NEAR beats FAR regardless of significance
    sixth_floor = _find(ranked, "The Sixth Floor Museum")
    dma         = _find(ranked, "Dallas Museum of Art")

    if sixth_floor and dma:
        results.append(_assert(
            sixth_floor["rank"] < dma["rank"],
            "TEST 3 — NEAR (Sixth Floor, 104m) ranks above FAR (DMA, 450m) despite equal significance",
            f"Sixth Floor rank < DMA rank",
            f"Sixth Floor rank={sixth_floor['rank']}, DMA rank={dma['rank']}",
        ))
    else:
        results.append(_assert(
            False, "TEST 3 — Sixth Floor Museum and Dallas Museum of Art both in results",
            "both present", f"Sixth Floor={sixth_floor}, DMA={dma}",
        ))

    # TEST 4 — High-significance FAR POI does NOT outrank IMMEDIATE POIs
    # DMA (FAR, sig 0.89) must rank below both JFK and Dealey (IMMEDIATE)
    if dma and jfk and dealey:
        results.append(_assert(
            dma["rank"] > jfk["rank"] and dma["rank"] > dealey["rank"],
            "TEST 4 — FAR DMA (sig 0.89) ranks below IMMEDIATE JFK and Dealey",
            "DMA rank > JFK rank AND DMA rank > Dealey rank",
            f"DMA rank={dma['rank']}, JFK rank={jfk['rank']}, Dealey rank={dealey['rank']}",
        ))
    else:
        results.append(_assert(
            False, "TEST 4 — DMA, JFK, and Dealey all present",
            "all three present", f"DMA={dma}, JFK={jfk}, Dealey={dealey}",
        ))

    # TEST 5 — Interest match affects ranking within NEAR tier
    # Sixth Floor (sig 0.89) should rank above Old Red (sig 0.83) within NEAR
    old_red = _find(ranked, "Old Red Courthouse")

    if sixth_floor and old_red:
        same_near_tier = sixth_floor["tier"] == "NEAR" and old_red["tier"] == "NEAR"
        sixth_above_old_red = sixth_floor["rank"] < old_red["rank"]
        results.append(_assert(
            same_near_tier and sixth_above_old_red,
            "TEST 5 — Within NEAR: Sixth Floor (sig 0.89) ranks above Old Red (sig 0.83)",
            "both NEAR, Sixth Floor rank < Old Red rank",
            f"Sixth Floor tier={sixth_floor['tier']} rank={sixth_floor['rank']}, "
            f"Old Red tier={old_red['tier']} rank={old_red['rank']}",
        ))
    else:
        results.append(_assert(
            False, "TEST 5 — Sixth Floor and Old Red both present",
            "both present", f"Sixth Floor={sixth_floor}, Old Red={old_red}",
        ))

    # TEST 6 — Wikipedia notability contributes to significance score
    # JFK Memorial has wikipedia (sig 0.71). Without it: richness=0.2, interest=0.9, wiki=0.0
    # → would be 0.2×0.3 + 0.9×0.5 + 0.0×0.2 = 0.51. With wikipedia: 0.71. Δ = +0.20.
    # Verify JFK's actual reported significance is >= 0.70 (confirming wiki boost applied).
    if jfk:
        results.append(_assert(
            jfk["significance"] >= 0.70,
            "TEST 6 — JFK Memorial significance (with wikipedia tag) >= 0.70",
            ">= 0.70 (wikipedia notability contributes +0.20 vs a no-wiki POI)",
            f"reported significance = {jfk['significance']}",
        ))
    else:
        results.append(_assert(
            False, "TEST 6 — JFK Memorial present in results",
            "present", "not found",
        ))

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    passed = sum(results)
    total  = len(results)

    print()
    print("=" * 55)
    print(f"  RESULTS: {passed}/{total} tests passed")
    print("=" * 55)


main()
