"""
tests/test_visibility_timing.py

Compares CoT + Self-Reflection vs single-pass visibility at one location.
Reports per-pass timings and decision diff.

Run:
    python tests/test_visibility_timing.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from utils.geoapify import search_nearby
from utils.osrm import get_current_street
from utils import visibility

# Dallas Arts District — good mix of large + small buildings
LAT     = 32.7895
LON     = -96.7971
HEADING = 225.0


async def run(label: str, use_cot: bool, pois: list, street: str) -> tuple[list, list, float]:
    # Clear cache so both runs hit the LLM fresh
    visibility._cache.clear()

    t0 = time.perf_counter()
    visible, rejected = await visibility.filter_visible(
        pois, LAT, LON, HEADING, street, use_cot=use_cot
    )
    elapsed = time.perf_counter() - t0

    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"  Total time : {elapsed:.2f}s")
    print(f"  Visible    : {len(visible)}")
    print(f"  Rejected   : {len(rejected)}")
    return visible, rejected, elapsed


async def main():
    print("Fetching POIs and street name…")
    pois, street = await asyncio.gather(
        search_nearby(LAT, LON, radius=200),
        get_current_street(LAT, LON),
    )
    print(f"  Street : {street}")
    print(f"  POIs   : {len(pois)}")

    # ── Standard single-pass ─────────────────────────────────────────────────
    vis_std, rej_std, t_std = await run(
        "Standard (single-pass)", use_cot=False, pois=pois, street=street
    )

    # ── CoT + Self-Reflection ────────────────────────────────────────────────
    vis_cot, rej_cot, t_cot = await run(
        "CoT + Self-Reflection", use_cot=True, pois=pois, street=street
    )

    # ── Comparison ───────────────────────────────────────────────────────────
    std_ids = {str(p["id"]) for p in vis_std}
    cot_ids = {str(p["id"]) for p in vis_cot}

    only_std = std_ids - cot_ids   # standard said YES, CoT said NO
    only_cot = cot_ids - std_ids   # CoT said YES, standard said NO
    agreed   = std_ids & cot_ids

    print(f"\n{'═'*60}")
    print(f"  COMPARISON")
    print(f"{'═'*60}")
    print(f"  Standard time : {t_std:.2f}s")
    print(f"  CoT time      : {t_cot:.2f}s  ({t_cot/t_std:.1f}× slower)")
    print(f"  Agreed visible: {len(agreed)}")

    if only_cot:
        names = [p["name"] for p in vis_cot if str(p["id"]) in only_cot]
        print(f"\n  CoT added ({len(only_cot)}) — standard missed these:")
        for n in names:
            print(f"    + {n}")

    if only_std:
        names = [p["name"] for p in vis_std if str(p["id"]) in only_std]
        print(f"\n  CoT removed ({len(only_std)}) — standard over-included:")
        for n in names:
            print(f"    - {n}")

    if not only_cot and not only_std:
        print("\n  Both methods agreed on every POI.")

    print()


if __name__ == "__main__":
    asyncio.run(main())
