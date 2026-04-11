import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph import build_graph, RECURSION_LIMIT
from state import AgentState

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

USER_ID    = "edge-test"
SESSION_ID = "edge-session-001"

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    {
        "id": 1,
        "desc": "Highway Driving",
        "lat": 32.85, "lon": -96.75,
        "speed": 28.0, "heading": 180, "time": "15:00",
        "expected": "WAIT or wide-radius search for major landmarks only — should NOT narrate small POIs at 63 mph.",
        "carry_messages": False,
    },
    {
        "id": 2,
        "desc": "Standing Still for 5 Minutes",
        "lat": 32.7787, "lon": -96.8083,
        "speed": 0.0, "heading": 0, "time": "15:05",
        "expected": "STORY — stationary user is receptive, prime moment for a story.",
        "carry_messages": False,
    },
    {
        "id": 3,
        "desc": "Middle of a Lake",
        "lat": 32.8200, "lon": -96.7200,
        "speed": 0.0, "heading": 0, "time": "15:10",
        "expected": "WAIT gracefully — search returns nothing, no crash, no weird story about water.",
        "carry_messages": False,
    },
    {
        "id": 4,
        "desc": "Rapid Back-to-Back Calls (3x same location, 10s apart)",
        "lat": 32.7787, "lon": -96.8083,
        "speed": 0.8, "heading": 90,
        "times": ["15:20", "15:20", "15:21"],  # special: 3 sub-calls
        "expected": "STORY on first call, WAIT on 2nd and 3rd (cooldown + already told).",
        "carry_messages": True,   # messages carry between the 3 sub-calls
    },
    {
        "id": 5,
        "desc": "Late Night Walk",
        "lat": 32.7787, "lon": -96.8083,
        "speed": 0.8, "heading": 90, "time": "23:30",
        "expected": "STORY adapted for nighttime — mention if things are closed, focus on night-relevant atmosphere.",
        "carry_messages": False,
    },
]

# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

total_gemini_calls = 0
total_tool_calls   = 0
scenario_steps: list[int] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_str(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return content


def _print_chain(messages: list, start_idx: int = 0):
    for msg in messages[start_idx:]:
        if isinstance(msg, (HumanMessage, SystemMessage)):
            continue
        if isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    args_str = str(tc["args"])[:80]
                    print(f"    AGENT THINKS  -> Calling: {tc['name']}({args_str})")
            else:
                content = _content_str(msg)
                if content:
                    print(f"    AGENT DECIDES -> {content[:300]}{'...' if len(content) > 300 else ''}")
        elif isinstance(msg, ToolMessage):
            content = _content_str(msg)
            preview = content[:150].replace("\n", " ")
            print(f"    TOOL RESULT   ({getattr(msg, 'name', 'tool')}): {preview}{'...' if len(content) > 150 else ''}")


def _count_stats(messages: list) -> tuple[int, int]:
    """Return (gemini_calls, tool_calls) in this message list."""
    ai = sum(1 for m in messages if isinstance(m, AIMessage))
    tools = sum(1 for m in messages if isinstance(m, ToolMessage))
    return ai, tools


def _make_state(lat, lon, speed, heading, time_str, messages=None) -> AgentState:
    gps_msg = HumanMessage(content=(
        f"GPS Update: lat={lat}, lon={lon}, speed={speed} m/s, "
        f"heading={heading}, time=2026-04-09T{time_str}:00. "
        f"User: {USER_ID}. Session: {SESSION_ID}."
    ))
    prior = messages or []
    return {
        "messages":     prior + [gps_msg],
        "user_id":      USER_ID,
        "session_id":   SESSION_ID,
        "latitude":     lat,
        "longitude":    lon,
        "speed_mps":    speed,
        "heading":      float(heading),
        "timestamp":    f"2026-04-09T{time_str}:00",
        "final_output": {},
    }


def _run_scenario(graph, scenario: dict) -> dict:
    """Run a single scenario and return the result. Handles multi-call scenario 4."""
    global total_gemini_calls, total_tool_calls

    if scenario["id"] == 4:
        # 3 rapid back-to-back calls carrying messages
        carried = []
        last_result = {}

        for i, t in enumerate(scenario["times"], 1):
            print(f"\n  -- Sub-call {i}/3 (time={t}) --")
            state = _make_state(
                scenario["lat"], scenario["lon"],
                scenario["speed"], scenario["heading"],
                t, messages=carried,
            )
            start_idx = len(carried) + 1
            result = graph.invoke(state, config={"recursion_limit": RECURSION_LIMIT})
            _print_chain(result["messages"], start_idx)

            ai, tc = _count_stats(result["messages"][start_idx:])
            total_gemini_calls += ai
            total_tool_calls += tc
            scenario_steps.append(ai + tc)

            final = result.get("final_output", {})
            action = final.get("action", "wait")
            if action == "speak":
                print(f"\n    STORY ({len(final.get('story_text','').split())} words): {final.get('story_text','')[:150]}...")
            else:
                print(f"\n    WAIT: {final.get('reasoning','')[:150]}")

            # Carry messages (drop SystemMessage)
            carried = [m for m in result["messages"] if not isinstance(m, SystemMessage)]
            last_result = result

        return last_result

    else:
        state = _make_state(
            scenario["lat"], scenario["lon"],
            scenario["speed"], scenario["heading"],
            scenario["time"],
        )
        result = graph.invoke(state, config={"recursion_limit": RECURSION_LIMIT})
        _print_chain(result["messages"], start_idx=1)

        ai, tc = _count_stats(result["messages"][1:])
        total_gemini_calls += ai
        total_tool_calls += tc
        scenario_steps.append(ai + tc)

        return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    graph = build_graph()

    for scenario in SCENARIOS:
        print(f"\n{'=' * 60}")
        print(f"  SCENARIO {scenario['id']}: {scenario['desc']}")
        if "time" in scenario:
            print(f"  Location : ({scenario['lat']}, {scenario['lon']})  Speed: {scenario['speed']} m/s  Time: {scenario['time']}")
        print(f"  Expected : {scenario['expected']}")
        print(f"{'=' * 60}\n")

        try:
            result = _run_scenario(graph, scenario)
        except Exception as e:
            print(f"  ERROR: {e}")
            scenario_steps.append(0)
            continue

        # For non-multi-call scenarios, print final output
        if scenario["id"] != 4:
            final = result.get("final_output", {})
            action = final.get("action", "wait")
            print()
            if action == "speak":
                story = final.get("story_text", "")
                print(f"  STORY ({len(story.split())} words):")
                print(f"  {story}")
            else:
                print(f"  WAIT: {final.get('reasoning', '')[:300]}")

        print(f"\n  Behavior matched expected? --> Judge above output manually.")

    # Summary stats
    avg_steps = sum(scenario_steps) / len(scenario_steps) if scenario_steps else 0
    print(f"\n{'=' * 60}")
    print(f"  EDGE CASE TEST SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total Gemini calls (AIMessages) : {total_gemini_calls}")
    print(f"  Total tool calls (ToolMessages) : {total_tool_calls}")
    print(f"  Avg reasoning steps/invocation  : {avg_steps:.1f}")
    print(f"  Steps per scenario              : {scenario_steps}")
    print(f"{'=' * 60}\n")


main()
