# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
pip install langgraph langchain-google-genai httpx edge-tts python-dotenv
```

Requires a `.env` file with `GEMINI_API_KEY`. The venv is at `./venv/`.

## Running

**Scripted demo** (hardcoded Dallas stops):
```bash
python main.py
```

**Interactive testing** (manual coords, Maps URLs, walk simulation):
```bash
python interactive.py
```

Inside interactive mode, commands:
- `32.7787, -96.8083` — manual lat/lon (optional `, speed, heading`)
- `<Google Maps URL>` — paste to extract coords
- `walk {lat},{lon} to {lat},{lon} in {N} steps` — simulate a walk along OSRM route
- `fast` — toggle test mode (disables 4-minute timing gate)
- `reset` — clear session history, POI cache, and context window
- `profile` / `history` — inspect current user profile and session stories

**Run a specific test file:**
```bash
python tests/test_tools.py
python tests/test_rank_pois.py
python tests/test_utils.py
python tests/test_agent.py
python tests/test_graph.py
python tests/test_edge_cases.py
```

(Tests use `sys.path.insert` to find the project root — run from repo root.)

## Architecture

Multi-agent **LangGraph** system with an Orchestrator subgraph feeding into a Storyteller subgraph. Both agents use `gemma-4-31b-it` via `ChatGoogleGenerativeAI`.

### Package layout

```
tourai/                      # main Python package
  __init__.py
  state.py                   # AgentState + StorytellerState TypedDicts
  tools.py                   # all 9 LangChain tools + ORCHESTRATOR_TOOLS / STORYTELLER_TOOLS lists
  prefetcher.py              # background POI pre-fetch (daemon thread, own asyncio loop)
  profile_manager.py         # JSON-backed user interest weights with engagement decay
  agents/
    orchestrator.py          # ORCHESTRATOR_PROMPT + bound model (steps 1–4)
    storyteller.py           # STORYTELLER_PROMPT + bound model (steps 5–7)
  graphs/
    orchestrator_graph.py    # orchestrator StateGraph — routes to storyteller on SPEAK decision
    storyteller_graph.py     # storyteller StateGraph — self-critique loop (max 2 revisions)
  utils/
    overpass.py              # async Overpass API client (OSM POI search)
    wikipedia.py             # async Wikipedia summary fetcher
    weather.py               # async Open-Meteo client (WMO weathercode table)
    tts.py                   # async TTS via edge-tts
    osrm.py                  # OSRM nearest-road snap and walking route polyline

graph.py                     # thin shim — re-exports build_orchestrator_graph as build_graph()
main.py                      # scripted demo runner (hardcoded Dallas stops)
interactive.py               # interactive REPL with walk simulation and OSRM street snapping
profiles/                    # JSON user profile files (auto-created)
output/                      # MP3 audio files (auto-created)
tests/                       # test scripts (not a pytest suite — run directly)
```

### Execution flow

```
GPS message
  → Orchestrator (steps 1–4: perceive, gate, search, spatial filter)
      calls make_orchestrator_decision(action="speak", poi=...) or action="wait"
  → decision_node extracts decision, sets orchestrator_decision in state
  → if "speak": storyteller_invoke_node compiles + runs storyteller subgraph
      → Storyteller (steps 5–7: enrich, write + self-critique, deliver audio)
          self-critique loop: up to 2 revision cycles
          calls synthesize_audio + log_story as parallel tool calls
  → final_output_node writes {action, story_text, audio_path} or {action, reasoning}
```

### Key design decisions

- **Prompt chaining**: Orchestrator and Storyteller have separate focused prompts. Orchestrator passes a structured POI brief (name, id, tags, coordinates) to Storyteller via `StorytellerState`.
- **`make_orchestrator_decision` (Tool 9)**: Orchestrator's only output channel — forces a structured decision and lets the graph route to storyteller or output directly.
- **Prefetcher**: `tourai/prefetcher.py` fires `search_nearby` in a daemon thread with its own event loop (isolated from main `_executor`) when the user starts walking. Results are cached and injected as pre-loaded context on the next GPS update.
- **Pre-loaded context injection**: `interactive.py` injects USER PROFILE, SESSION HISTORY, WEATHER, and (if available) PRE-FETCHED POIS into the GPS HumanMessage to avoid redundant tool calls. Orchestrator falls back to live `search_pois` if pre-fetch is missing.
- **Async bridging**: All `utils/*.py` are async. `tools.py` bridges them via `_run()` (ThreadPoolExecutor). Prefetcher uses `asyncio.run()` in its own thread — never touches `_executor`.

### Tools and caching

- `search_pois` — caches by `(grid_cell_55m, radius, frozenset(tags))` for 5 minutes
- `get_weather` — caches for 30 minutes
- `_session_stories` — module-level list in `tools.py`, lives for process lifetime
- Audio output goes to `./output/` as timestamped MP3s

### `rank_pois` scoring

```
significance = (tag_richness × 0.3) + (interest_match × 0.5) + (wiki_notability × 0.2)
```

POIs below 0.15 are filtered. Ranking: tier (IMMEDIATE < 50m, NEAR < 200m, FAR) takes priority over significance score. Within a tier, higher significance wins.

### Context window management

Both `main.py` and `interactive.py` compress history when it exceeds 30 messages: oldest messages are replaced with a summary preserving POI names told so far. The system message is stripped from carried messages and prepended fresh each invocation.

### OSRM integration

`interactive.py` snaps each GPS point to the nearest road via `tourai/utils/osrm.py` before invoking the agent. This provides `dest_lat/dest_lon` (road endpoint ahead), which `rank_pois` uses to compute `route_offset_m` — how far each POI sits off the traveler's path.
