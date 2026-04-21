# utils/visibility.py
#
# Geometric visibility filter using Shapely ray casting + UTM projection.
# Logic ported from tests/test_geoapify_visibility.py.
#
# Public API:
#   filter_visible(pois, user_lat, user_lon, user_heading, buildings=None)
#       → (visible: list[dict], rejected: list[dict])
#
#   diagnose_poi(poi, user_lat, user_lon, user_heading)
#       → dict  (for feedback route)
#
# buildings: dict[place_id → (name: str, wgs84_geom: BaseGeometry)]
# Each returned POI gains: distance_m, angle_deg, confidence, blocked_by.

import logging
import math
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Shapely ───────────────────────────────────────────────────────────────────
try:
    from shapely.geometry import LineString, Point, shape as shapely_shape
    from shapely.geometry.base import BaseGeometry
    from shapely.ops import transform as shp_transform, nearest_points
    SHAPELY = True
except ImportError:
    SHAPELY = False
    BaseGeometry = object
    logger.warning("shapely not installed — ray casting disabled")

# ── pyproj ────────────────────────────────────────────────────────────────────
try:
    from pyproj import Transformer
    PYPROJ = True
except ImportError:
    PYPROJ = False
    logger.warning("pyproj not installed — using lon/lat fallback for ray casting")

# ── geoutils ─────────────────────────────────────────────────────────────────
from utils.geoutils import haversine_meters, bearing as _compass_bearing


# =============================================================================
# Constants
# =============================================================================

FOV_HALF_DEG     = 90     # ±90° → full forward hemisphere
SKYLINE_FOV_HALF = 120    # ±120° — relaxed; tall structures noticed off-center
SKYLINE_MAX_DIST = 2000   # metres — max distance for skyline-visible POIs
PARK_PROXIMITY_M = 80     # park visible if user within 80m of polygon boundary
RAYCAST_MAX_DIST = 300    # metres — primary ray cast range
RAY_TRUNCATE_M   = 2.5    # truncate ray short of target to prevent self-occlusion

# Recognizability: hard AND gate after visibility passes.
# Even a clear sightline doesn't help if the POI is too far to identify.
_RECOG_DIST: dict[str, float] = {
    "very_large": 800.0,
    "large":      350.0,
    "medium":     150.0,
    "small":       40.0,
}

MAX_PARK_DIST_NO_POLY = 100.0   # fallback when no park polygon available


# =============================================================================
# Size classification
# =============================================================================

# Geoapify categories → size bucket
_CAT_SIZE: dict[str, str] = {
    "man_made.tower":                "very_large",
    "tourism.sights.tower":          "very_large",
    "man_made.water_tower":          "very_large",
    "man_made.lighthouse":           "very_large",
    "tourism.sights.lighthouse":     "very_large",
    "building.skyscraper":           "very_large",
    "entertainment.museum":          "large",
    "entertainment.culture.theatre": "large",
    "entertainment.culture":         "large",
    "tourism.sights.castle":         "large",
    "tourism.sights.city_hall":      "large",
    "building.public_and_civil":     "large",
    "building.historic":             "large",
    "heritage":                      "large",
    "heritage.unesco":               "large",
    "tourism":                       "medium",
    "tourism.sights":                "medium",
    "tourism.attraction":            "medium",
    "building":                      "medium",
    "amenity":                       "medium",
    "leisure":                       "small",
    "leisure.park":                  "small",
    "entertainment":                 "small",
    "dogs":                          "small",
    "access_limited":                "small",
}

_SIZE_RANK: dict[str, int] = {
    "very_large": 3, "large": 2, "medium": 1, "small": 0,
}

_SIZE_MAX_DIST: dict[str, float] = {
    "very_large": 1500.0,
    "large":       600.0,
    "medium":      300.0,
    "small":        80.0,
}

# OSM tags → size bucket (fallback when Geoapify categories unavailable)
_TAG_SIZE_FALLBACK: dict[str, str] = {
    "stadium": "very_large", "arena": "very_large",
    "tower": "very_large", "lighthouse": "very_large",
    "cathedral": "large", "university": "large", "college": "large",
    "museum": "large", "theatre": "large", "concert_hall": "large",
    "opera": "large", "castle": "large",
    "place_of_worship": "medium", "church": "medium", "chapel": "medium",
    "mosque": "medium", "temple": "medium", "synagogue": "medium",
    "library": "medium", "townhall": "medium", "courthouse": "medium",
    "attraction": "medium", "monument": "medium", "historic": "medium",
    "cafe": "small", "restaurant": "small", "bar": "small",
    "pub": "small", "shop": "small", "artwork": "small",
    "sculpture": "small", "fountain": "small",
    "park": "small", "garden": "small",
}


