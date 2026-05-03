# Prefetch Module

Data fetching pipeline - Stage 1 of itinerary generation.

```
prefetch/
├── orchestrator.py  # Two-stage prefetch: hotels → restaurants/attractions
└── distance.py     # Haversine distance calculations
```

**Key functions:**
- `prefetch_all(destination, dates, interests, api_key)` — Main entry point
- `distance_provider.matrix(points)` — Distance matrix for POIs
- `_attractions()`, `_restaurants()`, `_hotels()`, `_weather()` — Individual fetchers

**Two-stage prefetch:**
1. Fetch hotels around destination → pick best hotel (by stars)
2. Fetch restaurants around **picked hotel** (not destination center)
3. Fetch attractions around destination center

This ensures restaurants are near where user stays, not near the geocoded center.