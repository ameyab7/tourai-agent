# Narration Module

LLM prompting for itinerary generation.

```
narration/
└── narrator.py  # Day and trip-level narration prompts
```

**Key functions:**
- `narrate_day(day_index, day, bundle, interests)` — Generates day narration
- `narrate_trip(destination, interests, skeleton, bundle)` — Generates trip overview

**Prompts include:**
- Weather context
- Restaurant options for meal stops
- POI schedule
- Schema for output (tips, rain_plan, crowd_level, etc.)