from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # full conversation / tool call history
    user_id: str
    session_id: str
    latitude: float
    longitude: float
    speed_mps: float
    heading: float
    timestamp: str
    final_output: dict  # action, story_text, audio_bytes, poi_name — or None fields
