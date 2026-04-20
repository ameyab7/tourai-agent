# utils/visibility.py
#
# Geometric visibility filter with confidence scoring.
#
# Pre-computes size, distance, angle, occlusion, and aspect ratio for each POI,
# then applies deterministic rules with confidence weighting.
#
# Public API:
#   filter_visible(pois, user_lat, user_lon, user_heading, user_street) → (visible, rejected)
#
# Each returned POI gains:
#   distance_m  — metres from user
#   angle_deg   — degrees off user heading (0=ahead, 180=behind)
#   confidence  — float 0.0–1.0
#   blocked_by  — list of closer building names in the same sightline

import logging
import math
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _polygon_area_m2(pts_m: list[tuple[float, float]]) -> float:
    """Shoelace formula on pre-projected (x_m, y_m) points."""
    n = len(pts_m)
    if n < 3:
        return 0.0
    return abs(sum(
        pts_m[i][0] * pts_m[(i + 1) % n][1] - pts_m[(i + 1) % n][0] * pts_m[i][1]
        for i in range(n)
    )) / 2


def _project_geometry(geometry: list[dict]) -> list[tuple[float, float]]:
    """Convert lon/lat geometry to flat (x_m, y_m) relative to first point."""
    if not geometry:
        return []
    lat0 = geometry[0]["lat"]
    R    = 6_371_000
    return [
        (
            math.radians(c["lon"] - geometry[0]["lon"]) * R * math.cos(math.radians(lat0)),
            math.radians(c["lat"] - geometry[0]["lat"]) * R,
        )
        for c in geometry
    ]


def _bounding_box_dims(pts_m: list[tuple[float, float]]) -> tuple[float, float]:
    """Return (width_m, depth_m) of axis-aligned bounding box."""
    if not pts_m:
        return 0.0, 0.0
    xs = [p[0] for p in pts_m]
    ys = [p[1] for p in pts_m]
    return max(xs) - min(xs), max(ys) - min(ys)


def _parse_height(value) -> float | None:
    try:
        return float(str(value).lower().replace("m", "").replace("ft", "").strip())
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Street name normalisation
#
# Strips directional prefixes and common suffix abbreviations so that
# "N Main Street" == "Main St" == "Main".
# ---------------------------------------------------------------------------

_STREET_SUFFIXES = re.compile(
    r"\b(street|st|avenue|ave|boulevard|blvd|road|rd|drive|dr|lane|ln|"
    r"court|ct|place|pl|way|wy|circle|cir|trail|trl|parkway|pkwy)\b",
    re.IGNORECASE,
)
_DIRECTIONAL = re.compile(r"^(north|south|east|west|n|s|e|w)\s+", re.IGNORECASE)


def _normalize_street(name: str) -> str:
    name = name.strip().lower()
    name = _DIRECTIONAL.sub("", name)
    name = _STREET_SUFFIXES.sub("", name)
    return name.strip()


