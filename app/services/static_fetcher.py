from __future__ import annotations

from typing import Optional

import httpx
import structlog

from app.services.content import looks_like_js_rendered

logger = structlog.get_logger()

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


async def static_fetch(url: str, timeout_ms: int = 8000) -> Optional[dict]:
    """Fetch a URL with httpx (no JS). Returns None if non-200, non-HTML, or JS-rendered.

    Timeout lowered 15s → 8s: most static pages respond in <2s. Failing fast
    lets the chain move to Playwright sooner on slow/dead sites.
    """
    timeout = timeout_ms / 1000.0
    try:
        # Shared client — saves ~20-50ms TLS setup per call. http2=True adds
        # multiplexing for hosts we hit repeatedly during crawls.
        from app.services.http_client import get_shared_client
        client = await get_shared_client()
        resp = await client.get(url, headers=_BROWSER_HEADERS, timeout=timeout, follow_redirects=True)

    except Exception as exc:
        logger.debug("static_fetch error", url=url, error_type=type(exc).__name__)
        return None

    if resp.status_code != 200:
        logger.debug("static_fetch non-200", url=url, status=resp.status_code)
        return None

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        logger.debug("static_fetch non-HTML", url=url, content_type=content_type)
        return None

    html = resp.text
    if looks_like_js_rendered(html):
        logger.debug("static_fetch JS-rendered page", url=url)
        return None

    return {
        "html": html,
        "status_code": resp.status_code,
        "final_url": str(resp.url),
    }
