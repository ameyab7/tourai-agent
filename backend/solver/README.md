# Solver Module

Core itinerary building logic.

```
solver/
├── skeleton.py  # Build skeleton (day-by-day plan structure)
└── scorer.py    # POI scoring based on interests
```

**Key functions:**
- `build_skeleton(bundle, start_date, end_date, interests, pace, drive_tol_hrs)` — Creates skeleton
- `score_pois(pois, interests)` — Scores POIs by interest match
- `_finalize_day_ordering(...)` — Computes transit times