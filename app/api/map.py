from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import verify_api_key
from app.services.mapper import discover_urls
from app.validation import validate_url

router = APIRouter(tags=["map"])


class MapRequest(BaseModel):
    url: str
    max_urls: int = 500
    include_paths: Optional[List[str]] = None
    exclude_paths: Optional[List[str]] = None
    include_subdomains: bool = False


@router.post("/map")
async def map_urls(
    body: MapRequest,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    validate_url(body.url)

    result = await discover_urls(
        url=body.url,
        max_urls=body.max_urls,
        include_paths=body.include_paths,
        exclude_paths=body.exclude_paths,
        include_subdomains=body.include_subdomains,
    )

    return {
        "success": True,
        "urls": result["urls"],
        "total": result["total"],
        "sources": result["sources"],
    }
