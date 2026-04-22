# interactive.py
#
# Interactive testing tool for the TourAI agent.
# Supports manual coordinate entry, Google Maps URL paste, and walk simulation.

import logging
import math
import os
import re
import sys
import time
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph import build_graph, RECURSION_LIMIT
from state import AgentState
from tools import _session_stories, _haversine_meters

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Session config
# ---------------------------------------------------------------------------

USER_ID   = "interactive-user"
MAX_MESSAGES = 30
KEEP_RECENT  = 10
WALK_STEP_INTERVAL = 2  # seconds between walk steps (real sleep)

# ---------------------------------------------------------------------------
# Context window management (mirrors main.py)
# ---------------------------------------------------------------------------

def _maybe_summarize(messages: list) -> list:
    if len(messages) <= MAX_MESSAGES:
        return messages

    poi_names = [s["poi_name"] for s in _session_stories]
    poi_str   = ", ".join(poi_names) if poi_names else "none yet"

    summary = (
        f"[Session Summary — earlier context compressed]\n"
        f"Stories already told this session: {poi_str}.\n"
        f"Continuing the interactive tour now."
    )
    recent = messages[-KEEP_RECENT:]
    return [HumanMessage(content=summary)] + recent


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def _content_str(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return content


def _print_reasoning_chain(messages: list, start_idx: int) -> tuple[int, int]:
    """Print the reasoning chain from start_idx onward. Returns (gemini_calls, tool_calls)."""
    gemini_calls = 0
    tool_calls   = 0

    for msg in messages[start_idx:]:
        if isinstance(msg, (HumanMessage, SystemMessage)):
            continue

        if isinstance(msg, AIMessage):
            gemini_calls += 1
            tc = getattr(msg, "tool_calls", None)
            if tc:
                for call in tc:
                    args_preview = str(call["args"])[:80]
                    print(f"  🤖 AGENT THINKS  → Calling: {call['name']}({args_preview})")
            else:
                content = _content_str(msg)
                if content:
                    print(f"  🤖 AGENT DECIDES → {content[:300]}{'...' if len(content) > 300 else ''}")

        elif isinstance(msg, ToolMessage):
            tool_calls += 1
            content = _content_str(msg)
            preview = content[:300].replace("\n", " ")
            name    = getattr(msg, "name", "tool")
            print(f"  🔧 TOOL RESULT  ({name}): {preview}{'...' if len(content) > 300 else ''}")

    return gemini_calls, tool_calls


# ---------------------------------------------------------------------------
# Input parsers
# ---------------------------------------------------------------------------

def _parse_coords(text: str) -> tuple[float, float, float, float] | None:
    """Parse 'lat, lon [, speed [, heading]]'. Returns (lat, lon, speed, heading) or None."""
    # Strip any trailing/leading whitespace and split on commas or spaces
    parts = re.split(r"[,\s]+", text.strip())
    nums  = []
    for p in parts:
        try:
            nums.append(float(p))
        except ValueError:
            return None

    if len(nums) < 2:
        return None

    lat, lon = nums[0], nums[1]
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None

    speed   = nums[2] if len(nums) > 2 else 0.8
    heading = nums[3] if len(nums) > 3 else 0.0

    return lat, lon, speed, heading


def _parse_maps_url(text: str) -> tuple[float, float] | None:
    """Extract (lat, lon) from a Google Maps URL. Returns None if not a maps URL."""
    if "google.com/maps" not in text and not text.startswith("https://maps"):
        return None

    # Format 1: @{lat},{lon},{zoom}  (most common)
    m = re.search(r"@(-?\d+\.?\d+),(-?\d+\.?\d+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))

    # Format 2: q={lat},{lon}
    m = re.search(r"[?&]q=(-?\d+\.?\d+),(-?\d+\.?\d+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))

    # Format 3: /place/.../{lat},{lon},  (path segment)
    m = re.search(r"/(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+),", text)
    if m:
        return float(m.group(1)), float(m.group(2))

    return None


def _parse_walk(text: str) -> tuple[tuple, tuple, int] | None:
    """Parse 'walk {lat},{lon} to {lat},{lon} in {N} steps'.

    Returns ((start_lat, start_lon), (end_lat, end_lon), N) or None.
    """
    m = re.match(
        r"walk\s+"
        r"(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)"
        r"\s+to\s+"
        r"(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)"
        r"\s+in\s+(\d+)\s+steps?",
        text.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None

    start = (float(m.group(1)), float(m.group(2)))
    end   = (float(m.group(3)), float(m.group(4)))
    steps = int(m.group(5))

    if steps < 1 or steps > 50:
        print("  ⚠️  Steps must be between 1 and 50.")
        return None

    return start, end, steps


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute compass bearing (degrees) from point 1 to point 2."""
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


_WALK_SPEED_MPS = 1.0  # realistic pedestrian walking speed


def _interpolate_walk(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int,
) -> list[tuple[float, float, float, float]]:
    """Generate `steps` evenly spaced (lat, lon, speed, heading) points."""
    heading = _bearing(start[0], start[1], end[0], end[1])

    points = []
    for i in range(steps):
        t   = i / max(steps - 1, 1)
        lat = start[0] + t * (end[0] - start[0])
        lon = start[1] + t * (end[1] - start[1])
        points.append((lat, lon, _WALK_SPEED_MPS, round(heading, 1)))

    return points


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def _run_agent(
    graph,
    lat: float,
    lon: float,
    speed: float,
    heading: float,
    carried_messages: list,
    session_id: str,
    dest_lat: float | None = None,
    dest_lon: float | None = None,
) -> tuple[dict, list, int, int, float]:
    """Invoke the agent graph, print reasoning chain, return results.

    Returns:
        (final_output, new_carried_messages, gemini_calls, tool_calls, latency_s)
    """
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    carried_messages = _maybe_summarize(carried_messages)

    # Pre-load profile, session history, and weather into the GPS message.
    # Each skipped tool call saves one full Gemini round trip.
    from tools import get_user_profile, get_session_history, get_weather
    profile_text = get_user_profile.invoke({"user_id": USER_ID})
    history_text = get_session_history.invoke({"session_id": session_id})
    weather_text = get_weather.invoke({"latitude": lat, "longitude": lon})

    dest_str = (
        f" Destination: ({dest_lat}, {dest_lon})."
        if dest_lat is not None and dest_lon is not None
        else ""
    )
    gps_msg = HumanMessage(content=(
        f"GPS Update: lat={lat}, lon={lon}, speed={speed} m/s, "
        f"heading={heading}, time={now_str}.{dest_str} "
        f"User: {USER_ID}. Session: {session_id}.\n\n"
        f"[Pre-loaded context — do NOT call these tools again, use this data directly]\n"
        f"USER PROFILE:\n{profile_text}\n"
        f"SESSION HISTORY:\n{history_text}\n"
        f"WEATHER:\n{weather_text}"
    ))

    start_idx = len(carried_messages) + 1  # +1 for gps_msg

    initial_state: AgentState = {
        "messages":     carried_messages + [gps_msg],
        "user_id":      USER_ID,
        "session_id":   session_id,
        "latitude":     lat,
        "longitude":    lon,
        "speed_mps":    speed,
        "heading":      float(heading),
        "timestamp":    now_str,
        "final_output": {},
    }

    t0 = time.time()
    result = graph.invoke(initial_state, config={"recursion_limit": RECURSION_LIMIT})
    latency = time.time() - t0

    gemini_calls, tool_calls = _print_reasoning_chain(result["messages"], start_idx)

    final_output = result.get("final_output", {})

    new_carried = [m for m in result["messages"] if not isinstance(m, SystemMessage)]

    return final_output, new_carried, gemini_calls, tool_calls, latency


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _print_profile():
    from tools import get_user_profile
    print()
    print(get_user_profile.invoke({"user_id": USER_ID}))
    print()


def _print_history(session_id: str):
    from tools import get_session_history
    print()
    print(get_session_history.invoke({"session_id": session_id}))
    print()


def _reset(carried_messages: list) -> list:
    from tools import _poi_cache, _weather_cache
    _session_stories.clear()
    _poi_cache.clear()
    _weather_cache.clear()
    carried_messages.clear()
    print("\n  ✅ Session reset — history, POI cache, and context window cleared.\n")
    return []


# ---------------------------------------------------------------------------
# Single-coordinate dispatch
# ---------------------------------------------------------------------------

def _handle_coordinate(
    graph,
    lat: float,
    lon: float,
    speed: float,
    heading: float,
    carried_messages: list,
    session_id: str,
    dest_lat: float | None = None,
    dest_lon: float | None = None,
) -> list:
    """Run the agent for one set of coordinates. Returns updated carried_messages."""
    now_display = datetime.now().strftime("%H:%M:%S")

    print(f"\n  📍 Location : ({lat}, {lon})")
    print(f"  🚶 Speed    : {speed} m/s  |  🧭 Heading: {heading}°  |  🕐 Time: {now_display}")
    print()

    try:
        final, carried_messages, gc, tc, latency = _run_agent(
            graph, lat, lon, speed, heading, carried_messages, session_id,
            dest_lat=dest_lat, dest_lon=dest_lon,
        )
    except Exception as e:
        print(f"\n  ❌ Agent error: {e}\n")
        return carried_messages

    print()
    action = final.get("action", "wait")
    if action == "speak":
        story = final.get("story_text", "")
        print(f"  🔊 STORY ({len(story.split())} words):")
        print(f"  {story}")

        # Find audio file from most recent synthesize_audio ToolMessage
        for msg in reversed(carried_messages):
            if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "synthesize_audio":
                content = _content_str(msg)
                for line in content.splitlines():
                    if "File" in line and ".mp3" in line:
                        audio_path = line.split(":", 1)[-1].strip()
                        print(f"\n  🎧 Audio   : {audio_path}")
                        break
                break
    else:
        reasoning = final.get("reasoning", "")
        print(f"  ⏸️  WAIT: {reasoning[:200]}")

    print(f"\n  📊 Tool calls: {tc}  |  Gemini calls: {gc}  |  Latency: {latency:.1f}s")
    print()

    return carried_messages


# ---------------------------------------------------------------------------
# Main interactive loop
# ---------------------------------------------------------------------------

def main():
    graph      = build_graph()
    session_id = f"interactive-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    carried_messages: list = []

    print()
    print("═" * 43)
    print("  TourAI Interactive Testing Mode")
    print("  Type coordinates or commands below")
    print("═" * 43)
    print()
    print("  Formats:")
    print("    32.7787, -96.8083              (lat, lon)")
    print("    32.7787, -96.8083, 25.0        (+ speed m/s)")
    print("    32.7787, -96.8083, 0.8, 90     (+ heading °)")
    print("    <Google Maps URL>              (paste URL)")
    print("    walk {lat},{lon} to {lat},{lon} in {N} steps")
    print()
    print("  Commands: profile | history | reset | quit")
    print()

    while True:
        try:
            raw = input("📍 Enter coordinates or command:\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  👋 Goodbye!\n")
            sys.exit(0)

        if not raw:
            continue

        lower = raw.lower()

        # --- Commands ---
        if lower in ("quit", "exit", "q"):
            print("\n  👋 Goodbye!\n")
            sys.exit(0)

        if lower == "profile":
            _print_profile()
            continue

        if lower == "history":
            _print_history(session_id)
            continue

        if lower == "reset":
            carried_messages = _reset(carried_messages)
            continue

        # --- Walk simulation ---
        if lower.startswith("walk "):
            parsed = _parse_walk(raw)
            if parsed is None:
                print("  ⚠️  Could not parse walk command.")
                print("      Format: walk {lat},{lon} to {lat},{lon} in {N} steps\n")
                continue

            start, end, steps = parsed
            total_dist = _haversine_meters(start[0], start[1], end[0], end[1])
            points = _interpolate_walk(start, end, steps)

            print(f"\n  🗺️  Walk simulation: {steps} steps, ~{total_dist:.0f}m total")
            print(f"       From ({start[0]}, {start[1]}) → ({end[0]}, {end[1]})\n")

            for i, (lat, lon, speed, heading) in enumerate(points, 1):
                print(f"  ── Step {i}/{steps} ──")
                carried_messages = _handle_coordinate(
                    graph, lat, lon, speed, heading, carried_messages, session_id,
                    dest_lat=end[0], dest_lon=end[1],
                )
                if i < steps:
                    time.sleep(WALK_STEP_INTERVAL)

            continue

        # --- Google Maps URL ---
        if "google.com/maps" in raw or raw.startswith("https://maps"):
            coords = _parse_maps_url(raw)
            if coords is None:
                print("  ⚠️  Could not extract coordinates from that URL.\n")
                continue
            lat, lon = coords
            print(f"  🗺️  Parsed from URL: ({lat}, {lon})")
            carried_messages = _handle_coordinate(
                graph, lat, lon, 0.8, 0.0, carried_messages, session_id
            )
            continue

        # --- Manual coordinates ---
        parsed = _parse_coords(raw)
        if parsed is None:
            print(
                "  ⚠️  Could not parse input.\n"
                "      Try: 32.7787, -96.8083  or  32.7787, -96.8083, 1.5, 90\n"
            )
            continue

        lat, lon, speed, heading = parsed
        carried_messages = _handle_coordinate(
            graph, lat, lon, speed, heading, carried_messages, session_id
        )


main()