def _streets_match(a: str, b: str) -> bool:
    """True if two street names refer to the same street."""
    na, nb = _normalize_street(a), _normalize_street(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


# ---------------------------------------------------------------------------
# Size classification
#
# Categories:  very_large | large | medium | small
# Priority:    floors > explicit height > footprint area > tag heuristics
#
# Default for unlabelled buildings is "medium" (not "small") to avoid
# false negatives when OSM data is incomplete.
# ---------------------------------------------------------------------------

_TAG_SIZES: dict[str, str] = {
    # very_large
    "stadium":          "very_large",
    "arena":            "very_large",
    # large
    "cathedral":        "large",
    "university":       "large",
    "college":          "large",
    # medium — notable civic / cultural buildings
    "theatre":          "medium",
    "museum":           "medium",
    "attraction":       "medium",
    "gallery":          "medium",
    "place_of_worship": "medium",
    "church":           "medium",
    "chapel":           "medium",
    "synagogue":        "medium",
    "mosque":           "medium",
    "temple":           "medium",
    "arts_centre":      "medium",
    "concert_hall":     "medium",
    "opera_house":      "medium",
    "library":          "medium",
    "townhall":         "medium",
    "courthouse":       "medium",
    "monument":         "medium",
    "castle":           "medium",
    "memorial":         "medium",
    # small — street-level establishments (explicit so they don't hit the medium default)
    "cafe":             "small",
    "restaurant":       "small",
    "bar":              "small",
    "pub":              "small",
    "fast_food":        "small",
    "food_court":       "small",
    "kiosk":            "small",
    "shop":             "small",
    "convenience":      "small",
    "atm":              "small",
    # small — street-level art objects (statues, sculptures, installations)
    "artwork":          "small",
    "sculpture":        "small",
    "fountain":         "small",
}

# POI types that get a 1.5× distance multiplier
_LANDMARK_TYPES: frozenset[str] = frozenset({
    "stadium", "arena", "tower", "cathedral",
    "monument", "memorial", "castle",
})


def _size_category(tags: dict, geometry: list[dict]) -> str:
    # 1. Floor count
    try:
        floors = int(tags.get("building:levels", 0))
        if floors >= 30: return "very_large"
        if floors >= 10: return "large"
        if floors >= 4:  return "medium"
    except (ValueError, TypeError):
        pass

    # 2. Explicit height tag
    h = _parse_height(tags.get("height"))
    if h is not None:
        if h >= 100: return "very_large"
        if h >= 30:  return "large"
        if h >= 12:  return "medium"

    # 3. Polygon footprint area
    if geometry:
        pts = _project_geometry(geometry)
        area = _polygon_area_m2(pts)
        if area >= 10_000: return "very_large"
        if area >= 3_000:  return "large"
        if area >= 500:    return "medium"

    # 4. Tag heuristics
    for key in ("amenity", "building", "leisure", "tourism"):
        val = tags.get(key, "")
        if val in _TAG_SIZES:
            return _TAG_SIZES[val]

    # 5. Default: medium (not small) — safer for unlabelled buildings
    return "medium"


def _is_landmark(poi_type: str) -> bool:
    return poi_type in _LANDMARK_TYPES


# ---------------------------------------------------------------------------
# Aspect ratio — narrow-side penalty
#
# If the user is viewing a building along its narrow dimension (viewing angle
# roughly perpendicular to the long axis), confidence is reduced by 0.3×.
# Only applied when polygon geometry is available.
# ---------------------------------------------------------------------------

def _aspect_confidence_penalty(
    geometry:  list[dict],
    angle_deg: float,       # user bearing relative to heading
    user_bear: float,       # absolute compass bearing to POI
) -> float:
    """Return a confidence multiplier in (0.7, 1.0].

    1.0 → viewing the wide face; 0.7 → viewing the narrow face.
    """
    if len(geometry) < 3:
        return 1.0

    pts = _project_geometry(geometry)
    width, depth = _bounding_box_dims(pts)

    if depth == 0 or width == 0:
        return 1.0

    ratio = max(width, depth) / min(width, depth)
    if ratio < 3.0:
        return 1.0   # not elongated — no penalty

    # The long axis of the bounding box
    long_axis_deg = 0.0 if width >= depth else 90.0

    # Angular difference between user's approach direction and long axis
    # If viewing nearly parallel to long axis → wide face → no penalty
    # If viewing nearly perpendicular → narrow face → penalty
    approach = user_bear % 180        # fold to [0, 180)
    diff = abs((approach - long_axis_deg + 90) % 180 - 90)   # [0, 90]

    if diff > 60:      # viewing narrow face
        return 0.7
    return 1.0


# ---------------------------------------------------------------------------
# Occlusion hints
# ---------------------------------------------------------------------------

def _add_occlusion_hints(pois: list[dict]) -> list[dict]:
    sorted_pois = sorted(pois, key=lambda p: p["distance_m"])
    for i, poi in enumerate(sorted_pois):
        poi["blocked_by"] = [
            closer["name"]
            for closer in sorted_pois[:i]
            if abs(closer["angle_deg"] - poi["angle_deg"]) < 15
            and closer["_size"] in ("large", "very_large")
        ]
    return sorted_pois


# ---------------------------------------------------------------------------
# Angle-based confidence multiplier
# ---------------------------------------------------------------------------

def _angle_confidence(angle_deg: float) -> float:
    if angle_deg < 20:  return 1.0
    if angle_deg < 60:  return 0.7
    return 0.3


# ---------------------------------------------------------------------------
# Core visibility decision
# ---------------------------------------------------------------------------

def _is_visible(
    size:          str,
    distance_m:    float,
    angle_deg:     float,
    same_street:   bool,
    blocked_by:    list,
    poi_type:      str,
    aspect_conf:   float = 1.0,   # from _aspect_confidence_penalty
    cross_street:  bool  = False,  # True when addr:street is known and doesn't match user's street
) -> tuple[bool, float]:
    """Return (is_visible, confidence ∈ [0, 1])."""

    in_fov     = angle_deg < 60
    angle_conf = _angle_confidence(angle_deg)

    # ── Proximity overrides ──────────────────────────────────────────────────
    if distance_m < 30 and in_fov:
        return (True, round(0.95 * aspect_conf, 2))

    if distance_m < 50 and in_fov and size in ("small", "medium"):
        return (True, round(0.90 * aspect_conf, 2))

    # ── Cross-street suppression for medium POIs ─────────────────────────────
    # If OSM explicitly says this POI is on a different street, it is behind
    # buildings between the streets and not directly visible, regardless of
    # what category it is.  large/very_large are tall enough to see over those
    # buildings, so they are left to the normal distance rules.
    if cross_street and size == "medium":
        return (False, 0.85)

    # ── Occlusion override ───────────────────────────────────────────────────
    if blocked_by and size in ("small", "medium") and distance_m > 100:
        return (False, 0.90)

    # ── Landmark distance multiplier ─────────────────────────────────────────
    dist_mult = 1.5 if _is_landmark(poi_type) else 1.0

    # ── Size + distance rules ────────────────────────────────────────────────
    if size == "very_large":
        if in_fov:
            visible = distance_m < 1500 * dist_mult
            conf    = 0.95 * angle_conf if visible else 0.9
        else:
            visible = distance_m < 800 * dist_mult
            conf    = 0.75 * angle_conf if visible else 0.85
        return (visible, round(conf * aspect_conf, 2))

    if size == "large":
        visible = in_fov and distance_m < 600 * dist_mult
        if not visible:
            return (False, 0.85)
        conf = 0.85 * angle_conf
        if distance_m > 450:
            conf *= 0.8
        return (True, round(conf * aspect_conf, 2))

    if size == "medium":
        visible = in_fov and distance_m < 250 * dist_mult
        if not visible:
            return (False, 0.80)
        conf = 0.80 * angle_conf
        if distance_m > 180:
            conf *= 0.8
        return (True, round(conf * aspect_conf, 2))

    # small
    visible = in_fov and distance_m < 80 and same_street
    if not visible:
        return (False, 0.75)
    return (True, round(0.75 * angle_conf * aspect_conf, 2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_visible(
    pois:         list[dict],
    user_lat:     float,
    user_lon:     float,
    user_heading: float,
    user_street:  str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Filter POIs by geometric visibility rules with confidence scoring.

    Each returned POI gains: distance_m, angle_deg, confidence, blocked_by.
    Returns (visible, rejected), both sorted by distance_m ascending.
    """
    from utils.geoutils import haversine_meters, bearing

    if not pois:
        return [], []

    # ── Step 1: enrich ───────────────────────────────────────────────────────
    enriched = []
    for poi in pois:
        poi_lat = poi.get("lat", user_lat)
        poi_lon = poi.get("lon", user_lon)

        dist      = haversine_meters(user_lat, user_lon, poi_lat, poi_lon)
        bear      = bearing(user_lat, user_lon, poi_lat, poi_lon)
        angle     = (bear - user_heading + 360) % 360
        if angle > 180:
            angle = 360 - angle

        tags     = poi.get("tags", {})
        geometry = poi.get("geometry", [])
        size     = _size_category(tags, geometry)

        # Same-street check with normalisation
        poi_street = tags.get("addr:street", "").strip()
        if poi_street:
            same_street  = _streets_match(user_street or "", poi_street)
            cross_street = not same_street   # OSM confirmed a different street
        else:
            # No addr:street in OSM — assume same street if very close
            same_street  = dist < 100
            cross_street = False             # unknown — don't penalise

        # poi_type for landmark detection — historic/man_made first so that
        # a memorial or tower with tourism=attraction also gets landmark boost
        poi_type = (
            tags.get("historic") or tags.get("man_made") or
            tags.get("tourism") or tags.get("amenity") or
            tags.get("leisure") or tags.get("building") or ""
        )

        # Aspect ratio confidence penalty
        aspect_conf = _aspect_confidence_penalty(geometry, angle, bear)

        enriched.append({
            **poi,
            "distance_m":    round(dist, 1),
            "angle_deg":     round(angle, 1),
            "_size":         size,
            "_same_street":  same_street,
            "_cross_street": cross_street,
            "_poi_type":     poi_type,
            "_aspect_conf":  aspect_conf,
        })

    # ── Step 2: occlusion ────────────────────────────────────────────────────
    enriched = _add_occlusion_hints(enriched)

    # ── Step 3: visibility decision ──────────────────────────────────────────
    visible:  list[dict] = []
    rejected: list[dict] = []

    for poi in enriched:
        size         = poi.pop("_size")
        same_street  = poi.pop("_same_street")
        cross_street = poi.pop("_cross_street")
        poi_type     = poi.pop("_poi_type")
        aspect_conf  = poi.pop("_aspect_conf")

        is_vis, conf = _is_visible(
            size         = size,
            distance_m   = poi["distance_m"],
            angle_deg    = poi["angle_deg"],
            same_street  = same_street,
            blocked_by   = poi.get("blocked_by", []),
            poi_type     = poi_type,
            aspect_conf  = aspect_conf,
            cross_street = cross_street,
        )

        poi["confidence"] = conf

        if is_vis:
            visible.append(poi)
        else:
            rejected.append({**poi, "filtered_reason": "not visible"})

    visible.sort(key=lambda p: p["distance_m"])
    rejected.sort(key=lambda p: p["distance_m"])
    return visible, rejected
