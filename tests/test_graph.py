import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from graph import build_graph, RECURSION_LIMIT
from state import AgentState

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# GPS stop: Dealey Plaza, Dallas
LAT       = 32.7787
LON       = -96.8083
SPEED     = 0.8
HEADING   = 45.0
TIMESTAMP = "2026-04-09T14:30:00"
USER_ID   = "demo"
SESSION_ID = "session-001"


def _label(msg) -> str:
    if isinstance(msg, SystemMessage):
        return "SYSTEM"
    if isinstance(msg, HumanMessage):
        return "HUMAN"
    if isinstance(msg, AIMessage):
        return "AGENT"
    if isinstance(msg, ToolMessage):
        return f"TOOL({msg.name})"
    return type(msg).__name__


def main():
    print(f"\n{'='*65}")
    print(f"  TourAI ReAct Graph Test")
    print(f"  Location : Dealey Plaza, Dallas ({LAT}, {LON})")
    print(f"  Speed    : {SPEED} m/s (walking)")
    print(f"  Time     : {TIMESTAMP}")
    print(f"{'='*65}\n")

    graph = build_graph()

    gps_message = (
        f"GPS Update: lat={LAT}, lon={LON}, speed={SPEED} m/s, "
        f"heading={HEADING}, time={TIMESTAMP}. "
        f"User: {USER_ID}. Session: {SESSION_ID}."
    )

    initial_state: AgentState = {
        "messages":     [HumanMessage(content=gps_message)],
        "user_id":      USER_ID,
        "session_id":   SESSION_ID,
        "latitude":     LAT,
        "longitude":    LON,
        "speed_mps":    SPEED,
        "heading":      HEADING,
        "timestamp":    TIMESTAMP,
        "final_output": {},
    }

    print("  Running agent graph...\n")

    result = graph.invoke(
        initial_state,
        config={"recursion_limit": RECURSION_LIMIT},
    )

    # Print full reasoning chain
    print(f"\n{'='*65}")
    print(f"  FULL REASONING CHAIN ({len(result['messages'])} messages)")
    print(f"{'='*65}\n")

    for i, msg in enumerate(result["messages"], 1):
        label = _label(msg)
        content = msg.content if hasattr(msg, "content") else ""

        # Handle list content
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            ).strip()

        # Show tool calls on agent messages
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            calls_str = ", ".join(tc["name"] for tc in tool_calls)
            print(f"  [{i:02d}] {label} → calling tools: [{calls_str}]")
        elif content:
            preview = content[:200].replace("\n", " ")
            print(f"  [{i:02d}] {label}: {preview}{'...' if len(content) > 200 else ''}")
        else:
            print(f"  [{i:02d}] {label}: (empty)")

    # Final output
    print(f"\n{'='*65}")
    print(f"  FINAL OUTPUT")
    print(f"{'='*65}\n")

    final = result.get("final_output", {})
    action = final.get("action", "unknown")

    if action == "speak":
        story = final.get("story_text", "")
        print(f"  Action : STORY ({len(story.split())} words)\n")
        print(f"  {story}\n")
    elif action == "wait":
        print(f"  Action : WAIT")
        print(f"  Reason : {final.get('reasoning', '')}\n")
    else:
        print(f"  Action : {action}")
        print(f"  Data   : {final}\n")


main()
