"""
model_comparison/run_comparison.py

Benchmarks qwen/qwen3-32b vs llama-3.3-70b-versatile on the exact same
agentic trip-planning task that /v1/itinerary/stream uses.

Metrics captured per trial:
  - tool_calls_in_first_response  : did the model batch ALL tools at once? (bool)
  - tools_called_count            : how many tools called in first response
  - total_iterations              : Groq calls until final JSON produced
  - wall_time_s                   : total seconds from first call to JSON parsed
  - prompt_tokens / completion_tokens / total_tokens  (summed across all calls)
  - json_valid                    : did the final output parse as valid JSON?
  - required_fields_present       : list of top-level keys that are missing
  - stops_per_day                 : avg stops across days (plan richness)
  - has_meals                     : every day has ≥ 1 meal stop?
  - error                         : exception message if the run failed

Usage:
  cd /Users/ameyabhujbal/Documents/tourai-agent
  python -m model_comparison.run_comparison
"""

import asyncio
import json
import os
import re
import sys
import time
import traceback
from datetime import date, timedelta
from urllib.parse import quote_plus

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from groq import AsyncGroq

# ── Reuse the exact tool definitions and system prompt from the agent ──────────
from api.routes.itinerary_agent import TOOLS, _build_system_prompt, _TC, _Msg
from api.config import settings


MODELS = [
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
]

TRIALS = 3  # runs per model — enough to spot consistency issues

# Fixed test scenario — same input for every run
TEST = {
    "destination": "Austin, TX",
    "lat": 30.2672,
    "lon": -97.7431,
    "start_date": "2026-05-10",
    "end_date": "2026-05-12",  # 3-day trip — rich enough to stress the plan
    "interests": ["history", "food", "photography"],
    "style": "couple",
    "pace": "balanced",
    "drive_tol": 2.0,
}

REQUIRED_TOP_KEYS = {"title", "summary", "getting_there", "accommodation", "budget", "days"}
REQUIRED_STOP_KEYS = {"name", "poi_type", "tip", "arrival_time", "duration_min", "is_meal", "lat", "lon", "transit_from_prev"}


# ── Groq streaming helper (mirrors _groq_call in itinerary_agent.py) ──────────

