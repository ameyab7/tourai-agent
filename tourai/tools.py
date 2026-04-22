# tools.py
#
# All 8 LangGraph ReAct tools for the TourAI agent.
# Tools are sync functions — async utils are bridged via a thread pool executor
# so asyncio.run() gets its own event loop per call without conflicting with
# LangGraph's own event loop.

import asyncio
import concurrent.futures
import json
import logging
import math
import os
import re
import time
from datetime import datetime

import httpx
from langchain_core.tools import tool

from utils.overpass import search_nearby, OverpassError
from utils.wikipedia import get_wikipedia_summary, WikipediaError
from utils.weather import get_current_weather, WeatherError
from utils.tts import synthesize, TTSError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async-to-sync bridge
# ---------------------------------------------------------------------------

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _run(coro):
    """Run an async coroutine safely from a sync context."""
    future = _executor.submit(asyncio.run, coro)
    return future.result()


# ---------------------------------------------------------------------------
# Session memory (module-level, lives for the duration of the process)
# ---------------------------------------------------------------------------

_session_stories: list[dict] = []

# ---------------------------------------------------------------------------
# API usage tracking
# ---------------------------------------------------------------------------

_api_usage: dict[str, int] = {"overpass": 0}


def get_api_usage() -> dict:
    """Return a snapshot of API call counts for this process session."""
    return dict(_api_usage)

# ---------------------------------------------------------------------------
# POI cache — keyed on (grid_cell, frozenset(tags)), TTL = 5 min
# ---------------------------------------------------------------------------

_poi_cache: dict[tuple, tuple[float, str]] = {}
_POI_CACHE_TTL = 300  # seconds
_GRID_STEP = 0.0005  # ~55m per cell


def _grid_cell(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat / _GRID_STEP) * _GRID_STEP, round(lon / _GRID_STEP) * _GRID_STEP)


# ---------------------------------------------------------------------------
# Weather cache — TTL = 30 min
# ---------------------------------------------------------------------------

_weather_cache: dict[tuple, tuple[float, str]] = {}
_WEATHER_CACHE_TTL = 1800  # seconds

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _point_to_segment_dist_meters(
    p_lat: float, p_lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float,
) -> float:
    """Perpendicular distance in meters from point P to line segment AB.
    Uses flat-earth approximation (valid for distances < 10km).
    """
    lat_m = 111320.0
    lon_m = 111320.0 * math.cos(math.radians((a_lat + b_lat) / 2))

    # Translate so A is origin
    bx = (b_lon - a_lon) * lon_m
    by = (b_lat - a_lat) * lat_m
    px = (p_lon - a_lon) * lon_m
    py = (p_lat - a_lat) * lat_m

    seg_len_sq = bx * bx + by * by
    if seg_len_sq < 1e-10:
        return math.hypot(px, py)

    t = max(0.0, min(1.0, (px * bx + py * by) / seg_len_sq))
    return math.hypot(px - t * bx, py - t * by)


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing in degrees (0=N, 90=E, 180=S, 270=W) from point 1 to point 2."""
    dlon = math.radians(lon2 - lon1)
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _angle_diff(a: float, b: float) -> float:
    """Smallest angular difference between two bearings (0-180)."""
    return min(abs(a - b) % 360, 360 - abs(a - b) % 360)


def _parse_tag_filter(tag: str) -> str:
    """Convert a tag string like 'tourism~museum|attraction' into valid Overpass QL.

    Input formats:
      'tourism~museum|attraction'  → ["tourism"~"museum|attraction"]
      'historic~*'                 → ["historic"]   (wildcard = key-existence check)
      'historic'                   → ["historic"]

    Defensive handling:
      'historic~*|tourism~museum'  → ["historic"]   (agent concatenated multi-key tags;
                                      detected by '~' inside pipe-separated parts)
    """
    if "~" in tag:
        key, value_regex = tag.split("~", 1)
        value_regex = value_regex.strip()

        # '*' / '.*' / '.+' mean "any value" — Overpass regex doesn't accept bare '*'
        # Use a key-existence filter instead, which is both valid and faster.
        if value_regex in ("*", ".*", ".+", ""):
            return f'["{key.strip()}"]'

        # Detect agent concatenating multiple key~value pairs into one string.
        # e.g. "*|tourism~museum|amenity~restaurant" — each pipe-part contains "~".
        # This is invalid Overpass QL. Fall back to key-existence for the first key.
        if any("~" in part for part in value_regex.split("|")):
            logger.warning(
                "_parse_tag_filter: concatenated multi-key tag detected '%s' — "
                "using key-existence check for '%s' only. "
                "Tags must be separate list items, not joined with '|'.", tag, key.strip()
            )
            return f'["{key.strip()}"]'

        return f'["{key.strip()}"~"{value_regex}"]'
    return f'["{tag.strip()}"]'


def _build_custom_query(lat: float, lon: float, radius: int, tags: list[str]) -> str:
    filters = ""
    for tag in tags:
        tag_filter = _parse_tag_filter(tag)
        filters += f'  node(around:{radius},{lat},{lon}){tag_filter};\n'
        filters += f'  way(around:{radius},{lat},{lon}){tag_filter};\n'
    return f"""[out:json][timeout:10];
