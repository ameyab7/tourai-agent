"""api/config.py — Application settings loaded from environment / .env file."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    groq_api_key:     str = Field(..., alias="GROQ_API_KEY")
    gemini_api_key:   str = Field("", alias="GEMINI_API_KEY")
    geoapify_api_key: str = Field("", alias="GEOAPIFY_API_KEY")

    supabase_url:         str = Field("", alias="SUPABASE_URL")
    supabase_service_key: str = Field("", alias="SUPABASE_SERVICE_KEY")
    database_url:         str = Field("", alias="DATABASE_URL")
    google_places_api_key: str = Field("", alias="GOOGLE_PLACES_API_KEY")

    @field_validator("geoapify_api_key", mode="before")
    @classmethod
    def _clean_geoapify_key(cls, v: str) -> str:
        # Railway can store keys with a stray leading ' =' (e.g. ' =abc123').
        # Strip surrounding whitespace and any leading '=' characters.
        return str(v).strip().lstrip("=").strip()

    cors_origins:    list[str] = ["http://localhost:3000", "http://localhost:8081"]
    rate_limit_rpm:  int = 100
    request_timeout: int = 30
    poi_cache_ttl:   int = 3600
    vis_cache_ttl:   int = 300
    area_cache_ttl:  int = 3600   # obstacle buildings cache — 1 hour (buildings don't move)

    debug:      bool = False  # enables /debug endpoint
    sentry_dsn: str  = ""     # optional — leave empty to disable
    log_file:   str  = ""     # optional — e.g. "logs/api.log"

    osrm_base_url:      str = "http://router.project-osrm.org"
    overpass_local_url: str = "http://localhost:12345/api/interpreter"

    model_config = SettingsConfigDict(
        env_file=".env",
        populate_by_name=True,
        extra="ignore",
    )


settings = Settings()