def _best_size_from_cats(cats: list[str]) -> str:
    """Largest size bucket across all Geoapify categories."""
    best = "medium"
    for cat in cats:
        for lookup in (cat, ".".join(cat.split(".")[:2]), cat.split(".")[0]):
            if lookup in _CAT_SIZE:
                s = _CAT_SIZE[lookup]
                if _SIZE_RANK[s] > _SIZE_RANK[best]:
                    best = s
                break
    return best


def _best_size_from_tags(tags: dict) -> str:
    """Size bucket from OSM tags — used when Geoapify categories unavailable."""
    for key in ("tourism", "historic", "amenity", "leisure", "building", "man_made"):
        val = tags.get(key, "")
        if val in _TAG_SIZE_FALLBACK:
            return _TAG_SIZE_FALLBACK[val]
    return "medium"


def _best_size(poi: dict) -> str:
    """Return best size bucket, preferring Geoapify categories over OSM tags."""
    tags = poi.get("tags", {})
    # Override: artwork / sculpture is always "small" even if co-tagged with
    # broader cultural categories (e.g. an arts_centre that is also an artwork).
    if tags.get("tourism") in ("artwork",) or tags.get("artwork_type"):
        return "small"
    cats = poi.get("categories", [])
    if cats:
        return _best_size_from_cats(cats)
    return _best_size_from_tags(tags)


# =============================================================================
# Park classification
# =============================================================================

_PARK_CATS = frozenset({
    "leisure", "leisure.park", "leisure.garden",
    "leisure.nature_reserve", "leisure.national_park",
})


def _is_park(poi: dict) -> bool:
    cats = poi.get("categories", [])
    if any(c in _PARK_CATS for c in cats):
        return True
    tags = poi.get("tags", {})
    return tags.get("leisure") in ("park", "garden", "nature_reserve")


# =============================================================================
# UTM projection helpers
# =============================================================================

_utm_xfm_cache: dict[int, Any] = {}


def _utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def _get_utm_transformer(lat: float, lon: float) -> Optional[Any]:
    if not PYPROJ:
        return None
    epsg = _utm_epsg(lat, lon)
    if epsg not in _utm_xfm_cache:
        _utm_xfm_cache[epsg] = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg}", always_xy=True
        )
    return _utm_xfm_cache[epsg]


def _project_geom(geom: Any, transformer: Any) -> Optional[Any]:
    if not SHAPELY or geom is None or transformer is None:
        return None
    try:
        return shp_transform(transformer.transform, geom)
    except Exception:
        return None


# =============================================================================
# FOV
# =============================================================================

def _in_fov(
    user_lat: float, user_lon: float,
    poi_lat:  float, poi_lon:  float,
    heading:  float,
) -> bool:
    bear  = _compass_bearing(user_lat, user_lon, poi_lat, poi_lon)
    delta = abs((bear - heading + 180) % 360 - 180)
    return delta <= FOV_HALF_DEG


# =============================================================================
# Nearest boundary point
# =============================================================================

def _nearest_boundary_point(
    user_lon: float, user_lat: float,
    polygon:  Any,
) -> Optional[tuple[float, float]]:
    """
    Return (lon, lat) of the exterior boundary point closest to the user.

    Uses exterior.project() + exterior.interpolate() — operates on the outer
    ring only so courtyard/interior walls never produce a false target point.
    For MultiPolygon, checks each component polygon and returns the overall
    nearest exterior point.
    """
    if not SHAPELY or polygon is None:
        return None
    user_pt = Point(user_lon, user_lat)
    try:
        if hasattr(polygon, "exterior"):
            ext     = polygon.exterior
            nearest = ext.interpolate(ext.project(user_pt))
        else:
            # MultiPolygon — find nearest exterior point across all parts
            best_dist = float("inf")
            nearest   = None
            for part in polygon.geoms:
                ext = part.exterior
                pt  = ext.interpolate(ext.project(user_pt))
                d   = user_pt.distance(pt)
                if d < best_dist:
                    best_dist = d
                    nearest   = pt
            if nearest is None:
                return None
        return nearest.x, nearest.y
    except Exception:
        return None


