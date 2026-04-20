#!/usr/bin/env python
"""scripts/auto_fix_agent.py — Visibility filter auto-repair agent.

Reads DISAGREE entries from feedback_log.ndjson, runs a LangGraph ReAct
agent that diagnoses the root cause and applies a minimal targeted patch to
utils/visibility.py, verifies tests pass, adds a regression test case, and
deploys.

Usage:
    python scripts/auto_fix_agent.py [--log feedback_log.ndjson] [--dry-run]

Options:
    --log PATH      Path to feedback NDJSON log  (default: feedback_log.ndjson)
    --dry-run       Propose fixes but don't apply, commit, or deploy
    --no-deploy     Apply + commit but skip railway deployment
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict

# Add project root so utils/ resolves
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT         = Path(__file__).parent.parent
VISIBILITY   = ROOT / "utils" / "visibility.py"
TEST_FILE    = ROOT / "tests" / "test_visibility_accuracy.py"
PROCESSED_LOG= ROOT / "feedback_processed.ndjson"

# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------

class FixState(TypedDict):
    messages: Annotated[list, add_messages]
    diagnosis: dict          # the feedback log entry being fixed
    dry_run:   bool
    no_deploy: bool
    fixed:     bool          # did agent successfully apply a fix?
    deployed:  bool

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def read_visibility_code() -> str:
    """Read the current contents of utils/visibility.py."""
    return VISIBILITY.read_text()


@tool
def apply_edit(old_string: str, new_string: str) -> str:
    """Apply a targeted edit to utils/visibility.py.

    Replace the EXACT text `old_string` with `new_string`.
    The old_string must appear exactly once in the file.
    Returns 'OK' on success or an error message.
    """
    content = VISIBILITY.read_text()
    count = content.count(old_string)
    if count == 0:
        return f"ERROR: old_string not found in visibility.py. Check exact whitespace/indentation."
    if count > 1:
        return f"ERROR: old_string appears {count} times — provide more context to make it unique."
    VISIBILITY.write_text(content.replace(old_string, new_string, 1))
    return "OK"


@tool
def revert_edit() -> str:
    """Revert utils/visibility.py to the last git commit (undo any edits made this session)."""
    result = subprocess.run(
        ["git", "checkout", "utils/visibility.py"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode == 0:
        return "Reverted visibility.py to last git commit."
    return f"ERROR: git checkout failed: {result.stderr}"


@tool
def run_tests() -> str:
    """Run tests/test_visibility_accuracy.py and return pass/fail + summary.

    Returns the last 40 lines of output so the agent can see what failed.
    """
    result = subprocess.run(
        [sys.executable, "tests/test_visibility_accuracy.py"],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    output = (result.stdout + result.stderr).strip()
    lines  = output.splitlines()
    # Return the last 40 lines
    summary = "\n".join(lines[-40:])
    status  = "PASSED" if result.returncode == 0 else "FAILED"
    return f"[{status}]\n{summary}"


@tool
def add_test_case(scenario_code: str) -> str:
    """Append a new regression test scenario to tests/test_visibility_accuracy.py.

    `scenario_code` must be a complete scenario(...) block ready to paste
    into the test file just before the final closing bracket of the
    `SCENARIOS` list.  Use the same helper functions as the existing tests:
      scenario(description, user_lat, user_lon, heading, user_street, [pois])
      poi(id, name, lat, lon, tags, expected)

    Returns 'OK' or an error message.
    """
    content = TEST_FILE.read_text()

    # Find the last occurrence of "]  # end SCENARIOS" marker or the last "]" before __main__
    marker = "]  # end SCENARIOS"
    if marker not in content:
        return f"ERROR: could not find insertion marker '{marker}' in test file."

    insertion_point = content.rfind(marker)
    new_content = (
        content[:insertion_point]
        + "    # Auto-generated regression test\n"
        + textwrap.indent(scenario_code.strip(), "    ")
        + ",\n"
        + content[insertion_point:]
    )
    TEST_FILE.write_text(new_content)
    return "OK"


@tool
def git_commit_and_deploy(commit_message: str, deploy: bool = True) -> str:
    """Stage visibility.py and test file, create a git commit, optionally deploy.

    Only call this AFTER run_tests() returns PASSED.
    """
    files = ["utils/visibility.py", "tests/test_visibility_accuracy.py"]
    for f in files:
        subprocess.run(["git", "add", f], cwd=ROOT)

    result = subprocess.run(
        ["git", "commit", "-m", commit_message],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode != 0:
        return f"Commit failed: {result.stderr}"

    if deploy:
        dep = subprocess.run(
            ["railway", "up", "--detach"],
            capture_output=True, text=True, cwd=ROOT,
        )
        if dep.returncode == 0:
            return f"Committed and deployed. {dep.stdout.strip()}"
        return f"Committed but deploy failed: {dep.stderr}"

    return "Committed (deploy skipped)."


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS = [
    read_visibility_code,
    apply_edit,
    revert_edit,
    run_tests,
    add_test_case,
    git_commit_and_deploy,
]

TOOL_MAP = {t.name: t for t in TOOLS}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a precision code-repair agent for a geometric visibility filter written in Python.

The filter lives in utils/visibility.py. It decides whether a point-of-interest (POI)
is visible to a user walking down a street, based on:
  • size category (very_large / large / medium / small)
  • distance and field-of-view rules
  • cross-street suppression (medium POIs on confirmed different streets → hidden)
  • occlusion (large building in the same sightline and closer → blocks)
  • landmark 1.5× distance boost for stadiums, towers, monuments, etc.

You will be given the FULL DIAGNOSIS of a false positive or false negative report.
The diagnosis tells you:
  • What the filter decided  (filter_now_says: YES/NO)
  • What the user observed   (user_says: YES/NO)
  • Which rule fired         (rule + rule_description)
  • Why the size was chosen  (size_reason)
  • Street matching details  (street_info)

Your task:
  1. Read visibility.py to understand the current code.
  2. Identify the MINIMAL change that fixes this specific case without breaking others.
  3. Apply the edit with apply_edit (old_string → new_string).
  4. Run run_tests(). If FAILED, revert with revert_edit() and explain what needs manual review.
  5. If PASSED, add a regression test case with add_test_case().
  6. Call git_commit_and_deploy() with a clear commit message (unless dry_run is True).

Rules for edits:
  • Only touch utils/visibility.py — no other source files.
  • Prefer the smallest possible change: adding one line to _TAG_SIZES is better than
    rewriting _is_visible.
  • Never change distance thresholds by more than 20% unless the diagnosis clearly shows
    the current threshold is wrong for multiple cases.
  • If you're unsure whether a fix is correct, prefer NOT fixing and explain why.

DECISION TREE — work through these in order, stop at the first match:

  1. size_reason says "no height/floors/footprint/tag match → medium (default)"
     AND the POI has a leisure/amenity/tourism/building tag that isn't in _TAG_SIZES yet?
     → ADD that tag value to _TAG_SIZES with an appropriate size.
       Sizing guide: stadiums/arenas → very_large, cathedrals/universities → large,
       museums/theatres/churches → medium, cafes/shops/fountains/marinas → small.
       Example fix (adding leisure=marina as small):
         old: '    "fountain":         "small",\n}'
         new: '    "fountain":         "small",\n    "marina":           "small",\n}'

  2. size_reason says "tag heuristic" but the size is wrong?
     → CORRECT the existing _TAG_SIZES entry.

  3. is_landmark=False but this is clearly a landmark (tower, monument, castle…)?
     → ADD to _LANDMARK_TYPES frozenset.

  4. poi_type is empty even though relevant tags exist?
     → EXTEND the or-chain in filter_visible (historic → man_made → tourism → amenity → …).

  5. cross_street=True and user says YES (they can see it despite different street)?
     → Only adjust if size is large/very_large — those are already exempt.
       For medium, consider if the addr:street data is wrong rather than changing the rule.

  6. Distance threshold is clearly wrong (user can see it at 350m for a medium POI)?
     → Adjust the threshold in _is_visible by ≤20%.

IMPORTANT: Do NOT touch cross-street logic unless the diagnosis explicitly shows
cross_street=True AND the user_says=YES. For cases where the size is wrong (default medium
when it should be small), the fix is ALWAYS _TAG_SIZES, not cross-street logic.

Test case format for add_test_case() — use EXACTLY this structure:
  scenario("description of fix",
      user_lat, user_lon, heading, "Street Name", [
      poi("id", "POI Name", poi_lat, poi_lon,
          {"leisure": "marina"}, "NO"),
  ])

The poi() helper signature is: poi(id, name, lat, lon, tags_dict, expected)
The scenario() helper signature is: scenario(desc, user_lat, user_lon, heading, street, pois_list)

After fixing, write the commit message as:
  "fix(visibility): <one sentence describing the exact rule that was wrong>"
"""

