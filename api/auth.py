"""api/auth.py — FastAPI dependency for Supabase JWT auth."""

from fastapi import Header, HTTPException

from api.supabase_client import get_supabase


async def get_current_user(authorization: str = Header(...)):
    """Validate a Supabase Bearer token and return the auth user."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        resp = get_supabase().auth.get_user(token)
        if resp.user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return resp.user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