# =============================================================================
# Own-geom lookup — spatial containment
# =============================================================================

def _find_own_geom(
    poi_lat:   float,
    poi_lon:   float,
    buildings: dict,   # place_id → (name, wgs84, utm)
) -> Optional[Any]:
    """
    Return the building polygon that contains or is nearest to the POI centroid.

    Two-pass strategy:
      Pass 1 — strict containment (fast path).
      Pass 2 — proximity to exterior ≤ ~24m. Handles complex buildings where
                OSM placed the centroid outside the polygon (entrance gates,
                irregular parcels, place_id mismatch between API calls).
    """
    if not SHAPELY:
        return None
    p = Point(poi_lon, poi_lat)

    for _pid, (_name, wgs84_geom, _utm) in buildings.items():
        if wgs84_geom is None:
            continue
        try:
            if wgs84_geom.contains(p):
                return wgs84_geom
        except Exception:
            continue

    THRESHOLD = 0.00022   # degrees ≈ 24m
    for _pid, (_name, wgs84_geom, _utm) in buildings.items():
        if wgs84_geom is None:
            continue
        try:
            ext = (wgs84_geom.exterior
                   if hasattr(wgs84_geom, "exterior")
                   else wgs84_geom.boundary)
            if ext.distance(p) < THRESHOLD:
                return wgs84_geom
        except Exception:
            continue

    return None


# =============================================================================
# Ray casting
# =============================================================================

def check_line_of_sight(
    user_lat:     float, user_lon: float,
    poi_lat:      float, poi_lon:  float,
    obstacles:    list,   # (name, wgs84_geom, utm_geom_or_None)
    exclude_geom: Optional[Any] = None,
    transformer:  Any = None,
) -> tuple[bool, Optional[str]]:
    """
    Cast a ray from user to target, truncated RAY_TRUNCATE_M short.
    Uses UTM metres when pyproj is available; falls back to lon/lat.
    """
    if not SHAPELY or not obstacles:
        return True, None

    if transformer is not None and PYPROJ:
        ux, uy = transformer.transform(user_lon, user_lat)
        px, py = transformer.transform(poi_lon,  poi_lat)
        total  = math.hypot(px - ux, py - uy)
        if total < RAY_TRUNCATE_M:
            return True, None
        t   = (total - RAY_TRUNCATE_M) / total
        ray = LineString([(ux, uy), (ux + t * (px - ux), uy + t * (py - uy))])

        for name, wgs84_geom, utm_geom in obstacles:
            if wgs84_geom is None or wgs84_geom is exclude_geom:
                continue
            bldg = utm_geom or _project_geom(wgs84_geom, transformer)
            if bldg is None:
                continue
            try:
                if ray.intersects(bldg):
                    return False, name
            except Exception:
                continue
    else:
        total = haversine_meters(user_lat, user_lon, poi_lat, poi_lon)
        if total < RAY_TRUNCATE_M:
            return True, None
        t     = (total - RAY_TRUNCATE_M) / total
        e_lon = user_lon + t * (poi_lon - user_lon)
        e_lat = user_lat + t * (poi_lat - user_lat)
        ray   = LineString([(user_lon, user_lat), (e_lon, e_lat)])

        for name, wgs84_geom, *_ in obstacles:
            if wgs84_geom is None or wgs84_geom is exclude_geom:
                continue
            try:
                if ray.intersects(wgs84_geom):
                    return False, name
            except Exception:
                continue

    return True, None


# =============================================================================
# Recognizability gate
# =============================================================================

def _recognizable(poi: dict, dist_m: float) -> tuple[bool, str]:
    size     = _best_size(poi)
    max_dist = _RECOG_DIST[size]
    if dist_m <= max_dist:
        return True, ""
    return False, f"recog: {size} max {max_dist:.0f}m, actual {dist_m:.0f}m"


# =============================================================================
# Heuristic visibility (POIs beyond RAYCAST_MAX_DIST)
# =============================================================================

