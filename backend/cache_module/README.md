# Cache Module

TTL cache for pipeline data (skeleton, POIs, weather, etc.)

```
cache_module/
├── ttl_cache.py   # In-process TTL cache with async interface
└── keys.py        # Cache key generation (geocode, POI, skeleton, weather)
```

**Note:** This is separate from `api/cache.py` which handles visibility/POI caching for routes.

Key functions:
- `geocode_key(destination)` — Geocoding results
- `pois_key(lat, lon, radius)` — POI data
- `skeleton_key(...)` — Skeleton cache
- `weather_key(lat, lon, dates)` — Weather data