(
{filters});
out center tags;
"""


_OVERPASS_URL         = "https://overpass-api.de/api/interpreter"
_CUSTOM_QUERY_TIMEOUT = 8
_CUSTOM_MAX_RETRIES   = 3
_CUSTOM_RETRY_BACKOFF = [2, 5, 10]


async def _run_custom_query(query: str) -> list:
    """Execute a custom Overpass QL query with retry logic on 5xx errors.

    Raises:
        httpx.HTTPStatusError: immediately on 4xx (bad query syntax)
        OverpassError: after all retries exhausted on 5xx / timeout
    """
    import asyncio as _asyncio
    from utils.overpass import OverpassError

    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=_CUSTOM_QUERY_TIMEOUT) as client:
        for attempt in range(_CUSTOM_MAX_RETRIES):
            try:
                resp = await client.post(_OVERPASS_URL, data={"data": query})
                if resp.status_code == 400:
                    logger.error("Overpass 400 Bad Request. Query sent:\n%s", query)
                resp.raise_for_status()
                return resp.json().get("elements", [])
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise  # 4xx — bad query, no point retrying
                last_error = e
                logger.warning(
                    "Overpass HTTP %d on custom query (attempt %d/%d)\n"
                    "  Response body: %s",
                    e.response.status_code, attempt + 1, _CUSTOM_MAX_RETRIES,
                    e.response.text[:500],
                )
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "Overpass timeout on custom query (attempt %d/%d)",
                    attempt + 1, _CUSTOM_MAX_RETRIES,
                )

            if attempt < _CUSTOM_MAX_RETRIES - 1:
                wait = _CUSTOM_RETRY_BACKOFF[attempt]
                logger.info("Retrying Overpass in %ds...", wait)
                await _asyncio.sleep(wait)

    raise OverpassError(
        f"Overpass custom query failed after {_CUSTOM_MAX_RETRIES} attempts: {last_error}"
    )


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text).strip("_")[:40]


# ---------------------------------------------------------------------------
# Tool 1: search_pois
# ---------------------------------------------------------------------------

@tool
def search_pois(
    latitude: float,
    longitude: float,
    radius: int = 150,
    tags: list[str] | None = None,
) -> str:
    """Search for points of interest near a location using OpenStreetMap.

    Use this to find landmarks, museums, historic sites, restaurants, etc.
    Control what to search for by setting tags (Overpass QL format like
    'tourism~museum|attraction' or 'amenity~restaurant|cafe').
    Widen radius for sparse areas or when the user is driving.
    Returns a list of POIs with id, name, coordinates, tags, and distance.
    """
    cache_key = (_grid_cell(latitude, longitude), frozenset(tags or []))
    now = time.time()

    # Check cache
    if cache_key in _poi_cache:
        cached_time, cached_result = _poi_cache[cache_key]
        if now - cached_time < _POI_CACHE_TTL:
            logger.debug("search_pois: cache hit for %s", cache_key)
            return f"[CACHED] {cached_result}"

    try:
        if tags:
            query = _build_custom_query(latitude, longitude, radius, tags)

            try:
                _api_usage["overpass"] += 1
                elements = _run(_run_custom_query(query))
            except Exception as e:
                if "400" in str(e):
                    # Bad custom tags — fall back to default tag set
                    logger.warning(
                        "Custom tag query failed with 400 — retrying with default tags. Tags were: %s", tags
                    )
                    _api_usage["overpass"] += 1
                    pois = _run(search_nearby(latitude, longitude, radius))
                    elements = []  # skip element parsing below; pois already set
                else:
                    raise
            else:
                pois = []
                for el in elements:
                    t = el.get("tags", {})
                    name = t.get("name")
                    if not name:
                        continue
                    if el["type"] == "way":
                        center = el.get("center", {})
                        plat, plon = center.get("lat"), center.get("lon")
                    else:
                        plat, plon = el.get("lat"), el.get("lon")
                    if plat is None or plon is None:
                        continue
                    pois.append({"id": el["id"], "name": name, "lat": plat, "lon": plon, "tags": t, "poi_type": "custom"})
        else:
            _api_usage["overpass"] += 1
            pois = _run(search_nearby(latitude, longitude, radius))

    except Exception as e:
        return f"ERROR: Could not fetch POIs — {e}"

    if not pois:
        result = f"No POIs found within {radius}m of ({latitude:.4f}, {longitude:.4f})."
        # Cache empty results for only 60s — bad tag syntax can cause false empties,
        # and we don't want that locking out a retry with correct tags for 5 minutes.
        _poi_cache[cache_key] = (now - _POI_CACHE_TTL + 60, result)
        return result

    lines = [f"Found {len(pois)} POIs within {radius}m of ({latitude:.4f}, {longitude:.4f}):\n"]
    raw_for_rank: list[dict] = []

    for i, poi in enumerate(pois, 1):
        dist = int(_haversine_meters(latitude, longitude, poi["lat"], poi["lon"]))
        tags_summary = {k: v for k, v in poi["tags"].items()
                        if k in ("tourism", "historic", "amenity", "leisure", "building",
                                 "man_made", "natural", "description", "wikipedia", "wikidata",
                                 "addr:street")}
        lines.append(
            f"{i}. {poi['name']}\n"
            f"   id={poi['id']} | type={poi['poi_type']} | distance={dist}m\n"
            f"   coords=({poi['lat']:.5f}, {poi['lon']:.5f})\n"
            f"   tags={json.dumps(tags_summary)}"
        )
        raw_for_rank.append({
            "id":   str(poi["id"]),
            "name": poi["name"],
            "lat":  poi["lat"],
            "lon":  poi["lon"],
            "tags": tags_summary,
        })

    # Append machine-readable JSON so the agent can pass it directly to rank_pois
    # without reconstructing coordinates from the human-readable text above.
    lines.append(f"\nRAW_JSON_FOR_RANK_POIS: {json.dumps(raw_for_rank)}")

    result = "\n".join(lines)
    _poi_cache[cache_key] = (now, result)
    return result


# ---------------------------------------------------------------------------
# Tool 2: enrich_poi
# ---------------------------------------------------------------------------

_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_WIKIDATA_HEADERS = {
    "User-Agent": "TourAI/1.0 (contact@tourai.app)",
    "Accept": "application/sparql-results+json",
}


def _fetch_wikidata_facts(qid: str) -> dict:
    """Query Wikidata for architectural/historical facts about a place."""
    sparql = f"""
