"""tourai/replan/diff.py

Pure-function diff between a FinalDay before and after re-planning.

Produces a structured dict the UI uses to highlight what changed:
  swapped      — a before stop was replaced by an after stop in the same time slot
  dropped      — a before stop has no counterpart in the after day
  added        — an after stop has no counterpart in the before day
  time_shifted — the same stop (same poi_id) shifted its arrival_time

Meal slots are matched by meal type (breakfast / lunch / dinner), NOT by
poi_id. The poi_id (meal-breakfast-YYYY-MM-DD) stays the same but the
chosen restaurant (name) changes when narration re-picks — that manifests
as a "swapped" pair so the UI can show "Old Diner → New Cafe".
"""

from __future__ import annotations

from validation.validator import FinalDay, FinalStop

_SWAP_WINDOW_MIN = 60  # stops within 60 min of each other can be paired as a swap


# ── Internal helpers ──────────────────────────────────────────────────────────

def _time_to_min(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _meal_type(stop: FinalStop) -> str:
    """Extract 'breakfast', 'lunch', or 'dinner' from the poi_id."""
    parts = stop.poi_id.split("-")
    return parts[1] if len(parts) >= 2 else stop.poi_id


# ── Public API ────────────────────────────────────────────────────────────────

def compute_day_diff(before: FinalDay, after: FinalDay) -> dict:
    """Return a diff dict describing structural changes between two FinalDay versions.

    Pure function — no I/O, no side effects.
    """
    swapped: list[dict] = []
    added: list[dict] = []
    dropped: list[dict] = []
    time_shifted: list[dict] = []

    # ── Separate meal vs non-meal stops ───────────────────────────────────────
    before_reg   = [s for s in before.stops if not s.is_meal]
    after_reg    = [s for s in after.stops  if not s.is_meal]
    before_meals = [s for s in before.stops if s.is_meal]
    after_meals  = [s for s in after.stops  if s.is_meal]

    # ── Non-meal stops ────────────────────────────────────────────────────────
    before_by_id: dict[str, FinalStop] = {s.poi_id: s for s in before_reg}
    after_by_id:  dict[str, FinalStop] = {s.poi_id: s for s in after_reg}

    both_ids    = set(before_by_id) & set(after_by_id)
    only_before = sorted(set(before_by_id) - both_ids)  # sorted → deterministic iteration
    only_after  = sorted(set(after_by_id)  - both_ids)

    # Time-shifted: same poi_id present in both, arrival_time changed
    for pid in sorted(both_ids):
        b, a = before_by_id[pid], after_by_id[pid]
        if b.arrival_time != a.arrival_time:
            time_shifted.append({"poi_id": pid, "before": b.arrival_time, "after": a.arrival_time})

    # Swap matching: greedy, within _SWAP_WINDOW_MIN minutes
    # For each unmatched before stop, find the nearest unmatched after stop in time.
    matched_after: set[str] = set()

    for b_id in only_before:
        bs = before_by_id[b_id]
        bs_min = _time_to_min(bs.arrival_time)
        best: FinalStop | None = None
        best_delta = float("inf")

        for a_id in only_after:
            if a_id in matched_after:
                continue
            delta = abs(bs_min - _time_to_min(after_by_id[a_id].arrival_time))
            if delta <= _SWAP_WINDOW_MIN and delta < best_delta:
                best_delta = delta
                best = after_by_id[a_id]

        if best is not None:
            matched_after.add(best.poi_id)
            swapped.append({
                "before":       {"name": bs.name,   "poi_id": bs.poi_id},
                "after":        {"name": best.name,  "poi_id": best.poi_id},
                "arrival_time": bs.arrival_time,
            })
        else:
            dropped.append({"name": bs.name, "poi_id": bs.poi_id})

    for a_id in only_after:
        if a_id not in matched_after:
            s = after_by_id[a_id]
            added.append({"name": s.name, "poi_id": s.poi_id, "arrival_time": s.arrival_time})

    # ── Meal stops — matched by type, not poi_id ──────────────────────────────
    before_by_type = {_meal_type(s): s for s in before_meals}
    after_by_type  = {_meal_type(s): s for s in after_meals}

    for mt in sorted(set(before_by_type) | set(after_by_type)):
        b = before_by_type.get(mt)
        a = after_by_type.get(mt)

        if b and a:
            if b.name != a.name:
                # Restaurant pick changed
                swapped.append({
                    "before":       {"name": b.name, "poi_id": b.poi_id},
                    "after":        {"name": a.name, "poi_id": a.poi_id},
                    "arrival_time": b.arrival_time,
                })
            elif b.arrival_time != a.arrival_time:
                time_shifted.append({"poi_id": b.poi_id, "before": b.arrival_time, "after": a.arrival_time})
        elif b:
            dropped.append({"name": b.name, "poi_id": b.poi_id})
        else:
            assert a is not None
            added.append({"name": a.name, "poi_id": a.poi_id, "arrival_time": a.arrival_time})

    return {"swapped": swapped, "added": added, "dropped": dropped, "time_shifted": time_shifted}


def summarize_diff(diff: dict) -> str:
    """One-line summary for display, e.g. 'Swapped 2 stops, shifted 3 times'."""
    parts: list[str] = []
    if diff.get("swapped"):
        n = len(diff["swapped"])
        parts.append(f"Swapped {n} stop{'s' if n > 1 else ''}")
    if diff.get("dropped"):
        n = len(diff["dropped"])
        parts.append(f"Dropped {n} stop{'s' if n > 1 else ''}")
    if diff.get("added"):
        n = len(diff["added"])
        parts.append(f"Added {n} stop{'s' if n > 1 else ''}")
    if diff.get("time_shifted"):
        n = len(diff["time_shifted"])
        parts.append(f"Shifted {n} time{'s' if n > 1 else ''}")
    return ", ".join(parts) if parts else "No changes"
