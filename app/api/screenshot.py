from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth import verify_api_key
from app.db import log_usage
from app.services.scraper import browser_pool as _get_pool
from app.validation import validate_url

router = APIRouter(tags=["screenshot"])


class ViewportConfig(BaseModel):
    width: int = 1280
    height: int = 720


class ScreenshotRequest(BaseModel):
    url: str
    proxy: Optional[str] = None
    wait_for: Optional[str] = None
    timeout_ms: Optional[int] = None
    full_page: bool = False
    viewport: Optional[ViewportConfig] = None


@router.post("/screenshot")
async def screenshot(
    body: ScreenshotRequest,
    api_key: dict = Depends(verify_api_key),
):
    from app.services.scraper import _resolve_proxy, browser_pool

    validate_url(body.url)

    if browser_pool is None:
        return {"success": False, "error": "Browser pool not initialized"}

    start = time.monotonic()
    proxy_url = _resolve_proxy(body.proxy) if body.proxy else ""
    viewport = (
        {"width": body.viewport.width, "height": body.viewport.height}
        if body.viewport
        else None
    )
    wait_for = body.wait_for or "networkidle"
    timeout_ms = body.timeout_ms or 30000

    error_msg: Optional[str] = None
    screenshot_bytes: bytes = b""

    try:
        context = await browser_pool.acquire(proxy_url=proxy_url, viewport=viewport)
        try:
            page = await context.new_page()
            try:
                await page.goto(body.url, wait_until=wait_for, timeout=timeout_ms)
                screenshot_bytes = await page.screenshot(full_page=body.full_page)
            finally:
                await page.close()
        finally:
            await browser_pool.release(context)
    except Exception as exc:
        error_msg = str(exc)

    duration_ms = int((time.monotonic() - start) * 1000)

    await log_usage(
        key_id=api_key["id"],
        endpoint="/screenshot",
        url=body.url,
        duration_ms=duration_ms,
        error=error_msg,
    )

    if error_msg:
        return {"success": False, "error": error_msg, "duration_ms": duration_ms}

    return Response(content=screenshot_bytes, media_type="image/png")
