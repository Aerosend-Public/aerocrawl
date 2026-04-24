from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth import verify_api_key
from app.db import get_usage_stats

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
async def get_usage(
    days: int = Query(default=30, ge=1, le=365),
    api_key: dict = Depends(verify_api_key),
) -> dict:
    stats = await get_usage_stats(api_key["id"], days=days)
    return {"key_id": api_key["id"], "days": days, **stats}
