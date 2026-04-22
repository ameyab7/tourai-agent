import logging
import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph import build_graph, RECURSION_LIMIT
from state import AgentState
from tools import _session_stories

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# GPS walk through Dallas
# ---------------------------------------------------------------------------
STOPS = [
    {
        "n": 1, "desc": "Dealey Plaza",
        "lat": 32.7787, "lon": -96.8083,
        "speed": 0.8,  "heading": 90,  "time": "14:30",
    },
    {
        "n": 2, "desc": "JFK Memorial",
        "lat": 32.7792, "lon": -96.8075,
        "speed": 0.5,  "heading": 45,  "time": "14:34",
    },
    {
        "n": 3, "desc": "Old Red Museum",
        "lat": 32.7809, "lon": -96.8066,
        "speed": 1.1,  "heading": 0,   "time": "14:38",
    },
    {
        "n": 4, "desc": "Reunion Tower",
        "lat": 32.7755, "lon": -96.8088,
        "speed": 0.3,  "heading": 180, "time": "14:45",
    },
    {
        "n": 5, "desc": "Suburban area (nothing nearby)",
        "lat": 32.9500, "lon": -96.7500,
        "speed": 1.0,  "heading": 270, "time": "14:52",
    },
]

USER_ID    = "demo"
SESSION_ID = "session-001"

# Context window limits
MAX_MESSAGES  = 30
KEEP_RECENT   = 10


# ---------------------------------------------------------------------------
# Context window management
# ---------------------------------------------------------------------------

def _maybe_summarize(messages: list) -> list:
    """If messages exceed MAX_MESSAGES, compress oldest into a summary."""
    if len(messages) <= MAX_MESSAGES:
        return messages

    poi_names = [s["poi_name"] for s in _session_stories]
    poi_str = ", ".join(poi_names) if poi_names else "none yet"

    summary = (
        f"[Session Summary — earlier context compressed]\n"
        f"Stories already told this session: {poi_str}.\n"
        f"Traveler interests: history 90%, architecture 80%, photography 70%, food 50%, art 40%, nature 30%.\n"
        f"Continuing the tour now."
    )

    recent = messages[-KEEP_RECENT:]
    return [HumanMessage(content=summary)] + recent


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def _content_str(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return content


def _print_chain(messages: list, stop_start_idx: int):
    """Print the reasoning chain for the current stop only."""
    stop_messages = messages[stop_start_idx:]
    for msg in stop_messages:
        if isinstance(msg, HumanMessage):
            continue  # already printed as header
        if isinstance(msg, SystemMessage):
            continue

        if isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    args_preview = str(tc["args"])[:80]
                    print(f"  AGENT THINKS  -> Calling: {tc['name']}({args_preview})")
            else:
                content = _content_str(msg)
                if content:
                    print(f"  AGENT DECIDES -> {content[:300]}{'...' if len(content) > 300 else ''}")

        elif isinstance(msg, ToolMessage):
            content = _content_str(msg)
            preview = content[:200].replace("\n", " ")
            ellipsis = "..." if len(content) > 200 else ""
            name = getattr(msg, "name", "tool")
            print(f"  TOOL RESULT   ({name}): {preview}{ellipsis}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    graph = build_graph()

    carried_messages: list = []
    stories_told   = 0
    waits          = 0
    audio_files: list[str] = []

    print("\n" + "=" * 55)
    print("  TourAI  —  Agentic Dallas Walking Tour")
    print("=" * 55)

    for stop in STOPS:
        print(f"\n{'=' * 55}")
        print(f"  STOP {stop['n']}: {stop['desc']} ({stop['lat']}, {stop['lon']})")
        print(f"  Speed: {stop['speed']} m/s | Heading: {stop['heading']} | Time: {stop['time']}")
        print(f"{'=' * 55}\n")

        # Apply context window compression before each stop
        carried_messages = _maybe_summarize(carried_messages)

        gps_msg = HumanMessage(content=(
            f"GPS Update: lat={stop['lat']}, lon={stop['lon']}, "
            f"speed={stop['speed']} m/s, heading={stop['heading']}, "
            f"time=2026-04-09T{stop['time']}:00. "
            f"User: {USER_ID}. Session: {SESSION_ID}."
        ))

        # Track where this stop's messages start
        stop_start_idx = len(carried_messages) + 1  # +1 for the gps_msg we're about to add

        initial_state: AgentState = {
            "messages":     carried_messages + [gps_msg],
            "user_id":      USER_ID,
            "session_id":   SESSION_ID,
            "latitude":     stop["lat"],
            "longitude":    stop["lon"],
            "speed_mps":    stop["speed"],
            "heading":      float(stop["heading"]),
            "timestamp":    f"2026-04-09T{stop['time']}:00",
            "final_output": {},
        }

        try:
            result = graph.invoke(
                initial_state,
                config={"recursion_limit": RECURSION_LIMIT},
            )
        except Exception as e:
            print(f"  ERROR at stop {stop['n']}: {e}")
            waits += 1
            continue

        # Print reasoning chain for this stop
        _print_chain(result["messages"], stop_start_idx)

        # Parse final output
        final = result.get("final_output", {})
        action = final.get("action", "wait")

        print()
        if action == "speak":
            story = final.get("story_text", "")
            stories_told += 1

            # Find the audio file generated this stop
            audio_path = ""
            for msg in reversed(result["messages"]):
                if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "synthesize_audio":
                    content = _content_str(msg)
                    for line in content.splitlines():
                        if "File" in line and ".mp3" in line:
                            audio_path = line.split(":", 1)[-1].strip()
                            break
                    if audio_path:
                        break

            if audio_path:
                audio_files.append(audio_path)

            print(f"  STORY ({len(story.split())} words):")
            print(f"  {story}")
            if audio_path:
                print(f"\n  Audio: {audio_path}")
        else:
            waits += 1
            reasoning = final.get("reasoning", "")
            print(f"  WAIT: {reasoning[:200]}")

        # Carry messages forward (excluding the system message — it gets prepended fresh)
        carried_messages = [
            m for m in result["messages"]
            if not isinstance(m, SystemMessage)
        ]

    # Summary
    print(f"\n{'=' * 55}")
    print(f"  TOUR COMPLETE")
    print(f"  Stories told : {stories_told}")
    print(f"  Waits        : {waits}")
    if audio_files:
        print(f"  Audio files  :")
        for f in audio_files:
            print(f"    {f}")
    print(f"{'=' * 55}\n")


main()