def _heuristic_visible(
    poi:         dict,
    user_lat:    float, user_lon: float,
    obstacles:   Optional[list] = None,
    transformer: Any = None,
    buildings:   Optional[dict] = None,
) -> tuple[bool, str]:
    """
    Size × distance gate, then coarse ray cast against existing obstacles.
    No extra API calls — uses already-fetched obstacle polygons.
    """
    dist = haversine_meters(user_lat, user_lon, poi["lat"], poi["lon"])
    size = _best_size(poi)
    max_dist = _SIZE_MAX_DIST[size]

    if dist > max_dist:
        return False, f"heuristic: {size} max {max_dist:.0f}m, actual {dist:.0f}m"

    if obstacles:
        own_geom = _find_own_geom(poi["lat"], poi["lon"], buildings) if buildings else None
        is_vis, blocker = check_line_of_sight(
            user_lat, user_lon, poi["lat"], poi["lon"],
            obstacles, exclude_geom=own_geom, transformer=transformer,
        )
        if not is_vis:
            return False, f"heuristic+raycast: blocked by {blocker}"

    return True, f"heuristic: {size} within {max_dist:.0f}m"


# =============================================================================
# Park visibility
# =============================================================================

def _park_visible(
    poi:       dict,
    user_lat:  float, user_lon: float,
    buildings: dict,
) -> tuple[bool, str]:
    """
    Parks are shown when the user is within PARK_PROXIMITY_M of the polygon
    boundary. Falls back to centroid distance when no polygon is available.
    """
    own_geom = _find_own_geom(poi["lat"], poi["lon"], buildings)

    if own_geom is not None and SHAPELY:
        user_pt  = Point(user_lon, user_lat)
        try:
            ext     = (own_geom.exterior
                       if hasattr(own_geom, "exterior")
                       else own_geom.boundary)
            dist_m  = ext.distance(user_pt) * 111_000
            if dist_m <= PARK_PROXIMITY_M:
                return True, f"park: {dist_m:.0f}m from polygon boundary"
            return False, f"park: {dist_m:.0f}m from boundary > {PARK_PROXIMITY_M}m"
        except Exception:
            pass

    dist = haversine_meters(user_lat, user_lon, poi["lat"], poi["lon"])
    if dist <= MAX_PARK_DIST_NO_POLY:
        return True, f"park: centroid {dist:.0f}m (no polygon)"
    return False, f"park: centroid {dist:.0f}m > {MAX_PARK_DIST_NO_POLY}m (no polygon)"


# =============================================================================
# Skyline visibility
# =============================================================================

def _is_skyline_poi(poi: dict) -> bool:
    """
    True for structures tall enough to be visible above the roofline.

    Checks (in order):
    1. Explicit OSM tags: building=skyscraper/tower, man_made=tower
    2. OSM height data: height ≥ 100m OR building:levels ≥ 25
    3. Geoapify category starts with "building" AND size resolves to very_large
    """
    tags = poi.get("tags", {})
    cats = poi.get("categories", [])

    if tags.get("building") in {"skyscraper", "tower"}:
        return True
    if tags.get("man_made") == "tower":
        return True

    try:
        if float(tags.get("height", 0)) >= 100:
            return True
    except (ValueError, TypeError):
        pass

    try:
        if int(tags.get("building:levels", 0)) >= 25:
            return True
    except (ValueError, TypeError):
        pass

    if any(c.startswith("building") for c in cats) and _best_size(poi) == "very_large":
        return True

    return False


def _skyline_visible(
    poi:          dict,
    user_lat:     float, user_lon: float,
    user_heading: float,
) -> tuple[bool, str]:
    """
    Skyline-level visibility: tall structures seen above the roofline.
    Uses a relaxed FOV (±120°) and a generous 2 km distance cap.
    No ray casting — assumes line of sight above building obstacles.
    """
    # Relaxed FOV — humans notice tall landmarks even slightly off-center
    bear  = _compass_bearing(user_lat, user_lon, poi["lat"], poi["lon"])
    delta = abs((bear - user_heading + 180) % 360 - 180)
    if delta > SKYLINE_FOV_HALF:
        return False, f"skyline: {delta:.0f}° outside ±{SKYLINE_FOV_HALF}° of heading {user_heading:.0f}°"

    dist = haversine_meters(user_lat, user_lon, poi["lat"], poi["lon"])
    if dist > SKYLINE_MAX_DIST:
        return False, f"skyline: too far {dist:.0f}m (max {SKYLINE_MAX_DIST}m)"

    return True, f"skyline: visible at {dist:.0f}m"


# =============================================================================
# Single-POI visibility decision
# =============================================================================