async def _groq_call(client: AsyncGroq, model: str, system: str, messages: list) -> tuple[_Msg, dict]:
    """Returns (_Msg, token_usage_dict)."""
    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}] + messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=0.3,
        max_tokens=4000,
        stream=True,
    )

    stream = await client.chat.completions.create(**kwargs)

    content: str = ""
    tc_map: dict = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async for chunk in stream:
        # Accumulate usage from final chunk
        if hasattr(chunk, "x_groq") and chunk.x_groq and hasattr(chunk.x_groq, "usage"):
            u = chunk.x_groq.usage
            if u:
                usage["prompt_tokens"]     += getattr(u, "prompt_tokens", 0) or 0
                usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
                usage["total_tokens"]      += getattr(u, "total_tokens", 0) or 0

        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue
        delta = choice.delta
        if delta.content:
            content += delta.content
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tc_map:
                    tc_map[idx] = {"id": "", "name": "", "arguments": ""}
                if tc_delta.id:
                    tc_map[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc_map[idx]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc_map[idx]["arguments"] += tc_delta.function.arguments

    tool_calls = (
        [_TC(v["id"], v["name"], v["arguments"]) for v in tc_map.values()]
        if tc_map else None
    )
    return _Msg(content, tool_calls), usage


# ── Lightweight tool stubs (return realistic but fast mock data) ──────────────

async def _stub_tool(name: str, args: dict) -> str:
    """Fast stubs so we can focus on model behaviour, not API latency."""
    if name == "search_attractions":
        return json.dumps([
            {"name": "Texas State Capitol", "poi_type": "historic", "lat": 30.2747, "lon": -97.7404},
            {"name": "Blanton Museum of Art", "poi_type": "museum",  "lat": 30.2827, "lon": -97.7394},
            {"name": "South Congress Ave",   "poi_type": "attraction","lat": 30.2501, "lon": -97.7503},
            {"name": "Barton Springs Pool",  "poi_type": "park",      "lat": 30.2642, "lon": -97.7726},
            {"name": "6th Street",           "poi_type": "attraction","lat": 30.2689, "lon": -97.7394},
            {"name": "Rainey Street",        "poi_type": "bar",       "lat": 30.2589, "lon": -97.7397},
        ])
    if name == "search_restaurants":
        return json.dumps([
            {"name": "Franklin Barbecue",  "poi_type": "restaurant", "lat": 30.2702, "lon": -97.7313, "cuisine": "bbq"},
            {"name": "Uchi",               "poi_type": "restaurant", "lat": 30.2560, "lon": -97.7564, "cuisine": "japanese"},
            {"name": "Juan in a Million",  "poi_type": "restaurant", "lat": 30.2645, "lon": -97.7165, "cuisine": "mexican"},
            {"name": "Torchy's Tacos",     "poi_type": "cafe",       "lat": 30.2599, "lon": -97.7586, "cuisine": "tex-mex"},
        ])
    if name == "search_hotels":
        return json.dumps([
            {"name": "Hotel Van Zandt",    "lat": 30.2604, "lon": -97.7397, "stars": 4},
            {"name": "Austin Motel",       "lat": 30.2515, "lon": -97.7508, "stars": 3},
            {"name": "JW Marriott Austin", "lat": 30.2673, "lon": -97.7431, "stars": 5},
        ])
    if name == "get_weather_forecast":
        dates = args.get("dates", [TEST["start_date"]])
        return json.dumps([
            {"date": d, "temp_high_c": 28, "temp_low_c": 18, "description": "Sunny",
             "is_clear": True, "weather_code": 0,
             "sunrise_iso": f"{d}T06:30:00-05:00", "sunset_iso": f"{d}T20:15:00-05:00"}
            for d in dates
        ])
    if name == "get_golden_hour":
        d = args.get("date", TEST["start_date"])
        return json.dumps({"date": d, "sunrise": f"{d}T06:30:00-05:00",
                           "sunset": f"{d}T20:15:00-05:00",
                           "windows": {"label": "Golden hour", "active": False, "minutes_away": 120}})
    if name == "get_drive_time":
        return json.dumps({"duration_min": 12, "distance_km": 4.5})
    return json.dumps({"error": f"unknown tool: {name}"})


# ── Single trial runner ───────────────────────────────────────────────────────

EXPECTED_TOOLS = {"search_attractions", "search_restaurants", "search_hotels", "get_weather_forecast"}

async def run_trial(model: str, trial_num: int) -> dict:
    t = TEST
    client = AsyncGroq(api_key=settings.groq_api_key)
    system = _build_system_prompt(t["interests"], t["pace"], t["style"], t["drive_tol"])

    dates = [
        (date.fromisoformat(t["start_date"]) + timedelta(days=i)).isoformat()
        for i in range(
            (date.fromisoformat(t["end_date"]) - date.fromisoformat(t["start_date"])).days + 1
        )
    ]
    flights_url = f"https://www.google.com/travel/flights?q=Flights+to+{quote_plus(t['destination'])}"
    booking_url = f"https://www.booking.com/search.html?ss={quote_plus(t['destination'])}&checkin={t['start_date']}&checkout={t['end_date']}"

    user_msg = (
        f"Plan a trip to {t['destination']}.\n"
        f"Dates: {t['start_date']} to {t['end_date']} ({len(dates)} days)\n"
        f"Destination coordinates: lat={t['lat']}, lon={t['lon']}\n"
        f"Trip dates list: {dates}\n"
        f"Interests: {', '.join(t['interests'])}\n"
        f"Flights URL: {flights_url}\n"
        f"Booking URL: {booking_url}"
    )

    messages = [{"role": "user", "content": user_msg}]

    result = {
        "model": model,
        "trial": trial_num,
        "tool_calls_in_first_response": False,
        "tools_called_count": 0,
        "expected_tools_called": [],
        "missing_expected_tools": [],
        "total_iterations": 0,
        "wall_time_s": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "json_valid": False,
        "missing_fields": [],
        "stops_per_day": 0.0,
        "has_meals": False,
        "error": None,
    }

    start = time.monotonic()
    try:
        for iteration in range(8):
            result["total_iterations"] += 1
            msg, usage = await _groq_call(client, model, system, messages)

            result["prompt_tokens"]     += usage["prompt_tokens"]
            result["completion_tokens"] += usage["completion_tokens"]
            result["total_tokens"]      += usage["total_tokens"]

            # First response — measure tool batching
            if iteration == 0 and msg.tool_calls:
                names = [tc.function.name for tc in msg.tool_calls]
                result["tools_called_count"]      = len(names)
                result["tool_calls_in_first_response"] = len(names) >= 3
                result["expected_tools_called"]   = [n for n in names if n in EXPECTED_TOOLS]
                result["missing_expected_tools"]  = list(EXPECTED_TOOLS - set(names))

            # No tool calls → agent produced final plan
            if not msg.tool_calls:
                content = msg.content or ""
                match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
                if match:
                    try:
                        plan = json.loads(match.group(1))
                        result["json_valid"] = True
                        result["missing_fields"] = list(REQUIRED_TOP_KEYS - set(plan.keys()))

                        # stops_per_day and meal coverage
                        days = plan.get("days", [])
                        if days:
                            total_stops = sum(len(d.get("stops", [])) for d in days)
                            result["stops_per_day"] = round(total_stops / len(days), 1)
                            result["has_meals"] = all(
                                any(s.get("is_meal") for s in d.get("stops", []))
                                for d in days
                            )
                    except json.JSONDecodeError as e:
                        result["error"] = f"JSON parse failed: {e}"
                else:
                    result["error"] = "No ```json block found in final response"
                break

            # Execute tools (stubs) and feed results back
            tool_results = await asyncio.gather(
                *[_stub_tool(tc.function.name,
                             json.loads(tc.function.arguments or "{}"))
                  for tc in msg.tool_calls],
                return_exceptions=True,
            )

            # Append assistant message with tool_calls
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            # Append tool results
            for tc, res in zip(msg.tool_calls, tool_results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": res if isinstance(res, str) else json.dumps({"error": str(res)}),
                })

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    result["wall_time_s"] = round(time.monotonic() - start, 1)
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    all_results: list[dict] = []

    for model in MODELS:
        print(f"\n{'='*60}")
        print(f"  Model: {model}")
        print(f"{'='*60}")
        for t in range(1, TRIALS + 1):
            print(f"  Trial {t}/{TRIALS} ...", flush=True)
            r = await run_trial(model, t)
            all_results.append(r)
            status = "✓" if r["json_valid"] else "✗"
            print(
                f"  {status}  iter={r['total_iterations']}  "
                f"time={r['wall_time_s']}s  "
                f"tokens={r['total_tokens']}  "
                f"batched={r['tool_calls_in_first_response']}  "
                f"tools={r['tools_called_count']}  "
                f"err={r['error'] or '-'}"
            )
            # Small pause between trials to avoid rate-limit spikes
            if t < TRIALS:
                await asyncio.sleep(5)

        # Inter-model gap
        if model != MODELS[-1]:
            print("\n  Waiting 10s before next model...")
            await asyncio.sleep(10)

    # ── Write raw results ──────────────────────────────────────────────────────
    out_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nRaw results → {out_path}")

    # ── Summary table ──────────────────────────────────────────────────────────
    summary_path = os.path.join(os.path.dirname(__file__), "summary.md")
    with open(summary_path, "w") as f:
        _write_summary(f, all_results)
    print(f"Summary     → {summary_path}")

    # Print summary to stdout too
    with open(summary_path) as f:
        print("\n" + f.read())


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else "-"

