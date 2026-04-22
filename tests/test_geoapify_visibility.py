#!/usr/bin/env python3
"""
tests/test_geoapify_visibility.py

Standalone test for Geoapify-based visibility system.
Tests POI fetching, building polygon retrieval, ray casting, 300m caching,
and credit consumption across a simulated walk.

Run:
    python tests/test_geoapify_visibility.py

Requires:
    pip install httpx shapely pyproj python-dotenv
    GEOAPIFY_API_KEY in .env or environment
"""

import os
import sys
import time
import math
from typing import Optional, Any

# ── .env loading ─────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── HTTP client ───────────────────────────────────────────────────────────────
try:
    import httpx
    def _get(url, params=None, timeout=10.0):
        r = httpx.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
except ImportError:
    try:
        import requests
        def _get(url, params=None, timeout=10.0):
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
    except ImportError:
        print("ERROR: install httpx or requests → pip install httpx")
        sys.exit(1)

# ── Shapely ───────────────────────────────────────────────────────────────────
try:
    from shapely.geometry import LineString, Point, shape as shapely_shape
    from shapely.geometry.base import BaseGeometry
    from shapely.ops import transform as shp_transform, nearest_points
    SHAPELY = True
except ImportError:
    SHAPELY = False
    BaseGeometry = object
    print("WARNING: shapely not installed (pip install shapely) — ray casting disabled\n")

# ── pyproj ────────────────────────────────────────────────────────────────────
try:
    from pyproj import Transformer
    PYPROJ = True
except ImportError:
    PYPROJ = False
    print("WARNING: pyproj not installed (pip install pyproj) — using lon/lat fallback\n")


# =============================================================================
# Configuration
# =============================================================================

API_KEY        = os.getenv("GEOAPIFY_API_KEY", "")
DAILY_LIMIT    = 3_000

PLACES_URL     = "https://api.geoapify.com/v2/places"
DETAILS_URL    = "https://api.geoapify.com/v2/place-details"

GRID_RES       = 0.003   # ~333m grid cell

WALK_START     = (32.791514,-96.795706)
WALK_END       = (32.787654,-96.800159)
NUM_UPDATES    = 10

POI_RADIUS     = 500
POI_CATEGORIES = (
    "tourism,amenity,heritage,leisure,"
    "entertainment.museum,entertainment.culture,"
    "man_made.tower,man_made.water_tower,man_made.lighthouse,man_made.bridge,"
    "tourism.sights.tower,tourism.sights.castle,tourism.sights.city_hall,"
    "tourism.sights.bridge,tourism.sights.lighthouse,"
    "building.public_and_civil,building.historic"
)
POI_LIMIT      = 50

MAX_OBSTACLE_BUILDINGS = 50
OBSTACLE_RADIUS        = 200

RAY_TRUNCATE_M  = 2.5    # metres short of target — prevents self-occlusion
RAYCAST_MAX_DIST = 300   # beyond this, use heuristic + coarse raycast

# Fix 2: FOV filter — reject POIs more than this many degrees off heading
FOV_HALF_DEG = 90        # ±90° → full forward hemisphere

# Fix 3: Recognizability — hard AND gate applied after visibility passes.
# Even a clear sightline doesn't help if the POI is too far to identify.
_RECOG_DIST: dict[str, float] = {
    "very_large": 800.0,   # towers, skyscrapers — readable from far
    "large":      350.0,   # museums, civic buildings
    "medium":     150.0,   # churches, government buildings
    "small":       40.0,   # shops, cafes, sculptures
}

# Fix 5: Parks need proximity to polygon boundary, not line-of-sight
PARK_PROXIMITY_M = 80    # must be within 80m of park polygon edge


# =============================================================================
# Credit tracker
# =============================================================================

class CreditTracker:
    def __init__(self, daily_limit: int = DAILY_LIMIT):
        self.used        = 0
        self.daily_limit = daily_limit
        self._log: list[str] = []

    def charge(self, n: int = 1, reason: str = "") -> None:
        self.used += n
        self._log.append(f"+{n} ({reason})")

    @property
    def remaining(self) -> int:
        return self.daily_limit - self.used

    def summary(self) -> str:
        return f"Credits used this session: {self.used}"


credits = CreditTracker()


# =============================================================================
# Caches
# =============================================================================

