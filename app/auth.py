from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.db import get_key_by_hash
from app.services.rate_limit import check_and_increment


async def verify_api_key(request: Request) -> dict:
    """Extract API key from Authorization Bearer or X-API-Key, verify it, and
    enforce per-key rate limits. Admins bypass rate limits."""
    api_key: str | None = None

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header[7:].strip()

    if not api_key:
        api_key = request.headers.get("X-API-Key")

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    if not api_key.startswith("ns-"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_record = await get_key_by_hash(api_key)
    if key_record is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not key_record.get("active"):
        raise HTTPException(status_code=401, detail="API key revoked")

    # Per-key rate limit (admins bypass, missing Redis fails open).
    # Per-key overrides come from optional columns on api_keys; NULL = global default.
    await check_and_increment(
        key_id=key_record["id"],
        is_admin=bool(key_record.get("is_admin")),
        per_minute=key_record.get("rate_limit_per_minute"),
        per_hour=key_record.get("rate_limit_per_hour"),
    )

    return key_record


async def require_admin(api_key: dict = Depends(verify_api_key)) -> dict:
    """Require that the authenticated key has admin privileges."""
    if not api_key.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return api_key
