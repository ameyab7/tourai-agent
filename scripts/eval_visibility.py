#!/usr/bin/env python3
"""
scripts/eval_visibility.py — Precision / recall analysis for the visibility filter.

Reads ground-truth feedback from feedback_log.ndjson (or a Railway API endpoint)
and breaks down filter accuracy by rule bucket, size, and distance band.

Usage:
    python scripts/eval_visibility.py                        # local file
    python scripts/eval_visibility.py --api                  # pull from deployed API
    python scripts/eval_visibility.py --file my_walk.ndjson  # custom file
    python scripts/eval_visibility.py --min-samples 3        # hide thin buckets
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE     = "https://tourai-agent-production.up.railway.app"
DEFAULT_FILE = Path("feedback_log.ndjson")

DISTANCE_BANDS = [
    (0,    50,   "0–50 m"),
    (50,   150,  "50–150 m"),
    (150,  300,  "150–300 m"),
    (300,  600,  "300–600 m"),
    (600,  9999, ">600 m"),
]


# ── Rule bucketing ────────────────────────────────────────────────────────────

def rule_bucket(rule: str) -> str:
    """Map a free-form reason string to a short readable bucket label."""
    if not rule or rule == "clear":
        return "clear (passed)"
    r = rule.lower()
    if r.startswith("fov:"):
        return "fov — outside ±90°"
    if "heuristic+raycast" in r or "blocked by" in r:
        return "ray_cast — building blocked"
    if r.startswith("recog:"):
        return "recog — too far to identify"
    if r.startswith("heuristic:") and "within" in r:
        return "clear (heuristic pass)"
    if r.startswith("heuristic:"):
        return "heuristic — size/distance"
    if r.startswith("park:") and ">" in r:
        return "park — too far from boundary"
    if r.startswith("park:"):
        return "clear (park pass)"
    if r.startswith("skyline:") and "outside" in r:
        return "skyline — outside FOV"
    if r.startswith("skyline:") and "too far" in r:
        return "skyline — too far"
    if r.startswith("skyline:"):
        return "clear (skyline pass)"
    # Anything else is likely a building name from ray casting
    return "ray_cast — building blocked"


def dist_band(meters: float) -> str:
    for lo, hi, label in DISTANCE_BANDS:
        if lo <= meters < hi:
            return label
    return ">600 m"


# ── Data loading ──────────────────────────────────────────────────────────────

def load_from_file(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"File not found: {path}")
    entries = []
    with path.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [warn] skipping line {i}: {e}", file=sys.stderr)
    return entries


def load_from_api(limit: int = 1000) -> list[dict]:
    url = f"{API_BASE}/v1/feedback?limit={limit}"
    print(f"Fetching from {url} …")
    with urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    entries = data.get("entries", [])
    print(f"  {len(entries)} entries (total on server: {data.get('total', '?')})")
    return entries


# ── Metrics ───────────────────────────────────────────────────────────────────

class Bucket:
    __slots__ = ("tp", "fp", "fn", "tn")

    def __init__(self):
        self.tp = self.fp = self.fn = self.tn = 0

    def add(self, user_says: str, filter_says: str):
        u = user_says.upper()
        f = filter_says.upper()
        if   u == "YES" and f == "YES": self.tp += 1
        elif u == "NO"  and f == "YES": self.fp += 1
        elif u == "YES" and f == "NO":  self.fn += 1
        elif u == "NO"  and f == "NO":  self.tn += 1

    @property
    def total(self):
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self):
        d = self.tp + self.fp
        return self.tp / d if d else None

    @property
    def recall(self):
        d = self.tp + self.fn
        return self.tp / d if d else None

    @property
    def f1(self):
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def accuracy(self):
        return (self.tp + self.tn) / self.total if self.total else None


def fmt_pct(v) -> str:
    return f"{v*100:5.1f}%" if v is not None else "   n/a"


# ── Report printing ───────────────────────────────────────────────────────────

def print_table(title: str, buckets: dict[str, Bucket], min_samples: int):
    rows = [(k, b) for k, b in sorted(buckets.items()) if b.total >= min_samples]
    if not rows:
        print(f"\n{title}\n  (no buckets with ≥{min_samples} samples)")
        return

    col = max(len(k) for k, _ in rows)
    header = f"{'Bucket':{col}}  {'n':>4}  {'TP':>4}  {'FP':>4}  {'FN':>4}  {'TN':>4}  {'Prec':>6}  {'Rec':>6}  {'F1':>6}  {'Acc':>6}"
    bar = "─" * len(header)
    print(f"\n{title}")
    print(bar)
    print(header)
    print(bar)
    for k, b in rows:
        flag = ""
        if b.precision is not None and b.precision < 0.6:
            flag = "  ← low precision"
        if b.recall is not None and b.recall < 0.6:
            flag += "  ← low recall"
        print(
            f"{k:{col}}  {b.total:>4}  {b.tp:>4}  {b.fp:>4}  {b.fn:>4}  {b.tn:>4}"
            f"  {fmt_pct(b.precision)}  {fmt_pct(b.recall)}  {fmt_pct(b.f1)}  {fmt_pct(b.accuracy)}"
            + flag
        )
    print(bar)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Visibility filter precision/recall analysis")
    parser.add_argument("--api",         action="store_true",  help="Pull entries from deployed API")
    parser.add_argument("--file",        type=Path, default=DEFAULT_FILE, help="NDJSON file path")
    parser.add_argument("--min-samples", type=int,  default=2, help="Hide buckets with fewer entries")
    args = parser.parse_args()

    entries = load_from_api() if args.api else load_from_file(args.file)

    if not entries:
        sys.exit("No entries found.")

    # ── Parse entries ─────────────────────────────────────────────────────────
    skipped = 0
    by_rule  = defaultdict(Bucket)
    by_size  = defaultdict(Bucket)
    by_dist  = defaultdict(Bucket)
    overall  = Bucket()

    for e in entries:
        user_says   = e.get("user_says", "")
        filter_says = e.get("diag_filter_now_says", "")
        rule        = e.get("diag_rule", "")
        size        = e.get("diag_size", "unknown") or "unknown"
        dist        = e.get("diag_distance_m")

        if user_says not in ("YES", "NO") or filter_says not in ("YES", "NO"):
            skipped += 1
            continue

        rb = rule_bucket(rule)
        db = dist_band(float(dist)) if dist is not None else "unknown"

        by_rule[rb].add(user_says, filter_says)
        by_size[size].add(user_says, filter_says)
        by_dist[db].add(user_says, filter_says)
        overall.add(user_says, filter_says)

    print(f"\n{'═'*60}")
    print(f"  Visibility Filter  ·  Ground Truth Evaluation")
    print(f"{'═'*60}")
    print(f"  Total entries : {len(entries)}")
    print(f"  Valid         : {overall.total}  (skipped {skipped} malformed)")
    print(f"  TP={overall.tp}  FP={overall.fp}  FN={overall.fn}  TN={overall.tn}")
    print(f"  Overall  prec={fmt_pct(overall.precision)}  rec={fmt_pct(overall.recall)}"
          f"  F1={fmt_pct(overall.f1)}  acc={fmt_pct(overall.accuracy)}")

    min_s = args.min_samples
    print_table("By Rule / Rejection Reason", by_rule, min_s)
    print_table("By Size Bucket",             by_size, min_s)
    print_table("By Distance Band",           by_dist, min_s)

    # ── Worst offenders ───────────────────────────────────────────────────────
    print("\nWorst offenders (FP + FN ≥ 2):")
    found = False
    for buckets, label in [(by_rule, "rule"), (by_size, "size"), (by_dist, "dist")]:
        for k, b in sorted(buckets.items(), key=lambda x: x[1].fp + x[1].fn, reverse=True):
            if b.fp + b.fn >= 2:
                print(f"  [{label}] {k:40s}  FP={b.fp}  FN={b.fn}")
                found = True
    if not found:
        print("  (none yet — keep walking and collecting data)")

    print()


if __name__ == "__main__":
    main()
