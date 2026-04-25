"""
api/main.py — App factory + lifespan.

Run:
  uvicorn api.main:app --port 8000
  (from repo root so utils/ resolves correctly)

Module layout:
  api/config.py        — Settings
  api/logging_setup.py — JSON logging + correlation ID
  api/cache.py         — MemoryCache + cache keys + sweep loop
  api/metrics.py       — Prometheus counters/histograms + timed()
  api/middleware.py    — observability middleware + rate limiter
  api/models.py        — Pydantic request/response models
  api/routes/pois.py     — /v1/visible-pois, /v1/current-street
  api/routes/ask.py      — /v1/ask
  api/routes/story.py    — /v1/story
  api/routes/health.py   — /health, /metrics, /debug
  api/routes/feedback.py — /v1/feedback
"""

import asyncio
import os
import sys

# Project root on sys.path so `utils/` imports resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

load_dotenv()

from api.cache import cache_sweep_loop
from api.config import settings
from api.logging_setup import setup_logging
from api.middleware import observability_middleware
from api.routes import ask, feedback, health, itinerary, itinerary_agent, pois, profile, recommendations, route, story

logger = setup_logging(settings.log_file)

# Optional Sentry
try:
    import sentry_sdk
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.1,
            environment="production" if not settings.debug else "development",
        )
        logger.info("sentry_enabled")
except ImportError:
    pass


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from api.migrations import run_migrations
    run_migrations(settings.database_url)
    asyncio.create_task(cache_sweep_loop())
    yield


app = FastAPI(
    title="TourAI API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)
app.middleware("http")(observability_middleware)

app.include_router(pois.router)
app.include_router(ask.router)
app.include_router(story.router)
app.include_router(health.router)
app.include_router(feedback.router)
app.include_router(route.router)
app.include_router(profile.router)
app.include_router(recommendations.router)
app.include_router(itinerary.router)
app.include_router(itinerary_agent.router)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=True)