def _check_poi(
    poi:         dict,
    user_lat:    float, user_lon: float,
    user_heading: float,
    obstacles:   list,   # (name, wgs84, utm) triples — pre-projected
    buildings:   dict,   # place_id → (name, wgs84, utm) — for own-geom lookup
    transformer: Any,
) -> tuple[bool, str]:
    """
    Run all visibility gates in priority order:
      0. Skyline short-circuit (skyscrapers/towers — own FOV + 2 km gate)
      1. FOV filter (standard ±90°)
      2. Park special-case (proximity to polygon)
      3. Ray cast to nearest boundary point (or heuristic beyond 300m)
      4. Recognizability gate
    """
    dist = haversine_meters(user_lat, user_lon, poi["lat"], poi["lon"])

    # 0. Skyline short-circuit — runs before standard FOV so relaxed 120° applies
    if _is_skyline_poi(poi):
        return _skyline_visible(poi, user_lat, user_lon, user_heading)

    # 1. FOV gate
    if not _in_fov(user_lat, user_lon, poi["lat"], poi["lon"], user_heading):
        bear = _compass_bearing(user_lat, user_lon, poi["lat"], poi["lon"])
        return False, f"fov: {bear:.0f}° outside ±{FOV_HALF_DEG}° of heading {user_heading:.0f}°"

    # 2. Park special-case
    if _is_park(poi):
        is_vis, reason = _park_visible(poi, user_lat, user_lon, buildings)
        if not is_vis:
            return False, reason
        ok, why = _recognizable(poi, dist)
        return (False, why) if not ok else (True, reason)

    # 3. Ray cast or heuristic
    if dist > RAYCAST_MAX_DIST:
        is_vis, reason = _heuristic_visible(
            poi, user_lat, user_lon,
            obstacles=obstacles, transformer=transformer, buildings=buildings,
        )
    else:
        own_geom = _find_own_geom(poi["lat"], poi["lon"], buildings)
        # Cast to nearest exterior boundary point — fixes "wrong facade" problem
        if own_geom is not None:
            bp = _nearest_boundary_point(user_lon, user_lat, own_geom)
        else:
            bp = None
        target_lat = bp[1] if bp else poi["lat"]
        target_lon = bp[0] if bp else poi["lon"]

        is_vis_ray, blocker = check_line_of_sight(
            user_lat, user_lon, target_lat, target_lon,
            obstacles, exclude_geom=own_geom, transformer=transformer,
        )
        is_vis = is_vis_ray
        reason = blocker or "clear"

    if not is_vis:
        return False, reason

    # 4. Recognizability gate
    ok, why = _recognizable(poi, dist)
    return (False, why) if not ok else (True, reason)


# =============================================================================
# Public API
# =============================================================================