# ---------------------------------------------------------------------------
# Agent graph nodes
# ---------------------------------------------------------------------------

def _llm() -> ChatGroq:
    model_name = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    return ChatGroq(
        model=model_name,
        api_key=os.environ["GROQ_API_KEY"],
        temperature=0,
    ).bind_tools(TOOLS)


def agent_node(state: FixState) -> dict:
    """LLM step: decide next action."""
    model = _llm()
    response = model.invoke(state["messages"])
    return {"messages": [response]}


def tool_node(state: FixState) -> dict:
    """Execute the tool calls requested by the LLM."""
    last = state["messages"][-1]
    results = []
    for tc in last.tool_calls:
        name = tc["name"]
        args = tc["args"]

        # Honour dry_run: skip destructive tools
        if state["dry_run"] and name in ("apply_edit", "git_commit_and_deploy"):
            output = f"[DRY RUN] Would call {name}({args})"
        elif state["no_deploy"] and name == "git_commit_and_deploy":
            # Still commit but skip deploy
            args = {**args, "deploy": False}
            output = TOOL_MAP[name].invoke(args)
        else:
            output = TOOL_MAP[name].invoke(args)

        results.append(ToolMessage(content=str(output), tool_call_id=tc["id"]))

    # Track success signals — must be returned as state updates, not mutations
    updates: dict = {"messages": results}
    last_tool = last.tool_calls[-1]["name"] if last.tool_calls else ""
    last_output = str(results[-1].content) if results else ""
    if last_tool == "git_commit_and_deploy" and "Committed" in last_output:
        updates["fixed"]    = True
        updates["deployed"] = "deployed" in last_output.lower()

    return updates