SELECT ?architectLabel ?inception ?styleLabel ?height WHERE {{
  OPTIONAL {{ wd:{qid} wdt:P84 ?architect . }}
  OPTIONAL {{ wd:{qid} wdt:P571 ?inception . }}
  OPTIONAL {{ wd:{qid} wdt:P149 ?style . }}
  OPTIONAL {{ wd:{qid} wdt:P2048 ?height . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
"""
    try:
        resp = httpx.get(
            _WIKIDATA_SPARQL,
            params={"query": sparql, "format": "json"},
            headers=_WIKIDATA_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        bindings = resp.json().get("results", {}).get("bindings", [])
        if not bindings:
            return {}
        b = bindings[0]
        facts = {}
        if "architectLabel" in b:
            facts["architect"] = b["architectLabel"]["value"]
        if "inception" in b:
            facts["year_built"] = b["inception"]["value"][:4]
        if "styleLabel" in b:
            facts["architectural_style"] = b["styleLabel"]["value"]
        if "height" in b:
            facts["height_m"] = b["height"]["value"]
        return facts
    except Exception as e:
        logger.warning("Wikidata query failed for %s: %s", qid, e)
        return {}


@tool
def enrich_poi(
    poi_name: str,
    poi_tags: str = "",
    city: str = "",
    sources: str = "wikipedia,wikidata",
) -> str:
    """Get detailed information about a specific POI from Wikipedia and/or Wikidata.

    Use this to get historical context, architectural details, and fun facts
    before generating a story. Request specific sources via the sources parameter.
    Returns summary text, content length, structured facts (architect, year built,
    style), and whether data was found.
    Note: poi_tags is a JSON-serialized string of the OSM tags dict.
    """
    city_ctx = city or "Dallas Texas"
    source_list = [s.strip().lower() for s in sources.split(",")]
    lines = [f"Enrichment data for: {poi_name}\n"]

    # Wikipedia
    if "wikipedia" in source_list:
        try:
            wiki = _run(get_wikipedia_summary(poi_name, city=city_ctx))
            if wiki["found"]:
                lines.append(f"=== Wikipedia ===")
                lines.append(f"Title          : {wiki['title']}")
                lines.append(f"Content length : {wiki['content_length']:,} chars (significance signal)")
                lines.append(f"Thumbnail      : {wiki['thumbnail_url'] or 'none'}")
                lines.append(f"Summary        : {wiki['extract']}")
            else:
                lines.append("Wikipedia: No article found.")
        except (WikipediaError, ValueError) as e:
            lines.append(f"Wikipedia: ERROR — {e}")

    # Wikidata
    if "wikidata" in source_list:
        qid = None
        try:
            tags_dict = json.loads(poi_tags) if poi_tags else {}
            qid_raw = tags_dict.get("wikidata", "")
            if qid_raw and re.match(r"^Q\d+$", qid_raw):
                qid = qid_raw
        except (json.JSONDecodeError, AttributeError):
            pass

        if qid:
            facts = _fetch_wikidata_facts(qid)
            if facts:
                lines.append(f"\n=== Wikidata ({qid}) ===")
                for k, v in facts.items():
                    lines.append(f"{k.replace('_', ' ').title():<22}: {v}")
            else:
                lines.append(f"\nWikidata ({qid}): No structured facts found.")
        else:
            lines.append("\nWikidata: No Q-number in poi_tags — skipped.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: get_weather
# ---------------------------------------------------------------------------

@tool
def get_weather(latitude: float, longitude: float) -> str:
    """Get current weather conditions at a location.

    Use this to decide whether to recommend indoor vs outdoor POIs,
    or to mention weather context in your story.
    Returns condition, temperature, wind speed, and whether it's daylight.
    """
    cache_key = (_grid_cell(latitude, longitude),)
    now = time.time()

    if cache_key in _weather_cache:
        cached_time, cached_result = _weather_cache[cache_key]
        if now - cached_time < _WEATHER_CACHE_TTL:
            logger.debug("get_weather: cache hit")
            return f"[CACHED] {cached_result}"

    try:
        w = _run(get_current_weather(latitude, longitude))
    except (WeatherError, ValueError) as e:
        return f"ERROR: Could not fetch weather — {e}"

    result = (
        f"Current weather at ({latitude:.4f}, {longitude:.4f}):\n"
        f"  Condition    : {w['condition']}\n"
        f"  Temperature  : {w['temperature_c']}°C\n"
        f"  Feels like   : {w['feels_like_c']}°C\n"
        f"  Wind speed   : {w['wind_speed_kmh']} km/h\n"
        f"  Daylight     : {'Yes' if w['is_daylight'] else 'No'}\n"
        f"\nRecommendation: "
        + (
            "Great conditions for outdoor exploration." if w["condition"] == "clear" and w["is_daylight"]
            else "Consider recommending indoor POIs (museums, galleries)." if w["condition"] in ("rain", "snow")
            else "Mild conditions — both indoor and outdoor POIs work."
        )
    )

    _weather_cache[cache_key] = (now, result)
    return result


# ---------------------------------------------------------------------------
# Tool 4: get_user_profile
# ---------------------------------------------------------------------------

@tool
def get_user_profile(user_id: str) -> str:
    """Read the traveler's interest profile and preferences.

    Use this to understand what they care about so you can personalize
    searches and stories. Returns interest weights, preferred voice,
    story length preference, and cooldown setting.
    """
    profile = {
        "user_id": user_id,
        "interests": {
            "history":      0.9,
            "architecture": 0.8,
            "photography":  0.7,
            "food":         0.5,
            "art":          0.4,
            "nature":       0.3,
        },
        "preferred_voice":   "en-US-GuyNeural",
        "story_length":      "medium",   # short=60-80w, medium=80-120w, long=120-160w
        "cooldown_seconds":  5,
    }

    interests_str = "\n".join(
        f"    {cat:<15}: {int(w * 100)}%"
        for cat, w in sorted(profile["interests"].items(), key=lambda x: -x[1])
    )

    return (
        f"User profile for '{user_id}':\n"
        f"  Interests (ranked):\n{interests_str}\n"
        f"  Preferred voice  : {profile['preferred_voice']}\n"
        f"  Story length     : {profile['story_length']} (80-120 words)\n"
        f"  Cooldown         : {profile['cooldown_seconds']}s between stories\n"
    )


# ---------------------------------------------------------------------------
# Tool 5: get_session_history
# ---------------------------------------------------------------------------

@tool
def get_session_history(session_id: str) -> str:
    """Get the list of stories already told in this session.

    Use this to avoid repeating POIs, detect feedback patterns
    (multiple skips on a category), and thread narratives between stories.
    Returns poi names, feedback, listen percentage, and timestamps.
    """
    stories = [s for s in _session_stories if s.get("session_id") == session_id]

    if not stories:
        return "No stories told yet this session."

    lines = [f"Session history for '{session_id}' ({len(stories)} stories told):\n"]
    for i, s in enumerate(stories, 1):
        lines.append(
            f"{i}. {s['poi_name']} (id={s['poi_id']})\n"
            f"   Told at : {s['timestamp']}\n"
            f"   Location: ({s['latitude']:.4f}, {s['longitude']:.4f})\n"
            f"   Preview : {s['story_text'][:80]}..."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6: synthesize_audio
# ---------------------------------------------------------------------------

@tool
def synthesize_audio(text: str, voice: str = "en-US-GuyNeural") -> str:
    """Convert story text to natural speech audio using edge-tts.

    Choose voice based on context:
      en-US-GuyNeural   — warm American male (default)
      en-US-JennyNeural — warm American female
      en-GB-RyanNeural  — British male style
    Returns confirmation with file path and estimated duration.
    """
    if not text or not text.strip():
        return "ERROR: text is empty."

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"story_{timestamp}.mp3"
    filepath = os.path.join(OUTPUT_DIR, filename)

    try:
        audio_bytes = _run(synthesize(text, voice=voice))
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
    except (TTSError, ValueError) as e:
        return f"ERROR: TTS synthesis failed — {e}"
    except OSError as e:
        return f"ERROR: Could not write audio file — {e}"

    word_count = len(text.split())
    duration_seconds = int((word_count / 150) * 60)
    duration_str = f"{duration_seconds}s (~{word_count} words at 150 wpm)"

    return (
        f"Audio synthesized successfully.\n"
        f"  File     : {filepath}\n"
        f"  Voice    : {voice}\n"
        f"  Size     : {len(audio_bytes):,} bytes\n"
        f"  Duration : {duration_str}\n"
        f"  Play     : open \"{filepath}\""
    )


# ---------------------------------------------------------------------------
# Tool 7: rank_pois
# ---------------------------------------------------------------------------

# Maps OSM tag key-value patterns to interest categories
_TAG_INTEREST_MAP: list[tuple[str, str, str | None, str]] = [
    # (tag_key, tag_value_or_wildcard, value_match, interest_category)
    ("historic",  "*",          None,           "history"),
    ("tourism",   "museum",     None,           "history"),
    ("tourism",   "gallery",    None,           "art"),
    ("tourism",   "artwork",    None,           "art"),
    ("tourism",   "viewpoint",  None,           "photography"),
    ("tourism",   "attraction", None,           "history"),
    ("building",  "cathedral",  None,           "architecture"),
    ("building",  "church",     None,           "architecture"),
    ("building",  "chapel",     None,           "architecture"),
    ("amenity",   "restaurant", None,           "food"),
    ("amenity",   "cafe",       None,           "food"),
    ("amenity",   "bar",        None,           "food"),
    ("amenity",   "theatre",    None,           "local_culture"),
    ("leisure",   "park",       None,           "nature"),
    ("leisure",   "garden",     None,           "nature"),
    ("natural",   "*",          None,           "nature"),
]

_RICHNESS_TAGS = {
    "description", "wikipedia", "wikidata", "website", "image",
    "architect", "start_date", "heritage", "opening_hours",
}


def _score_interest_match(tags: dict, interests: dict) -> tuple[float, str]:
    """Return (score, matched_category) for a POI's tags vs user interests."""
    best_score = 0.2   # default — no match
    best_cat   = "no match"

    for key, value_pattern, _, category in _TAG_INTEREST_MAP:
        if key not in tags:
            continue
        tag_val = tags[key].lower()
        if value_pattern == "*" or tag_val == value_pattern:
            # tourism=museum can match both history and art — take the higher weight
            weight = interests.get(category, 0.0)
            if weight > best_score:
                best_score = weight
                best_cat   = category

    return best_score, best_cat


_MIN_SIGNIFICANCE = 0.15  # POIs below this score are not worth mentioning


@tool
def rank_pois(
    pois_json: str,
    user_interests_json: str,
    user_lat: float,
    user_lon: float,
    dest_lat: float = -999.0,
    dest_lon: float = -999.0,
) -> str:
    """Score and sort a list of POIs by relevance to the traveler. Call this after search_pois — it scores each POI on interest match, wiki notability, and tag richness, then sorts by distance. When dest_lat/dest_lon are provided, also calculates route_offset_m — how far each POI sits off the straight line to the destination. A low route_offset_m means the POI is directly on the traveler's path; a high value means it's on a different street or behind buildings. Always pass dest_lat/dest_lon from the GPS data when available."""
    try:
        pois: list[dict] = json.loads(pois_json)
        if not isinstance(pois, list):
            return "ERROR: pois_json must be a JSON array."
    except json.JSONDecodeError as e:
        return f"ERROR: Could not parse pois_json — {e}"

    try:
        interests: dict = json.loads(user_interests_json)
        if not isinstance(interests, dict):
            return "ERROR: user_interests_json must be a JSON object."
    except json.JSONDecodeError as e:
        return f"ERROR: Could not parse user_interests_json — {e}"

    if not pois:
        return "No POIs to rank."

    # Normalize interest values if passed as percentages (90 → 0.9)
    if interests and max(interests.values(), default=0) > 1.0:
        interests = {k: v / 100.0 for k, v in interests.items()}

    scored: list[dict] = []
    filtered_out: list[str] = []

    for poi in pois:
        tags: dict = poi.get("tags", {})
        poi_lat: float = poi.get("lat", user_lat)
        poi_lon: float = poi.get("lon", user_lon)

        richness_count = sum(1 for t in _RICHNESS_TAGS if t in tags)
        tag_richness = min(richness_count / 5.0, 1.0)

        interest_score, matched_cat = _score_interest_match(tags, interests)

        wiki_notability = 1.0 if ("wikipedia" in tags or "wikidata" in tags) else 0.0

        significance = (tag_richness * 0.3) + (interest_score * 0.5) + (wiki_notability * 0.2)

        if significance < _MIN_SIGNIFICANCE:
            filtered_out.append(poi.get("name", "Unknown"))
            continue

        dist_m = _haversine_meters(user_lat, user_lon, poi_lat, poi_lon)
        bearing = _bearing(user_lat, user_lon, poi_lat, poi_lon)

        route_offset_m = None
        if dest_lat > -999 and dest_lon > -999:
            route_offset_m = int(_point_to_segment_dist_meters(
                poi_lat, poi_lon,
                user_lat, user_lon,
                dest_lat, dest_lon,
            ))

        entry = {
            "name":           poi.get("name", "Unknown"),
            "id":             poi.get("id", ""),
            "distance_m":     int(dist_m),
            "bearing":        round(bearing, 1),
            "significance":   round(significance, 2),
            "interest_match": f"{interest_score:.1f} ({matched_cat})",
            "wiki":           "yes" if wiki_notability else "no",
        }
        if route_offset_m is not None:
            entry["route_offset_m"] = route_offset_m

        scored.append(entry)

    if not scored:
        return f"No POIs met the minimum significance threshold. Filtered out: {', '.join(filtered_out) or 'all'}."

    # Sort by distance — let the LLM weigh distance against significance
    scored.sort(key=lambda x: x["distance_m"])

    lines = ["Ranked POIs (closest first):\n"]
    if filtered_out:
        lines.append(f"(Below threshold, excluded: {', '.join(filtered_out)})\n")
    for i, s in enumerate(scored, 1):
        offset_str = f" | route_offset: {s['route_offset_m']}m" if "route_offset_m" in s else ""
        lines.append(
            f"{i}. {s['name']} — {s['distance_m']}m away, bearing {s['bearing']}°"
            f" | significance: {s['significance']:.2f}"
            f" | interest: {s['interest_match']}"
            f" | wiki: {s['wiki']}"
            f"{offset_str}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8: log_story
# ---------------------------------------------------------------------------

@tool
def log_story(
    session_id: str,
    poi_id: str,
    poi_name: str,
    story_text: str,
    latitude: float,
    longitude: float,
) -> str:
    """Record a delivered story for session history.

    Always call this after generating and synthesizing a story so future
    get_session_history calls include it. This prevents repeating POIs.
    """
    entry = {
        "session_id": session_id,
        "poi_id":     poi_id,
        "poi_name":   poi_name,
        "story_text": story_text,
        "latitude":   latitude,
        "longitude":  longitude,
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _session_stories.append(entry)

    print(f"\n[TourAI] Story logged: '{poi_name}' @ {entry['timestamp']}")

    return (
        f"Story logged successfully.\n"
        f"  POI      : {poi_name} (id={poi_id})\n"
        f"  Session  : {session_id}\n"
        f"  Time     : {entry['timestamp']}\n"
        f"  Total stories this session: "
        f"{len([s for s in _session_stories if s['session_id'] == session_id])}"
    )


# ---------------------------------------------------------------------------
# Exported tool list for the agent
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    search_pois,
    rank_pois,
    enrich_poi,
    get_weather,
    get_user_profile,
    get_session_history,
    synthesize_audio,
    log_story,
]
