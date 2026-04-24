"""Per-domain strategy memoization + synthetic probe admin endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_admin, verify_api_key
from app.services import probe, strategy

router = APIRouter(tags=["strategy"])


@router.get("/strategy")
async def list_strategies(_: dict = Depends(verify_api_key)) -> dict:
    rows = await strategy.get_all()
    return {"count": len(rows), "domains": rows}


@router.post("/strategy/probe")
async def run_probe(_admin: dict = Depends(require_admin)) -> dict:
    """Run the weekly synthetic chain-health probe.

    Scrapes a fixed list of canary URLs with force_refresh enabled, reports
    which chain arm won for each. Use to confirm non-preferred methods still
    work before you actually need them in production.
    """
    return await probe.run_probe()