def should_continue(state: FixState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_agent() -> object:
    g = StateGraph(FixState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tool_node)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


# ---------------------------------------------------------------------------
# Run one fix
# ---------------------------------------------------------------------------

def fix_one(entry: dict, dry_run: bool, no_deploy: bool) -> bool:
    """Run the agent on a single feedback entry. Returns True if fixed."""
    print(f"\n{'='*70}")
    print(f"POI:         {entry.get('diag_poi_name')}")
    print(f"user_says:   {entry.get('user_says')}")
    print(f"filter_says: {entry.get('diag_filter_now_says')}")
    print(f"rule:        {entry.get('diag_rule')}")
    print(f"size:        {entry.get('diag_size')} — {entry.get('diag_size_reason')}")
    print(f"distance_m:  {entry.get('diag_distance_m')}")
    print(f"street_info: {entry.get('diag_street_info')}")
    print(f"note:        {entry.get('note')}")
    print(f"{'='*70}")

    human_msg = f"""
FEEDBACK REPORT
===============
POI name:        {entry.get('diag_poi_name')}
POI type:        {entry.get('diag_poi_type')}
user_says:       {entry.get('user_says')}    ← what the user actually observed
filter_now_says: {entry.get('diag_filter_now_says')}    ← what the code currently outputs
user note:       {entry.get('note') or '(none)'}

FULL DIAGNOSIS
==============
distance_m:      {entry.get('diag_distance_m')}
bearing_deg:     {entry.get('diag_bearing_deg')}
angle_deg:       {entry.get('diag_angle_deg')}
in_fov:          {entry.get('diag_in_fov')}
size:            {entry.get('diag_size')}
size_reason:     {entry.get('diag_size_reason')}
is_landmark:     {entry.get('diag_is_landmark')}
dist_mult:       {entry.get('diag_dist_mult')}
same_street:     {entry.get('diag_same_street')}
cross_street:    {entry.get('diag_cross_street')}
street_info:     {entry.get('diag_street_info')}
aspect_conf:     {entry.get('diag_aspect_conf')}
rule:            {entry.get('diag_rule')}
rule_description:{entry.get('diag_rule_description')}
confidence:      {entry.get('diag_confidence')}

Please fix this false {'positive' if entry.get('user_says') == 'NO' else 'negative'}.
{'DRY RUN MODE — do not call apply_edit or git_commit_and_deploy.' if dry_run else ''}
"""

    graph = build_agent()
    initial_state: FixState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=human_msg),
        ],
        "diagnosis": entry,
        "dry_run":   dry_run,
        "no_deploy": no_deploy,
        "fixed":     False,
        "deployed":  False,
    }

    final = graph.invoke(initial_state, config={"recursion_limit": 20})

    # Print agent's final message
    last = final["messages"][-1]
    if isinstance(last, AIMessage):
        print(f"\nAgent conclusion:\n{last.content}")

    return final.get("fixed", False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _fetch_from_api(api_base: str) -> list[dict]:
    """Pull DISAGREE entries from the live Railway API."""
    import urllib.request
    url = f"{api_base.rstrip('/')}/v1/feedback?agreement=DISAGREE&limit=100"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())["entries"]


