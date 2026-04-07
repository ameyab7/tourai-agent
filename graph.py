# graph.py
#
# Builds the TourAI LangGraph agent graph.
#
# Node pipeline:
#   START
#     -> gps_processor       (classify movement speed)
#     -> poi_discovery        (find nearby OSM POIs)
#     -> significance_filter  (score and pick top POI)
#     -> timing_decision      (decide whether to speak)
#     -> [conditional]
#          if should_speak=True:
#            context_enrichment -> story_generation -> audio_delivery -> memory_update -> END
#          if should_speak=False:
#            END

from langgraph.graph import StateGraph, START, END

from state import TourGuideState
from nodes.gps_processor import gps_processor
from nodes.poi_discovery import poi_discovery
from nodes.significance_filter import significance_filter
from nodes.timing_decision import timing_decision
from nodes.context_enrichment import context_enrichment
from nodes.story_generation_node import story_generation_node
from nodes.audio_delivery import audio_delivery
from nodes.memory_update import memory_update


def _should_speak(state: TourGuideState) -> str:
    return "speak" if state.get("should_speak") else "skip"


def build_graph() -> StateGraph:
    graph = StateGraph(TourGuideState)

    # Register all nodes
    graph.add_node("gps_processor",       gps_processor)
    graph.add_node("poi_discovery",        poi_discovery)
    graph.add_node("significance_filter",  significance_filter)
    graph.add_node("timing_decision",      timing_decision)
    graph.add_node("context_enrichment",   context_enrichment)
    graph.add_node("story_generation",     story_generation_node)
    graph.add_node("audio_delivery",       audio_delivery)
    graph.add_node("memory_update",        memory_update)

    # Linear pipeline up to timing decision
    graph.add_edge(START,               "gps_processor")
    graph.add_edge("gps_processor",     "poi_discovery")
    graph.add_edge("poi_discovery",     "significance_filter")
    graph.add_edge("significance_filter", "timing_decision")

    # Conditional branch after timing decision
    graph.add_conditional_edges(
        "timing_decision",
        _should_speak,
        {
            "speak": "context_enrichment",
            "skip":  END,
        },
    )

    # Story pipeline
    graph.add_edge("context_enrichment", "story_generation")
    graph.add_edge("story_generation",   "audio_delivery")
    graph.add_edge("audio_delivery",     "memory_update")
    graph.add_edge("memory_update",      END)

    return graph.compile()
