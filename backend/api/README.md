# API Structure

```
api/
├── main.py              # FastAPI app factory + lifespan
├── config.py            # Settings from environment (.env)
├── models.py            # Pydantic request/response schemas
├── cache.py             # Visibility/POI cache (in-memory)
├── metrics.py           # Prometheus metrics
├── middleware.py        # Rate limiting + observability
├── auth.py              # Supabase JWT validation
├── logging_setup.py     # JSON logging + correlation IDs
├── migrations.py        # Database schema setup
├── supabase_client.py   # Lazy Supabase client
├── pipeline.py          # /v2/itinerary/* endpoints
├── replan_pipeline.py  # /v2/itinerary/{id}/replan
└── routes/
    ├── health.py        # /health, /metrics, /debug
    ├── pois.py          # /v1/visible-pois, /v1/current-street
    ├── ask.py           # /v1/ask
    ├── story.py         # /v1/story
    ├── profile.py       # /v1/profile/*
    ├── feedback.py      # /v1/feedback
    ├── recommendations.py  # /v1/recommendations
    └── route.py         # /v1/route
```

## Key Distinctions

- **api/cache.py** — Simple in-memory cache for visibility/POI caching
- **cache_module/** — Separate TTL cache used by prefetch/orchestrator for API data

## Running

```bash
cd backend
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Environment

Required in `.env`:
- `GROQ_API_KEY` — LLM for narration
- `GEOAPIFY_API_KEY` — POI data (fallback: Overpass)
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` — Auth/profile storage
- `DATABASE_URL` — PostgreSQL for profiles