def filter_visible(
    pois:          list[dict],
    user_lat:      float,
    user_lon:      float,
    user_heading:  float,
    buildings:     Optional[dict] = None,   # place_id → (name, wgs84_geom)
    user_street:   Optional[str]  = None,   # unused — kept for API compatibility
) -> tuple[list[dict], list[dict]]:
    """
    Filter POIs by geometric visibility: FOV + ray casting + recognizability.

    buildings: dict[place_id → (name, wgs84_geom)] from the area cache.
               When None (no Geoapify key set), ray casting is skipped and
               only FOV + size/distance gates are applied.

    Each returned POI gains: distance_m, angle_deg, confidence, blocked_by.
    Returns (visible, rejected), both sorted by distance_m ascending.
    """
    if not pois:
        return [], []

    # ── Debug dump ────────────────────────────────────────────────────────────
    # Set env var TOURAI_VIS_DEBUG=1 to print a per-POI table to stderr.
    # Columns: name | categories | size | recog_m | dist_m | skyline?
    # Use this to spot missing landmarks (wrong categories) and oversized junk.
    import os as _os
    if _os.environ.get("TOURAI_VIS_DEBUG"):
        import sys as _sys
        print(f"\n{'─'*90}", file=_sys.stderr)
        print(f"filter_visible  lat={user_lat:.5f} lon={user_lon:.5f} heading={user_heading:.1f}°  pois={len(pois)}", file=_sys.stderr)
        print(f"{'NAME':<35} {'SIZE':<10} {'RECOG_M':<9} {'DIST_M':<8} {'SKYLINE':<8} CATEGORIES", file=_sys.stderr)
        print(f"{'─'*90}", file=_sys.stderr)
        for _p in sorted(pois, key=lambda p: haversine_meters(user_lat, user_lon, p["lat"], p["lon"])):
            _dist  = haversine_meters(user_lat, user_lon, _p["lat"], _p["lon"])
            _size  = _best_size(_p)
            _recog = _RECOG_DIST[_size]
            _sky   = "YES" if _is_skyline_poi(_p) else "-"
            _cats  = ",".join(_p.get("categories", [])) or "(none)"
            print(f"{_p['name']:<35} {_size:<10} {_recog:<9.0f} {_dist:<8.0f} {_sky:<8} {_cats}", file=_sys.stderr)
        print(f"{'─'*90}\n", file=_sys.stderr)
    # ── End debug dump ────────────────────────────────────────────────────────

    xfm = _get_utm_transformer(user_lat, user_lon)

    # Pre-project all building geometries to UTM once for this call
    if buildings:
        bldg_utm: dict[str, tuple] = {}
        for pid, entry in buildings.items():
            name, wgs84 = entry[0], entry[1]
            utm = _project_geom(wgs84, xfm)
            bldg_utm[pid] = (name, wgs84, utm)

        # ── _obstacles_in_bbox filter ──────────────────────────────────────
        # Mirror test_geoapify_visibility.py: only keep obstacle buildings
        # whose centroid falls within the bounding box of user + all POI
        # centroids ± 0.0005° (~55m). Prevents buildings far from every POI
        # from being tested on every ray cast.
        if SHAPELY and bldg_utm and pois:
            _BBOX_BUFFER = 0.0005
            all_lats = [user_lat] + [p["lat"] for p in pois]
            all_lons = [user_lon] + [p["lon"] for p in pois]
            _min_lat = min(all_lats) - _BBOX_BUFFER
            _max_lat = max(all_lats) + _BBOX_BUFFER
            _min_lon = min(all_lons) - _BBOX_BUFFER
            _max_lon = max(all_lons) + _BBOX_BUFFER

            bldg_utm = {
                pid: triple
                for pid, triple in bldg_utm.items()
                if triple[1] is not None and (
                    _min_lat <= triple[1].centroid.y <= _max_lat and
                    _min_lon <= triple[1].centroid.x <= _max_lon
                )
            }

        obstacles = list(bldg_utm.values())
    else:
        bldg_utm  = {}
        obstacles = []

    visible:  list[dict] = []
    rejected: list[dict] = []

    for poi in pois:
        dist  = haversine_meters(user_lat, user_lon, poi["lat"], poi["lon"])
        bear  = _compass_bearing(user_lat, user_lon, poi["lat"], poi["lon"])
        angle = abs((bear - user_heading + 180) % 360 - 180)

        is_vis, reason = _check_poi(
            poi, user_lat, user_lon, user_heading,
            obstacles, bldg_utm, xfm,
        )

        enriched = {
            **poi,
            "distance_m": round(dist, 1),
            "angle_deg":  round(angle, 1),
            "blocked_by": [],
        }

        if is_vis:
            enriched["confidence"] = 1.0
            visible.append(enriched)
        else:
            enriched["confidence"]      = 0.0
            enriched["filtered_reason"] = reason
            rejected.append(enriched)

    visible.sort(key=lambda p: p["distance_m"])
    rejected.sort(key=lambda p: p["distance_m"])
    return visible, rejected


def diagnose_poi(
    poi:          dict,
    user_lat:     float,
    user_lon:     float,
    user_heading: float,
    user_street:  Optional[str] = None,   # unused — kept for API compatibility
) -> dict:
    """
    Run the full visibility pipeline on a single POI and return a trace dict.
    Used by the feedback route to diagnose false positives/negatives.
    """
    dist  = haversine_meters(user_lat, user_lon, poi["lat"], poi["lon"])
    bear  = _compass_bearing(user_lat, user_lon, poi["lat"], poi["lon"])
    angle = abs((bear - user_heading + 180) % 360 - 180)
    size  = _best_size(poi)

    xfm     = _get_utm_transformer(user_lat, user_lon)
    is_vis, reason = _check_poi(
        poi, user_lat, user_lon, user_heading,
        obstacles=[], buildings={}, transformer=xfm,
    )

    return {
        "poi_id":           poi.get("id"),
        "poi_name":         poi.get("name"),
        "distance_m":       round(dist, 1),
        "bearing_deg":      round(bear, 1),
        "angle_deg":        round(angle, 1),
        "in_fov":           angle < FOV_HALF_DEG,
        "size":             size,
        "rule":             reason,
        "rule_description": reason,
        "visible":          is_vis,
        "confidence":       1.0 if is_vis else 0.0,
        "filter_now_says":  "YES" if is_vis else "NO",
    }
