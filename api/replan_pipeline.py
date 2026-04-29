"""tourai/api/replan_pipeline.py

SSE pipeline for re-planning a single day of an existing plan.

Flow:
  loading   → load PlanSnapshot from plan_store
  mutating  → apply constraint changes
  narrating → re-narrate the affected day only
  saving    → persist updated snapshot
  complete  → emit full updated plan + diff changes summary
"""

from __future__ import annotations

import json
import logging
import traceback

from api.models import ReplanRequest
from narration.narrator import narrate_replanned_day
from replan.diff import compute_day_diff, summarize_diff
from replan.mutator import mutate_constraints, summarize_mutation
from storage.plan_store import PlanSnapshot, _deserialize_bundle, plan_store
from validation.validator import FinalDay, _merge_day

logger = logging.getLogger("tourai.replan")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def run_replan_pipeline(plan_id: str, request: ReplanRequest):
    """Async generator yielding SSE events for a single-day re-plan."""
    # Deferred import breaks the circular dependency with api.pipeline
    # (pipeline.py defines the endpoint that imports this module).
    from api.pipeline import _skeleton_from_dict, _skeleton_to_dict

    yield _sse({"type": "stage", "stage": "loading", "message": "Loading your plan…"})

    snapshot = await plan_store.load(plan_id)
    if snapshot is None:
        yield _sse({"type": "error", "message": f"Plan {plan_id!r} not found or expired."})
        return

    skeleton = _skeleton_from_dict(snapshot.skeleton_dict)
    bundle = _deserialize_bundle(snapshot.bundle_dict)
    interests = list(snapshot.request.interests)
    day_index = request.day_index

    if day_index < 0 or day_index >= len(skeleton.days):
        yield _sse({
            "type": "error",
            "message": f"day_index {day_index} out of range (plan has {len(skeleton.days)} days).",
        })
        return

    # Capture the original FinalDay before mutation for diff computation later.
    original_days = snapshot.final_plan.get("days", [])
    before_day: FinalDay | None = None
    if day_index < len(original_days):
        try:
            before_day = FinalDay.model_validate(original_days[day_index])
        except Exception:
            before_day = None

    yield _sse({"type": "stage", "stage": "mutating", "message": "Applying your changes…"})
    try:
        new_skeleton, mutation_log = mutate_constraints(skeleton, bundle, request)
    except Exception:
        logger.error("mutate_failed", extra={"plan_id": plan_id, "exc": traceback.format_exc()})
        yield _sse({"type": "error", "message": "Failed to apply changes."})
        return

    yield _sse({"type": "stage", "stage": "narrating", "message": f"Re-writing Day {day_index + 1}…"})
    try:
        narration = await narrate_replanned_day(
            day_index, new_skeleton.days[day_index], bundle, interests, mutation_log
        )
    except Exception:
        logger.error("narrate_failed", extra={"plan_id": plan_id, "exc": traceback.format_exc()})
        narration = None

    merged_day = _merge_day(day_index, new_skeleton.days[day_index], narration, bundle)
    yield _sse({"type": "day", "day_index": day_index, "day": merged_day.model_dump()})

    # Compute structural diff between old and new day.
    diff: dict = {}
    if before_day is not None:
        try:
            diff = compute_day_diff(before_day, merged_day)
        except Exception:
            logger.warning("diff_failed", extra={"plan_id": plan_id, "exc": traceback.format_exc()})

    yield _sse({
        "type": "diff",
        "day_index": day_index,
        "diff": diff,
        "summary": summarize_diff(diff),
    })

    yield _sse({"type": "stage", "stage": "saving", "message": "Saving updated plan…"})

    updated_plan = dict(snapshot.final_plan)
    updated_days = list(updated_plan.get("days", []))
    if day_index < len(updated_days):
        updated_days[day_index] = merged_day.model_dump()
    updated_plan["days"] = updated_days

    updated_snapshot = PlanSnapshot(
        plan_id=plan_id,
        user_id=snapshot.user_id,
        created_at=snapshot.created_at,
        request=snapshot.request,
        skeleton_dict=_skeleton_to_dict(new_skeleton),
        bundle_dict=snapshot.bundle_dict,
        final_plan=updated_plan,
    )
    await plan_store.save(plan_id, updated_snapshot)

    yield _sse({
        "type": "complete",
        "plan": updated_plan,
        "plan_id": plan_id,
        "changes": {
            "day_index": day_index,
            "diff": diff,
            "mutation_summary": summarize_mutation(mutation_log),
        },
    })
