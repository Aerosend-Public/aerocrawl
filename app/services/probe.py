"""Synthetic probe — weekly health check of the 9-step fallback chain.

Scrapes a small fixed list of canary domains with force_refresh, reports
which method won for each. Surfaces silent rot on non-preferred methods
before they're needed in production.

Canaries are chosen to exercise different chain arms:
  - wikipedia.org   → static httpx should win
  - instantly.ai    → Playwright+stealth (JS-heavy SPA)
  - reddit.com      → CF worker (Reddit route)
  - github.com/...  → smart route (github API)
  - arxiv.org/abs/  → smart route (academic)
  - news.ycombinator.com → smart route (HN Algolia)
  - www.g2.com      → Zyte (hard unblocker test, only fires if allowlisted)
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import structlog

from app.services.scraper import scrape_url

logger = structlog.get_logger()

CANARY_URLS: list[str] = [
    "https://en.wikipedia.org/wiki/Web_scraping",
    "https://www.instantly.ai/",
    "https://www.reddit.com/r/coldemail/top.json",
    "https://github.com/anthropics/claude-code",
    "https://arxiv.org/abs/2310.06770",
    "https://news.ycombinator.com/news",
    "https://www.g2.com/products/lemlist/reviews",
]


async def run_probe(timeout_per_url_s: float = 180.0) -> dict:
    """Scrape every canary URL with force_refresh in full parallel.

    Per-URL timeout raised 60→180s so Zyte-hitting canaries (G2) complete.
    All 7 fire simultaneously; the browser pool semaphore (5 contexts) is
    the implicit bound. Probe total runtime ~= slowest canary, not sum.
    """
    started = time.monotonic()

    async def _probe_one(url: str) -> dict:
        t0 = time.monotonic()
        try:
            r = await asyncio.wait_for(
                scrape_url(url=url, formats=["markdown"], force_refresh=True),
                timeout=timeout_per_url_s,
            )
            return {
                "url": url,
                "success": r.success,
                "method": r.scrape_method,
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "block_type": r.block_type,
                "methods_tried": r.methods_tried,
            }
        except asyncio.TimeoutError:
            return {
                "url": url, "success": False, "method": "",
                "duration_ms": int(timeout_per_url_s * 1000), "error": "timeout",
            }
        except Exception as exc:
            return {
                "url": url, "success": False, "method": "",
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "error": type(exc).__name__,
            }

    results = await asyncio.gather(*[_probe_one(u) for u in CANARY_URLS])

    successes = sum(1 for r in results if r["success"])
    methods_seen = {r["method"] for r in results if r["method"]}

    return {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_duration_ms": int((time.monotonic() - started) * 1000),
        "canary_count": len(CANARY_URLS),
        "successes": successes,
        "failures": len(CANARY_URLS) - successes,
        "methods_seen": sorted(methods_seen),
        "results": results,
    }
