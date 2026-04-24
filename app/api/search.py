from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.db import log_usage
from app.services.search_scraper import search as search_fn

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    count: int = Field(default=10, ge=1, le=20)


@router.post("/search")
async def search(
    body: SearchRequest,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    result = await search_fn(
        query=body.query,
        count=body.count,
        cf_proxy_url=settings.CF_PROXY_URL,
    )

    await log_usage(
        key_id=api_key["id"],
        endpoint="/search",
        url=f"search:{body.query[:100]}",
        status_code=200 if result["success"] else 502,
        duration_ms=result["duration_ms"],
        scrape_method=result.get("search_engine", ""),
        error=result.get("error"),
    )

    return result
