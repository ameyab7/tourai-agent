# graph.py
#
# Wires the TourAI ReAct agent into a LangGraph StateGraph.
#
# Flow:
#   START → agent_node → (tool_calls?) → tools_node → agent_node → ...
#                      ↘ (no tool_calls) → output_node → END
#
# The agent_node calls Gemini. If Gemini wants to call tools, the tools_node
# executes them and returns results. The agent then reasons again with the
# tool results, possibly calling more tools, until it produces a final
# STORY: or WAIT: response — at which point output_node extracts it and ends.

import logging

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from state import AgentState
from agent import agent_model, SYSTEM_PROMPT
from tools import ALL_TOOLS

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 25  # ~12 tool-call round trips before hard stop


# ---------------------------------------------------------------------------
# Node: agent
# ---------------------------------------------------------------------------

def agent_node(state: AgentState) -> dict:
    """Call Gemini with the current message history.

    Prepends the system prompt if this is the first turn (no SystemMessage yet).
    Returns the new AIMessage to be appended to state["messages"].
    """
    messages = state["messages"]

    # Prepend system prompt on the first turn
    has_system = any(isinstance(m, SystemMessage) for m in messages)
    if not has_system:
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

    logger.debug("agent_node: invoking with %d messages", len(messages))
    response = agent_model.invoke(messages)
    logger.debug("agent_node: got response, tool_calls=%s", bool(getattr(response, "tool_calls", None)))

    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Node: tools
# ---------------------------------------------------------------------------

tool_node = ToolNode(ALL_TOOLS)


# ---------------------------------------------------------------------------
# Conditional edge: should_continue
# ---------------------------------------------------------------------------

def should_continue(state: AgentState) -> str:
    """Route to 'tools' if the agent made tool calls, else to 'output'."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and getattr(last_message, "tool_calls", None):
        return "tools"
    return "output"


# ---------------------------------------------------------------------------
# Node: output
# ---------------------------------------------------------------------------

def output_node(state: AgentState) -> dict:
    """Parse the agent's final response into a structured final_output dict."""
    last_message = state["messages"][-1]
    content = last_message.content if hasattr(last_message, "content") else ""

    # Handle list content (some Gemini model variants return content as list)
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()

    content = content.strip()
    logger.debug("output_node: content preview = %s", content[:80])

    if content.upper().startswith("STORY:"):
        story_text = content[len("STORY:"):].strip()
        final_output = {"action": "speak", "story_text": story_text}
    elif content.upper().startswith("WAIT:"):
        reasoning = content[len("WAIT:"):].strip()
        final_output = {"action": "wait", "reasoning": reasoning}
    else:
        # Agent responded with neither prefix — treat as a story if non-empty
        if content:
            final_output = {"action": "speak", "story_text": content}
        else:
            final_output = {"action": "wait", "reasoning": "Agent produced no output."}

    return {"final_output": final_output}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph():
    """Build and compile the TourAI ReAct agent graph."""
    graph = StateGraph(AgentState)

    graph.add_node("agent",  agent_node)
    graph.add_node("tools",  tool_node)
    graph.add_node("output", output_node)

    graph.set_entry_point("agent")

    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "output": "output"},
    )
    graph.add_edge("tools",  "agent")   # tool results feed back into agent
    graph.add_edge("output", END)

    return graph.compile()
