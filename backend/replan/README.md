# Replan Module

In-trip plan modification logic.

```
replan/
├── mutator.py  # Plan modification strategies
└── diff.py     # Diff computation for changes
```

**Key functions:**
- `mutate_bad_weather(...)` — Swap outdoor activities for indoor
- `mutate_running_late(...)` — Drop activities to catch up
- `mutate_place_closed(...)` — Replace closed POI with similar
- `compute_day_diff(old, new)` — Compute what changed

**Mutation types:**
- `bad_weather` — Weather became rainy
- `running_late` — Behind schedule
- `place_closed` — POI closed
- `tired` — User too tired
- `free_text` — Custom request