_area_cache: dict[str, dict] = {}
_bldg_cache: dict[str, Optional[BaseGeometry]] = {}
_utm_xfm_cache: dict[int, Any] = {}


def _grid_key(lat: float, lon: float) -> str:
    g_lat = round(lat / GRID_RES) * GRID_RES
    g_lon = round(lon / GRID_RES) * GRID_RES
    return f"{g_lat:.4f},{g_lon:.4f}"


# =============================================================================
# Geometry helpers
# =============================================================================

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def bearing_to(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing (0=N, 90=E, 180=S, 270=W)."""
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(math.radians(lat2))
    y = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


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
# Fix 2: FOV helpers
# =============================================================================

def _travel_heading(waypoints: list[tuple[float, float]], idx: int) -> float:
    """
    Compass heading of travel at waypoint idx.
    Uses forward direction (to next) when possible, otherwise backward.
    """
    if idx < len(waypoints) - 1:
        return bearing_to(*waypoints[idx], *waypoints[idx + 1])
    return bearing_to(*waypoints[idx - 1], *waypoints[idx])


def _in_fov(
    user_lat: float, user_lon: float,
    poi_lat:  float, poi_lon:  float,
    heading:  float,
) -> bool:
    """Return True if POI is within FOV_HALF_DEG of the travel heading."""
    bear  = bearing_to(user_lat, user_lon, poi_lat, poi_lon)
    delta = abs((bear - heading + 180) % 360 - 180)
    return delta <= FOV_HALF_DEG


# =============================================================================
# Fix 4: Nearest boundary point
# =============================================================================

def _nearest_boundary_point(
    user_lon: float, user_lat: float,
    polygon:  Any,              # shapely Polygon/MultiPolygon in WGS84
) -> Optional[tuple[float, float]]:
    """
    Return (lon, lat) of the polygon exterior boundary point closest to the user.

    Uses exterior.project() + exterior.interpolate() — the canonical shapely
    approach.  This is important for two reasons:

    1. Complex buildings (church with courtyard, historic house with grounds):
       OSM centroid is often the geometric centre of the parcel, which may sit
       inside a courtyard or behind a wall.  The nearest exterior boundary point
       is the facade the user is actually looking at.

    2. .exterior is the outer ring only.  Using .boundary would also include
       interior rings (courtyard walls), which can produce points inside the
       building shell and cause false self-occlusion.

    For MultiPolygon (e.g. a campus with detached wings), iterates each
    component polygon and returns the overall nearest exterior point.
    """
    if not SHAPELY or polygon is None:
        return None
    user_pt = Point(user_lon, user_lat)
    try:
        if hasattr(polygon, "exterior"):
            # Single Polygon — project + interpolate on outer ring only
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
        return nearest.x, nearest.y   # (lon, lat)
    except Exception:
        return None


# =============================================================================
# Fix 5: Park classification
# =============================================================================

_PARK_CATS = frozenset({"leisure", "leisure.park", "leisure.garden",
                         "leisure.nature_reserve", "leisure.national_park"})


def _is_park(poi: dict) -> bool:
    cats = poi.get("categories", [])
    return any(c in _PARK_CATS for c in cats)


def _park_visible(
    poi:       dict,
    user_lat:  float,
    user_lon:  float,
    buildings: dict,           # place_id → (name, wgs84, utm)
) -> tuple[bool, str]:
    """
    Parks are shown if the user is within PARK_PROXIMITY_M of the park polygon.
    Falls back to simple distance heuristic when no polygon is available.
    """
    dist = haversine_m(user_lat, user_lon, poi["lat"], poi["lon"])

    own_geom = _find_own_geom(poi["lat"], poi["lon"], buildings)

    if own_geom is not None and SHAPELY:
        user_pt  = Point(user_lon, user_lat)
        try:
            boundary = (own_geom.exterior
                        if hasattr(own_geom, "exterior")
                        else own_geom.boundary)
            dist_deg = boundary.distance(user_pt)
            dist_m   = dist_deg * 111_000   # rough conversion; accurate enough
            if dist_m <= PARK_PROXIMITY_M:
                return True, f"park: {dist_m:.0f}m from polygon boundary"
            return False, f"park: {dist_m:.0f}m from boundary > {PARK_PROXIMITY_M}m"
        except Exception:
            pass

    # No polygon — fall back to a tight distance heuristic
    MAX_PARK_DIST = 100.0
    if dist <= MAX_PARK_DIST:
        return True, f"park: centroid {dist:.0f}m (no polygon)"
    return False, f"park: centroid {dist:.0f}m > {MAX_PARK_DIST}m (no polygon)"


# =============================================================================
# Size classification
# =============================================================================

_CAT_SIZE: dict[str, str] = {
    "man_made.tower":                "very_large",
    "tourism.sights.tower":          "very_large",
    "man_made.water_tower":          "very_large",
    "man_made.lighthouse":           "very_large",
    "tourism.sights.lighthouse":     "very_large",
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

_SIZE_MAX_DIST: dict[str, float] = {
    "very_large": 1500.0,
    "large":       600.0,
    "medium":      300.0,
    "small":        80.0,
}

_SIZE_RANK: dict[str, int] = {
    "very_large": 3, "large": 2, "medium": 1, "small": 0,
}


def _best_size(cats: list[str]) -> str:
    """Return the largest size bucket across all categories."""
    best = "medium"
    for cat in cats:
        for lookup in (cat, ".".join(cat.split(".")[:2]), cat.split(".")[0]):
            if lookup in _CAT_SIZE:
                s = _CAT_SIZE[lookup]
                if _SIZE_RANK[s] > _SIZE_RANK[best]:
                    best = s
                break
    return best


# =============================================================================
# Fix 1: Noise filter — name field only, no formatted-address fallback
# =============================================================================

def _is_noise(poi: dict) -> bool:
    """
    Return True for POIs that should be suppressed.

    Rule: only keep POIs that have a real 'name' tag from OSM.
    Geoapify's 'formatted' field is a constructed address string — it is NOT
    a place name, so we reject any POI where the name came from formatted
    rather than the actual name field.

    Also rejects Geoapify access/road features regardless of name.
    """
    if not poi.get("_has_real_name", False):
        return True
    cats = poi.get("categories", [])
    if any(c.startswith("access") for c in cats):
        return True
    return False


# =============================================================================
# Geoapify: Places API
# =============================================================================

def fetch_pois(lat: float, lon: float) -> list[dict]:
    """Fetch named storytelling targets. Cost: 1 credit."""
    params = {
        "categories": POI_CATEGORIES,
        "filter":     f"circle:{lon},{lat},{POI_RADIUS}",
        "limit":      POI_LIMIT,
        "apiKey":     API_KEY,
    }
    try:
        data = _get(PLACES_URL, params=params)
    except Exception as e:
        print(f"    ERROR fetching POIs: {e}")
        return []

    pois: list[dict] = []
    for feat in data.get("features", []):
        props  = feat.get("properties", {})
        geom   = feat.get("geometry", {})
        coords = geom.get("coordinates", [0.0, 0.0])

        # Fix 1: Only use the 'name' field.  'formatted' is a constructed
        # address string — not a place name.  Mark whether a real name exists
        # so _is_noise() can reject nameless features.
        real_name = (props.get("name") or "").strip()
        pois.append({
            "id":              props.get("place_id", ""),
            "name":            real_name or "Unknown",
            "_has_real_name":  bool(real_name),
            "lat":             coords[1],
            "lon":             coords[0],
            "categories":      props.get("categories", []),
        })

    credits.charge(1, "Places API fetch")
    return pois


def fetch_obstacle_buildings(lat: float, lon: float) -> list[dict]:
    """Fetch all buildings within OBSTACLE_RADIUS. Cost: 1 credit."""
    params = {
        "categories": "building",
        "filter":     f"circle:{lon},{lat},{OBSTACLE_RADIUS}",
        "limit":      100,
        "apiKey":     API_KEY,
    }
    try:
        data = _get(PLACES_URL, params=params)
    except Exception as e:
        print(f"    ERROR fetching obstacle buildings: {e}")
        return []

    buildings = []
    for feat in data.get("features", []):
        props  = feat.get("properties", {})
        geom   = feat.get("geometry", {})
        coords = geom.get("coordinates", [0.0, 0.0])
        buildings.append({
            "id":   props.get("place_id", ""),
            "name": props.get("name") or props.get("formatted") or "building",
            "lat":  coords[1],
            "lon":  coords[0],
        })

    credits.charge(1, "obstacle buildings fetch")
    return buildings


def fetch_building_geometry(place_id: str) -> Optional[BaseGeometry]:
    """Fetch building footprint polygon. Cost: 1 credit. Returns None for Points."""
    if place_id in _bldg_cache:
        return _bldg_cache[place_id]

    params = {
        "id":       place_id,
        "features": "details,geometry",
        "apiKey":   API_KEY,
    }
    try:
        data = _get(DETAILS_URL, params=params)
    except Exception as e:
        print(f"    ERROR fetching geometry for {place_id[:12]}…: {e}")
        _bldg_cache[place_id] = None
        return None

    features = data.get("features", [])
    if not features:
        _bldg_cache[place_id] = None
        return None

    geom_data = features[0].get("geometry")
    if not geom_data or not SHAPELY:
        _bldg_cache[place_id] = None
        return None

    if geom_data.get("type") == "Point":
        _bldg_cache[place_id] = None
        return None

    try:
        geom = shapely_shape(geom_data)
        _bldg_cache[place_id] = geom
        credits.charge(1, f"building geometry {place_id[:8]}…")
        return geom
    except Exception as e:
        print(f"    ERROR parsing geometry for {place_id[:12]}…: {e}")
        _bldg_cache[place_id] = None
        return None


# =============================================================================
# Obstacle selection — bounding-box filter
# =============================================================================

def _obstacles_in_bbox(
    user_lat:      float,
    user_lon:      float,
    pois:          list[dict],
    raw_obstacles: list[dict],
    cap:           int = MAX_OBSTACLE_BUILDINGS,
) -> list[dict]:
    all_lats = [user_lat] + [p["lat"] for p in pois]
    all_lons = [user_lon] + [p["lon"] for p in pois]

    BUFFER  = 0.0005   # ~55m
    min_lat = min(all_lats) - BUFFER
    max_lat = max(all_lats) + BUFFER
    min_lon = min(all_lons) - BUFFER
    max_lon = max(all_lons) + BUFFER

    in_bbox = [
        b for b in raw_obstacles
        if min_lat <= b["lat"] <= max_lat and min_lon <= b["lon"] <= max_lon
    ]
    in_bbox.sort(key=lambda b: haversine_m(user_lat, user_lon, b["lat"], b["lon"]))
    return in_bbox[:cap]


# =============================================================================
# Own-geom lookup — spatial containment
# =============================================================================

def _find_own_geom(
    poi_lat:   float,
    poi_lon:   float,
    buildings: dict,   # place_id → (name, wgs84, utm)
) -> Optional[BaseGeometry]:
    """
    Return the building polygon that spatially contains or is nearest to the
    POI centroid.

    Two-pass strategy:
      Pass 1 — containment: centroid is strictly inside the polygon.
               Catches well-formed buildings where OSM placed the node inside.
      Pass 2 — proximity:   centroid is within ~25m of the polygon exterior.
               Catches complex buildings (National Shrine, historic houses)
               where OSM's centroid sits outside the polygon because the parcel
               outline is irregular or the node was placed on an entrance gate.
               0.00022° ≈ 24m at Dallas latitude — large enough to cover most
               OSM placement imprecision, small enough not to match a neighbour.

    place_id mismatch between the Places API and Place-Details API is the main
    reason we can't rely on a dict key lookup here.
    """
    if not SHAPELY:
        return None
    p = Point(poi_lon, poi_lat)

    # Pass 1: containment (fast, exact)
    for _pid, (_name, wgs84_geom, _utm) in buildings.items():
        if wgs84_geom is None:
            continue
        try:
            if wgs84_geom.contains(p):
                return wgs84_geom
        except Exception:
            continue

    # Pass 2: proximity to exterior boundary (~25m threshold)
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
    exclude_geom: Optional[BaseGeometry] = None,
    transformer:  Any = None,
) -> tuple[bool, Optional[str]]:
    """
    Cast a ray from user to POI, truncated RAY_TRUNCATE_M short of the target.
    Uses UTM (metres) when pyproj is available.
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
        total = haversine_m(user_lat, user_lon, poi_lat, poi_lon)
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
# Fix 3: Recognizability gate
# =============================================================================

def _recognizable(poi: dict, dist_m: float) -> tuple[bool, str]:
    """
    Hard AND gate: even a clear sightline doesn't matter if the POI is too
    far away to identify.  Applied after visibility passes.
    """
    size     = _best_size(poi.get("categories", []))
    max_dist = _RECOG_DIST[size]
    if dist_m <= max_dist:
        return True, ""
    return False, f"recog: {size} max {max_dist:.0f}m, actual {dist_m:.0f}m"


# =============================================================================
# Heuristic visibility (POIs beyond RAYCAST_MAX_DIST)
#
# Fix 6: pass buildings so _find_own_geom can compute exclude_geom — prevents
# the heuristic raycast path from suffering the same self-exclusion bug as
# the primary path did before fix 3 (own_geom lookup by containment).
# =============================================================================

def _heuristic_visible(
    poi:         dict,
    user_lat:    float,
    user_lon:    float,
    obstacles:   Optional[list] = None,
    transformer: Any = None,
    buildings:   Optional[dict] = None,   # Fix 6: for exclude_geom lookup
) -> tuple[bool, str]:
    dist = haversine_m(user_lat, user_lon, poi["lat"], poi["lon"])
    size = _best_size(poi.get("categories", []))
    max_dist = _SIZE_MAX_DIST[size]

    if dist > max_dist:
        return False, f"heuristic: {size} max {max_dist:.0f}m, actual {dist:.0f}m"

    if obstacles:
        # Fix 6: find and exclude the POI's own geometry so it cannot occlude itself
        own_geom = (
            _find_own_geom(poi["lat"], poi["lon"], buildings)
            if buildings else None
        )
        is_vis, blocker = check_line_of_sight(
            user_lat, user_lon, poi["lat"], poi["lon"],
            obstacles,
            exclude_geom=own_geom,
            transformer=transformer,
        )
        if not is_vis:
            return False, f"heuristic+raycast: blocked by {blocker}"

    return True, f"heuristic: {size} within {max_dist:.0f}m"


# =============================================================================
# Single-POI visibility decision
#
# Combines all gates in priority order:
#   1. FOV filter (heading from waypoints)
#   2. Park special-case (proximity to polygon)
#   3. Ray cast (centroid for no-polygon POIs, nearest boundary for polygon POIs)
#      or heuristic + coarse raycast for dist > RAYCAST_MAX_DIST
#   4. Recognizability gate
# =============================================================================

def _check_poi(
    poi:        dict,
    user_lat:   float,
    user_lon:   float,
    heading:    Optional[float],   # None = no FOV filter
    obstacles:  list,
    buildings:  dict,
    transformer: Any,
) -> tuple[bool, str]:

    dist = haversine_m(user_lat, user_lon, poi["lat"], poi["lon"])

    # ── Fix 2: FOV gate ───────────────────────────────────────────────────────
    if heading is not None:
        if not _in_fov(user_lat, user_lon, poi["lat"], poi["lon"], heading):
            return False, f"fov: {bearing_to(user_lat, user_lon, poi['lat'], poi['lon']):.0f}° outside ±{FOV_HALF_DEG}° of heading {heading:.0f}°"

    # ── Fix 5: Park special-case ──────────────────────────────────────────────
    if _is_park(poi):
        is_vis, reason = _park_visible(poi, user_lat, user_lon, buildings)
        if not is_vis:
            return False, reason
        ok, why = _recognizable(poi, dist)
        if not ok:
            return False, why
        return True, reason

    # ── Primary path: raycast or heuristic ───────────────────────────────────
    if dist > RAYCAST_MAX_DIST:
        is_vis, reason = _heuristic_visible(
            poi, user_lat, user_lon,
            obstacles=obstacles, transformer=transformer, buildings=buildings,
        )
        tag = "heuristic"
    else:
        own_geom = _find_own_geom(poi["lat"], poi["lon"], buildings)

        # Fix 4: Cast to nearest boundary point when polygon is available.
        # Casting to centroid gives false positives when the user is behind
        # the building — the nearest boundary point is the visible face.
        if own_geom is not None:
            bp = _nearest_boundary_point(user_lon, user_lat, own_geom)
        else:
            bp = None

        target_lat = bp[1] if bp else poi["lat"]
        target_lon = bp[0] if bp else poi["lon"]

        is_vis, blocker = check_line_of_sight(
            user_lat, user_lon, target_lat, target_lon,
            obstacles,
            exclude_geom=own_geom,
            transformer=transformer,
        )
        reason = blocker or "clear"
        tag    = "raycast"
        is_vis = is_vis  # alias for clarity

    if not is_vis:
        return False, reason

    # ── Fix 3: Recognizability gate (applied after visibility passes) ─────────
    ok, why = _recognizable(poi, dist)
    if not ok:
        return False, why

    return True, tag


# =============================================================================
# Area pipeline
# =============================================================================

def process_new_area(
    user_lat: float,
    user_lon: float,
    heading:  Optional[float] = None,
) -> dict:
    raw_pois = fetch_pois(user_lat, user_lon)
    pois = [p for p in raw_pois if not _is_noise(p)]
    if not pois:
        return {"pois": [], "buildings": {}, "transformer": None,
                "visible": [], "blocked": []}

    raw_obstacles = fetch_obstacle_buildings(user_lat, user_lon)
    candidates    = _obstacles_in_bbox(user_lat, user_lon, pois, raw_obstacles)

    xfm = _get_utm_transformer(user_lat, user_lon)

    buildings: dict[str, tuple] = {}
    newly_fetched = 0

    for bldg in candidates:
        pid = bldg["id"]
        if not pid:
            continue
        already_cached = pid in _bldg_cache
        wgs84 = fetch_building_geometry(pid)
        if wgs84 is not None:
            utm = _project_geom(wgs84, xfm)
            buildings[pid] = (bldg["name"], wgs84, utm)
        if not already_cached:
            newly_fetched += 1

    if newly_fetched:
        print(f"  Fetching {newly_fetched} obstacle geometries: {newly_fetched} credits")
    if buildings:
        print(f"  Obstacle polygons ({len(buildings)}): "
              + ", ".join(name for name, _, _ in buildings.values()))

    obstacles = list(buildings.values())
    visible, blocked = [], []

    for poi in pois:
        is_vis, reason = _check_poi(
            poi, user_lat, user_lon, heading, obstacles, buildings, xfm
        )
        tag = reason if reason in ("raycast", "heuristic") else reason
        if is_vis:
            visible.append({**poi, "_visibility": reason})
        else:
            blocked.append({**poi, "_blocked_by": reason, "_visibility": "blocked"})

    return {
        "pois":        pois,
        "buildings":   buildings,
        "transformer": xfm,
        "visible":     visible,
        "blocked":     blocked,
    }


def recast_from_cache(
    user_lat: float,
    user_lon: float,
    cached:   dict,
    heading:  Optional[float] = None,
) -> tuple[list, list]:
    """Re-run all visibility checks from a new position. 0 API calls."""
    buildings = cached["buildings"]
    xfm       = cached.get("transformer")
    obstacles = list(buildings.values())
    visible, blocked = [], []

    for poi in cached["pois"]:
        is_vis, reason = _check_poi(
            poi, user_lat, user_lon, heading, obstacles, buildings, xfm
        )
        if is_vis:
            visible.append({**poi, "_visibility": reason})
        else:
            blocked.append({**poi, "_blocked_by": reason, "_visibility": "blocked"})

    return visible, blocked


# =============================================================================
# Walk simulation
# =============================================================================

def interpolate_walk(
    start: tuple[float, float],
    end:   tuple[float, float],
    steps: int,
) -> list[tuple[float, float]]:
    lat1, lon1 = start
    lat2, lon2 = end
    return [
        (
            lat1 + (lat2 - lat1) * i / max(steps - 1, 1),
            lon1 + (lon2 - lon1) * i / max(steps - 1, 1),
        )
        for i in range(steps)
    ]


def run_simulation() -> None:
    print("=" * 60)
    print("=== Geoapify Visibility Test ===")
    print("=" * 60)

    if not API_KEY:
        print("ERROR: GEOAPIFY_API_KEY not set.")
        print("  Add it to your .env file or: export GEOAPIFY_API_KEY=your_key")
        sys.exit(1)

    masked = API_KEY[:4] + "****" + API_KEY[-4:] if len(API_KEY) > 8 else "****"
    print(f"API Key:     {masked}")
    print(f"Daily limit: {DAILY_LIMIT:,} credits")
    print(f"Walk:        {WALK_START[0]},{WALK_START[1]} → "
          f"{WALK_END[0]},{WALK_END[1]} ({NUM_UPDATES} updates)")
    print(f"Projection:  {'UTM via pyproj' if PYPROJ else 'lon/lat fallback'}")
    print(f"Ray casting: {'enabled' if SHAPELY else 'disabled — pip install shapely'}")
    print(f"FOV filter:  ±{FOV_HALF_DEG}° from travel heading")
    print()

    waypoints  = interpolate_walk(WALK_START, WALK_END, NUM_UPDATES)
    cache_hits = cache_misses = 0

    for i, (lat, lon) in enumerate(waypoints, start=1):
        dist_str = ""
        if i > 1:
            prev = waypoints[i - 2]
            d = haversine_m(prev[0], prev[1], lat, lon)
            dist_str = f" (walked {d:.0f}m)"

        # Fix 2: compute heading from waypoint movement
        heading = _travel_heading(waypoints, i - 1)

        print(f"[Update {i}] User at {lat:.4f}, {lon:.4f}{dist_str}  heading {heading:.0f}°")

        key    = _grid_key(lat, lon)
        cached = _area_cache.get(key)

        if cached:
            cache_hits += 1
            print("  → Same area (cache hit)")
            visible, blocked = recast_from_cache(lat, lon, cached, heading=heading)
            n_total = len(cached["pois"])
        else:
            cache_misses += 1
            print(f"  → New area detected (cache miss)")
            print(f"  Fetching POIs: 1 credit")
            result = process_new_area(lat, lon, heading=heading)
            _area_cache[key] = result
            visible  = result["visible"]
            blocked  = result["blocked"]
            n_total  = len(result["pois"])

        print(f"  → {n_total} POIs checked, "
              f"{len(visible)} visible, {len(blocked)} blocked")

        def _fmt(p, u_lat, u_lon, marker, extra=""):
            d    = haversine_m(u_lat, u_lon, p["lat"], p["lon"])
            bear = bearing_to(u_lat, u_lon, p["lat"], p["lon"])
            name = p.get("name") or "Unknown"
            cats = p.get("categories", [])
            cat  = cats[0] if cats else "—"
            vis  = p.get("_visibility", "")
            coord = f"({p['lat']:.5f}, {p['lon']:.5f})"
            line  = (f"    {marker}  {name:<44} "
                     f"{d:>6.0f}m  {bear:>5.1f}°  {coord}  [{cat}] [{vis}]")
            if extra:
                line += f"\n         reason: {extra}"
            return line

        visible_sorted = sorted(
            visible, key=lambda p: haversine_m(lat, lon, p["lat"], p["lon"])
        )
        print(f"\n  VISIBLE ({len(visible_sorted)}):"
              f"  (name | dist | bearing | coords | category | how)")
        for p in visible_sorted:
            print(_fmt(p, lat, lon, "✓ "))
        if not visible_sorted:
            print("    (none)")

        blocked_sorted = sorted(
            blocked, key=lambda p: haversine_m(lat, lon, p["lat"], p["lon"])
        )
        print(f"\n  BLOCKED ({len(blocked_sorted)}):"
              f"  (name | dist | bearing | coords | category | reason)")
        for p in blocked_sorted:
            print(_fmt(p, lat, lon, "✗ ", extra=p.get("_blocked_by") or "unknown"))
        if not blocked_sorted:
            print("    (none)")

        print(f"\n  → {credits.summary()}")
        print()
        time.sleep(0.5)

    print("=" * 60)
    print("=== SUMMARY ===")
    print(f"Total updates:          {NUM_UPDATES}")
    print(f"Cache hits:             {cache_hits}")
    print(f"Cache misses:           {cache_misses}")
    print(f"Total credits used:     {credits.used}")
    print(f"Credits remaining:      {credits.remaining:,}")

    if credits.used > 0:
        capacity = credits.daily_limit // credits.used
        print(f"Estimated daily capacity: ~{capacity} similar sessions")

    print("=" * 60)
    print()
    print(f"Building geometry cache: {len(_bldg_cache)} unique IDs "
          f"({sum(1 for v in _bldg_cache.values() if v is not None)} with polygons)")
    print(f"Area cache:              {len(_area_cache)} grid cells")

    if not SHAPELY:
        print("\nNOTE: pip install shapely")
    if not PYPROJ:
        print("NOTE: pip install pyproj")


if __name__ == "__main__":
    run_simulation()