def _pct(vals):
    vals = [v for v in vals if v is not None]
    return f"{round(100 * sum(vals) / len(vals))}%" if vals else "-"

def _write_summary(f, results: list[dict]):
    f.write("# Model Comparison: qwen3-32b vs llama-3.3-70b-versatile\n\n")
    f.write(f"**Task:** 3-day trip plan for Austin TX · interests: history, food, photography · couple · balanced pace\n\n")
    f.write(f"**Trials per model:** {TRIALS}\n\n")

    for model in MODELS:
        rows = [r for r in results if r["model"] == model]
        success = [r for r in rows if r["json_valid"]]
        failed  = [r for r in rows if r["error"]]

        f.write(f"---\n\n## {model}\n\n")

        f.write("| Metric | Value |\n|---|---|\n")
        f.write(f"| Success rate (valid JSON plan) | {len(success)}/{len(rows)} |\n")
        f.write(f"| Batched all tools in 1st call | {_pct([r['tool_calls_in_first_response'] for r in rows])} |\n")
        f.write(f"| Avg tools called in 1st response | {_avg([r['tools_called_count'] for r in rows])} / {len(EXPECTED_TOOLS)} expected |\n")
        f.write(f"| Avg Groq iterations to complete | {_avg([r['total_iterations'] for r in rows])} |\n")
        f.write(f"| Avg wall time | {_avg([r['wall_time_s'] for r in rows])}s |\n")
        f.write(f"| Avg total tokens used | {_avg([r['total_tokens'] for r in rows])} |\n")
        f.write(f"| Avg prompt tokens | {_avg([r['prompt_tokens'] for r in rows])} |\n")
        f.write(f"| Avg completion tokens | {_avg([r['completion_tokens'] for r in rows])} |\n")
        f.write(f"| Has all required top-level fields | {_pct([len(r['missing_fields']) == 0 for r in success] or [False])} |\n")
        f.write(f"| Avg stops per day | {_avg([r['stops_per_day'] for r in success])} |\n")
        f.write(f"| Every day has meals | {_pct([r['has_meals'] for r in success] or [False])} |\n")

        if failed:
            f.write(f"\n**Errors:**\n")
            for r in failed:
                f.write(f"- Trial {r['trial']}: `{r['error']}`\n")

        if success:
            missing_tools_all = [t for r in rows for t in r["missing_expected_tools"]]
            if missing_tools_all:
                from collections import Counter
                counts = Counter(missing_tools_all)
                f.write(f"\n**Tools frequently skipped:** {dict(counts)}\n")

        f.write("\n")

    # ── Head-to-head verdict ───────────────────────────────────────────────────
    f.write("---\n\n## Head-to-head verdict\n\n")
    f.write("| Dimension | Winner | Why |\n|---|---|---|\n")
    f.write("| Daily token budget | qwen3-32b | 500K/day vs 100K/day — 5× more trips |\n")
    f.write("| Requests/min | qwen3-32b | 60/min vs 30/min |\n")
    f.write("| Tokens/min (burst) | llama-3.3-70b | 12K vs 6K — less likely to hit mid-request |\n")
    f.write("| Tool calling reliability | See results above | Determined by benchmark |\n")
    f.write("| Plan quality (stops, meals) | See results above | Determined by benchmark |\n")
    f.write("| Parallel tool batching | See results above | Key for agent speed |\n\n")
    f.write("> **Recommendation:** Use whichever model scored higher on success rate and tool batching above.\n")
    f.write("> If tied, prefer **qwen3-32b** for the 5× daily token headroom — critical as user count grows.\n")


if __name__ == "__main__":
    asyncio.run(main())
