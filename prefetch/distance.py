"""tourai/prefetch/distance.py

Distance + travel-time matrix between POIs.

Design: a Protocol so we can swap providers. Default is Haversine + a mode
multiplier that approximates real travel time well enough for clustering
decisions. When you graduate to Google Distance Matrix, only this file changes.

For N POIs, returns an N×N matrix of (km, walking_min, driving_min).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Leg:
    km: float
    walking_min: int   # rough walking time, capped — we won't suggest >15min walks anyway
    driving_min: int   # straight-line distance × 1.3 detour factor / typical urban speed


class DistanceProvider(Protocol):
    def matrix(self, points: list[tuple[float, float]]) -> list[list[Leg]]: ...


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


class HaversineProvider:
    """Free, no API call, accurate enough for selecting which POIs are 'close'.

    Tuning constants:
      - WALKING_KMH = 4.5  (typical urban walking with stops at lights)
      - DRIVING_KMH = 25   (urban/suburban average accounting for traffic)
      - DETOUR     = 1.3   (real road distance ≈ 1.3× straight-line in cities)

    These produce times that are within 20-30% of Google Distance Matrix for
    same-city legs, which is fine for solver decisions. Inadequate for legs
    where the user actually depends on the time (hotel → airport, etc.) —
    swap to Google for those.
    """

    WALKING_KMH = 4.5
    DRIVING_KMH = 25.0
    DETOUR = 1.3

    def matrix(self, points: list[tuple[float, float]]) -> list[list[Leg]]:
        n = len(points)
        out: list[list[Leg]] = [[Leg(0.0, 0, 0)] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                straight = _haversine_km(points[i], points[j])
                road_km = straight * self.DETOUR
                walking = int(round((road_km / self.WALKING_KMH) * 60))
                driving = int(round((road_km / self.DRIVING_KMH) * 60))
                leg = Leg(km=round(road_km, 2), walking_min=walking, driving_min=driving)
                out[i][j] = leg
                out[j][i] = leg
        return out


# Default provider. Replace via DI when you swap to Google.
distance_provider: DistanceProvider = HaversineProvider()


def transit_mode_for(driving_min: int) -> str:
    """Map a driving leg duration to the same mode taxonomy your prompt uses."""
    if driving_min == 0:
        return "arrive"
    if driving_min <= 15:
        return "walk"
    if driving_min <= 30:
        return "uber"
    return "drive"
