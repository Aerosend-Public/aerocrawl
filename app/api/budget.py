"""Budget / spend endpoints for paid providers (Zyte, etc)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import verify_api_key
from app.config import settings
from app.services import budget_guard

router = APIRouter(prefix="/budget", tags=["budget"])


@router.get("/zyte")
async def zyte_budget(_: dict = Depends(verify_api_key)) -> dict:
    summary = await budget_guard.monthly_summary("zyte")
    cap = settings.ZYTE_MONTHLY_BUDGET_USD
    spent = summary.get("spent_usd", 0.0)
    summary["cap_usd"] = cap
    summary["remaining_usd"] = max(0.0, round(cap - spent, 4))
    summary["at_cap"] = spent >= cap
    summary["allowlist"] = settings.zyte_allowlist
    return summary
