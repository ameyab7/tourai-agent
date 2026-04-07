# nodes/significance.py
#
# Scores a POI on how relevant and significant it is for a specific user.
#
# The final score (0.0 - 1.0) is a weighted blend of three components:
#
#   base_significance (40%) — How well-documented is this place?
#     Rewards POIs with rich OSM tags (description, wikipedia, etc.)
#     and large Wikipedia articles (longer = more notable landmark).
#
#   interest_match (40%) — Does this place match what the user cares about?
#     Maps OSM tag types to interest categories (history, art, architecture...)
#     and looks up the user's weight for that category.
#
#   proximity_bonus (20%) — How close is the user to this POI?
#     Linear decay from 1.0 at 0m to 0.0 at the edge of the search radius.
#
# Final score = (base × 0.4) + (interest × 0.4) + (proximity × 0.2)

import math
import logging

logger = logging.getLogger(__name__)

# OSM tags that indicate a well-documented, notable place
RICH_TAGS = {"description", "wikipedia", "wikidata", "website", "image", "architect", "start_date", "heritage"}

# Maps OSM tag type + value patterns to user interest categories
# Format: (poi_type, tag_value_substring_or_None) -> interest_category
# None means "any value of this poi_type"
_INTEREST_MAP: list[tuple[str, str | None, str]] = [
    ("historic",  None,          "history"),
    ("tourism",   "museum",      "history"),
    ("tourism",   "artwork",     "art"),
    ("tourism",   "gallery",     "art"),
    ("tourism",   "viewpoint",   "photography"),
    ("tourism",   "attraction",  "sightseeing"),
    ("tourism",   "hotel",       "sightseeing"),
    ("amenity",   "theatre",     "performing_arts"),
    ("amenity",   "cinema",      "performing_arts"),
    ("amenity",   "arts_centre", "art"),
    ("amenity",   "library",     "history"),
    ("amenity",   "place_of_worship", "spirituality"),
    ("building",  "cathedral",   "architecture"),
    ("building",  "church",      "architecture"),
    ("building",  "civic",       "architecture"),
    ("building",  "government",  "architecture"),
    ("building",  "skyscraper",  "architecture"),
    ("leisure",   "park",        "nature"),
    ("leisure",   "garden",      "nature"),
    ("natural",   "peak",        "nature"),
    ("man_made",  "lighthouse",  "architecture"),
]

# Weights for the three scoring components
_W_SIGNIFICANCE = 0.4
_W_INTEREST     = 0.4
_W_PROXIMITY    = 0.2

# Maximum wiki article length used for normalization
_WIKI_LENGTH_CAP = 10_000

# Default interest weight when no category matches the user's profile
_DEFAULT_INTEREST = 0.2


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the distance in meters between two GPS coordinates."""
    R = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _base_significance(poi: dict, wiki_content_length: int) -> float:
    """Score how well-documented this POI is (0.0 - 1.0)."""
    tags = poi.get("tags", {})
    rich_count = sum(1 for t in RICH_TAGS if t in tags)
    tag_score = min(rich_count / 5, 1.0)

    wiki_score = min(wiki_content_length / _WIKI_LENGTH_CAP, 0.5)

    # Combine: tag_score contributes up to 0.5, wiki_score up to 0.5 → max 1.0
    return min((tag_score * 0.5) + wiki_score, 1.0)


def _interest_match(poi: dict, interest_profile: dict) -> float:
    """Score how well this POI matches the user's interests (0.0 - 1.0)."""
    poi_type = poi.get("poi_type", "")
    tags = poi.get("tags", {})

    # Collect the tag value for the matched poi_type (e.g. "museum", "artwork")
    tag_value = tags.get(poi_type, "")

    best_weight = _DEFAULT_INTEREST

    for map_type, map_value, category in _INTEREST_MAP:
        if map_type != poi_type:
            continue
        # None means any value of this poi_type matches
        if map_value is not None and map_value not in tag_value:
            continue
        weight = interest_profile.get(category, _DEFAULT_INTEREST)
        if weight > best_weight:
            best_weight = weight

    return best_weight


def _proximity_bonus(
    user_lat: float, user_lon: float,
    poi_lat: float, poi_lon: float,
    search_radius: float,
) -> float:
    """Linear decay from 1.0 at 0m to 0.0 at search_radius (0.0 - 1.0)."""
    if search_radius <= 0:
        return 0.0
    distance = _haversine_meters(user_lat, user_lon, poi_lat, poi_lon)
    return max(0.0, 1.0 - (distance / search_radius))


def score_poi(
    poi: dict,
    interest_profile: dict,
    user_lat: float,
    user_lon: float,
    search_radius: float,
    wiki_content_length: int,
) -> float:
    """Score a POI for relevance to a specific user at a specific location.

    Args:
        poi: POI dict with keys: id, name, lat, lon, tags, poi_type.
        interest_profile: Dict mapping interest category to weight (0.0-1.0),
            e.g. {"history": 0.8, "architecture": 0.9}.
        user_lat: User's current latitude.
        user_lon: User's current longitude.
        search_radius: The radius (meters) used to find this POI — sets the
            proximity decay boundary.
        wiki_content_length: Character count of the full Wikipedia article
            for this POI (0 if no article found).

    Returns:
        Float score in range 0.0 - 1.0.

    Raises:
        ValueError: If poi is missing required keys or search_radius is not positive.
    """
    required_keys = {"name", "lat", "lon", "tags", "poi_type"}
    missing = required_keys - poi.keys()
    if missing:
        raise ValueError(f"POI dict is missing required keys: {missing}")
    if search_radius <= 0:
        raise ValueError(f"search_radius must be positive, got {search_radius}")

    sig   = _base_significance(poi, wiki_content_length)
    match = _interest_match(poi, interest_profile)
    prox  = _proximity_bonus(user_lat, user_lon, poi["lat"], poi["lon"], search_radius)

    score = (sig * _W_SIGNIFICANCE) + (match * _W_INTEREST) + (prox * _W_PROXIMITY)

    logger.debug(
        "score_poi '%s': significance=%.2f interest=%.2f proximity=%.2f → %.2f",
        poi["name"], sig, match, prox, score,
    )

    return round(score, 4)
