"""utils/poi_ranker.py — Shared POI scoring and ranking used by recommendations + itinerary."""

import math

# POI type → interest categories it satisfies
TYPE_TO_INTERESTS: dict[str, set[str]] = {
    "park":               {"nature", "hiking", "photography", "social"},
    "nature_reserve":     {"nature", "hiking", "photography"},
    "garden":             {"nature", "photography", "relaxed"},
    "viewpoint":          {"photography", "nature", "architecture"},
    "peak":               {"hiking", "nature", "photography"},
    "beach":              {"nature", "photography", "social"},
    "museum":             {"history", "culture", "architecture"},
    "art_gallery":        {"culture", "photography", "architecture"},
    "gallery":            {"culture", "photography"},
    "theatre":            {"culture", "social"},
    "cinema":             {"culture", "social"},
    "library":            {"culture", "history"},
    "historic":           {"history", "architecture", "photography"},
    "monument":           {"history", "architecture", "photography"},
    "memorial":           {"history"},
    "castle":             {"history", "architecture", "photography"},
    "ruins":              {"history", "photography"},
    "archaeological_site":{"history"},
    "restaurant":         {"food", "social"},
    "cafe":               {"food", "social"},
    "bakery":             {"food"},
    "pub":                {"social", "food"},
    "bar":                {"social"},
    "marketplace":        {"food", "social", "shopping"},
    "mall":               {"shopping"},
    "sports_centre":      {"sports"},
    "stadium":            {"sports", "social"},
    "swimming_pool":      {"sports"},
    "pitch":              {"sports"},
    "attraction":         {"culture", "photography"},
    "artwork":            {"culture", "photography", "architecture"},
    "theme_park":         {"social", "culture"},
    "aquarium":           {"nature", "culture"},
    "zoo":                {"nature", "culture"},
    "winery":             {"food", "social"},
    "brewery":            {"food", "social"},
    "tower":              {"architecture", "photography"},
    "culture":            {"culture", "history"},
}

# Intrinsic destination quality — how "worth visiting" a POI type is regardless of interests
_TIER: dict[str, float] = {
    "museum":             3.0,
    "art_gallery":        3.0,
    "castle":             3.0,
    "archaeological_site":3.0,
    "viewpoint":          2.5,
    "peak":               2.5,
    "nature_reserve":     2.5,
    "beach":              2.5,
    "theatre":            2.0,
    "zoo":                2.0,
    "aquarium":           2.0,
    "winery":             2.0,
    "ruins":              2.0,
    "memorial":           1.5,
    "tower":              1.5,
    "park":               1.5,
    "attraction":         1.0,
    "theme_park":         1.0,
    "artwork":            1.0,
    "stadium":            1.0,
    "brewery":            1.0,
    "culture":            1.0,
    "cinema":             0.5,
}


def poi_interests(poi: dict) -> set[str]:
    """Derive interest categories from a POI's type and optional OSM tags."""
    cats: set[str] = set()
    poi_type = poi.get("poi_type", "").lower()
    cats |= TYPE_TO_INTERESTS.get(poi_type, set())

    tags = poi.get("tags", {})
    for key in ("tourism", "amenity", "leisure", "historic", "natural"):
        val = tags.get(key, "").lower()
        if val:
            cats |= TYPE_TO_INTERESTS.get(val, set())

    return cats


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R  = 6371.0
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a  = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def score_poi(
    poi: dict,
    interests: list[str],
    user_lat: float,
    user_lon: float,
) -> float:
    """
    Score a POI for itinerary ranking.

    Factors:
      +3 per matched user interest
      +tier score (intrinsic destination quality)
      -0.08 per km distance (slight proximity preference)
    """
    cats          = poi_interests(poi)
    interest_set  = set(interests)
    interest_score = len(cats & interest_set) * 3.0
    tier_score     = _TIER.get(poi.get("poi_type", ""), 0.5)
    dist_km        = _haversine_km(user_lat, user_lon, poi.get("lat", user_lat), poi.get("lon", user_lon))
    dist_penalty   = dist_km * 0.08
    return interest_score + tier_score - dist_penalty


def rank_pois(
    pois: list[dict],
    interests: list[str],
    user_lat: float,
    user_lon: float,
    limit: int = 10,
    max_per_type: int = 3,
) -> list[dict]:
    """
    Sort POIs by relevance to the user and return the top `limit`.

    Diversity cap: no more than `max_per_type` of any single poi_type in the
    result, so the model sees variety rather than 10 parks.
    """
    scored = sorted(pois, key=lambda p: score_poi(p, interests, user_lat, user_lon), reverse=True)

    type_counts: dict[str, int] = {}
    result: list[dict] = []
    for poi in scored:
        t = poi.get("poi_type", "other")
        if type_counts.get(t, 0) < max_per_type:
            result.append(poi)
            type_counts[t] = type_counts.get(t, 0) + 1
        if len(result) >= limit:
            break

    return result