def _load_from_file(log_path: Path) -> list[dict]:
    """Load DISAGREE entries from a local NDJSON file."""
    entries = []
    for line in log_path.read_text().splitlines():
        try:
            e = json.loads(line)
            if e.get("agreement") == "DISAGREE":
                entries.append(e)
        except Exception:
            pass
    return entries


def main():
    parser = argparse.ArgumentParser(description="Auto-fix visibility filter issues from feedback log")
    parser.add_argument("--api",       default="https://tourai-agent-production.up.railway.app",
                        help="API base URL to pull feedback from (default: Railway production)")
    parser.add_argument("--log",       default=None,
                        help="Local NDJSON log path (overrides --api if set)")
    parser.add_argument("--dry-run",   action="store_true", help="Propose fixes only, don't apply")
    parser.add_argument("--no-deploy", action="store_true", help="Commit fixes but don't deploy")
    parser.add_argument("--limit",     type=int, default=5, help="Max entries to process per run")
    args = parser.parse_args()

    # Load already-processed entry timestamps
    processed = set()
    if PROCESSED_LOG.exists():
        for line in PROCESSED_LOG.read_text().splitlines():
            try:
                processed.add(json.loads(line)["entry_ts"])
            except Exception:
                pass

    # Fetch entries — prefer local file if explicitly specified, otherwise pull from API
    if args.log:
        log_path = ROOT / args.log
        if not log_path.exists():
            print(f"No feedback log found at {log_path}")
            return
        all_entries = _load_from_file(log_path)
        print(f"Loaded {len(all_entries)} DISAGREE entries from {log_path}")
    else:
        try:
            all_entries = _fetch_from_api(args.api)
            print(f"Fetched {len(all_entries)} DISAGREE entries from {args.api}")
        except Exception as exc:
            print(f"Failed to fetch from API ({exc}). Try --log <path> for a local file.")
            return

    entries = [e for e in all_entries if e.get("ts") not in processed]

    if not entries:
        print("No unprocessed DISAGREE entries found.")
        return

    print(f"Found {len(entries)} unprocessed DISAGREE entries. Processing up to {args.limit}.")

    fixed_count = 0
    for entry in entries[:args.limit]:
        try:
            fixed = fix_one(entry, dry_run=args.dry_run, no_deploy=args.no_deploy)
        except Exception as exc:
            print(f"\nERROR processing entry: {exc}")
            fixed = False

        # Mark as processed regardless of outcome (so we don't retry blindly)
        with PROCESSED_LOG.open("a") as fh:
            fh.write(json.dumps({
                "entry_ts":  entry.get("ts"),
                "poi_name":  entry.get("diag_poi_name"),
                "fixed":     fixed,
                "processed": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

        if fixed:
            fixed_count += 1

    print(f"\nDone. Fixed {fixed_count}/{min(len(entries), args.limit)} issues.")


if __name__ == "__main__":
    main()
