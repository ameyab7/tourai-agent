import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage, SystemMessage

from agent import agent_model, SYSTEM_PROMPT

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main():
    print(f"\n{'='*60}")
    print(f"  TourAI Agent Test — First GPS Update")
    print(f"{'='*60}\n")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            "GPS Update: lat=32.7787, lon=-96.8083, speed=0.8 m/s, heading=45, "
            "timestamp=2026-04-09T14:30:00. User ID: demo. Session ID: session-001."
        )),
    ]

    print("  Sending GPS update to agent...")
    print(f"  Location: Dealey Plaza, Dallas (32.7787, -96.8083)")
    print(f"  Speed   : 0.8 m/s (walking)")
    print(f"  Time    : 14:30 local\n")

    response = agent_model.invoke(messages)

    print(f"  Response type : {type(response).__name__}")
    print(f"  Content       : {response.content or '(empty — agent is making tool calls)'}")

    if hasattr(response, "tool_calls") and response.tool_calls:
        print(f"\n  Tool calls ({len(response.tool_calls)}):")
        for tc in response.tool_calls:
            print(f"    - {tc['name']}")
            print(f"      args: {tc['args']}")
    else:
        print("\n  No tool calls — agent responded directly.")

    print()


main()
