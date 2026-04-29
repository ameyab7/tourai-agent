"""api/auth.py — FastAPI dependency for Supabase JWT auth."""

from __future__ import annotations

import asyncio
import logging

from fastapi import Header, HTTPException

from api.supabase_client import get_supabase

logger = logging.getLogger("tourai.auth")


async def get_current_user(authorization: str = Header(...)):
    """Validate a Supabase Bearer token and return the auth user."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        # get_user() is a synchronous HTTP call — run it off the event loop.
        resp = await asyncio.to_thread(get_supabase().auth.get_user, token)
        if resp.user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return resp.user
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth_failed", extra={"error": type(exc).__name__, "detail": str(exc)})
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_optional_user(authorization: str = Header(default="")):
    """Like get_current_user but returns None instead of raising on missing/invalid auth.

    Use this on endpoints where auth enhances the response but isn't required.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        resp = await asyncio.to_thread(get_supabase().auth.get_user, token)
        return resp.user if resp.user else None
    except Exception as exc:
        logger.warning("optional_auth_failed", extra={"error": type(exc).__name__, "detail": str(exc)})
        return None
