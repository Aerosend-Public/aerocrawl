"""Cache management endpoints: stats, per-URL purge, namespace purge."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth import require_admin, verify_api_key
from app.services import cache

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
async def cache_stats(_: dict = Depends(verify_api_key)) -> dict:
    return await cache.stats()


@router.delete("")
async def purge_url(
    url: str = Query(..., description="URL to invalidate (all format variants)"),
    _admin: dict = Depends(require_admin),
) -> dict:
    count = await cache.invalidate(url)
    return {"url": url, "keys_deleted": count}


@router.delete("/purge-all")
async def purge_namespace(_admin: dict = Depends(require_admin)) -> dict:
    """Delete every cache entry in the current namespace. Use when a schema
    change or a poisoned dataset requires a blanket reset."""
    count = await cache.purge_all()
    return {"keys_deleted": count}
