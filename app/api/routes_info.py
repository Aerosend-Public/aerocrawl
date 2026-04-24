"""Debug endpoint — which route handler would fire for a given URL?"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth import verify_api_key
from app.routes import list_routes, which_route

router = APIRouter(tags=["routes"])


@router.get("/route-info")
async def route_info(
    url: str = Query(..., description="URL to test against the route registry"),
    _: dict = Depends(verify_api_key),
) -> dict:
    """Report which smart-route handler (if any) would fire for this URL.

    Useful for debugging — tells you whether a URL will take the API fast-path
    or drop into the 9-step scrape chain.
    """
    matched = await which_route(url)
    return {
        "url": url,
        "matched_route": matched,
        "will_use_route": matched is not None,
        "registry": list_routes(),
    }
