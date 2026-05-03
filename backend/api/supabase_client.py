"""api/supabase_client.py — lazy-initialised Supabase service-role client."""

from __future__ import annotations

from supabase import Client, create_client

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        from api.config import settings
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client
