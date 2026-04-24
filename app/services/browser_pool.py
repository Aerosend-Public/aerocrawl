from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urlparse

import structlog
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright_stealth import stealth_async

logger = structlog.get_logger()

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
DEFAULT_VIEWPORT = {"width": 1280, "height": 720}


def parse_proxy(proxy_url: str) -> Optional[dict]:
    """Parse proxy URL like http://user:pass@host:port into Playwright proxy dict."""
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url)
        result: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        return result
    except Exception:
        logger.warning("Failed to parse proxy URL", proxy_url=proxy_url)
        return None


class BrowserPool:
    def __init__(self, max_contexts: int = 5) -> None:
        self._max_contexts = max_contexts
        self._semaphore = asyncio.Semaphore(max_contexts)
        self._active_contexts: int = 0
        self._queued: int = 0
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        """Lazy-init shared Chromium headless browser."""
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                logger.info("Browser launched")
            return self._browser

    async def _create_context(
        self,
        proxy_url: str = "",
        viewport: Optional[dict] = None,
    ) -> BrowserContext:
        """Create a new browser context with optional proxy and viewport."""
        browser = await self._ensure_browser()
        kwargs: dict = {
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": viewport or DEFAULT_VIEWPORT,
            "ignore_https_errors": True,
        }
        proxy = parse_proxy(proxy_url)
        if proxy:
            kwargs["proxy"] = proxy
        context = await browser.new_context(**kwargs)
        return context

    async def new_stealth_page(self, context: BrowserContext) -> Page:
        """Create a new page with stealth patches applied."""
        page = await context.new_page()
        try:
            await stealth_async(page)
        except Exception:
            logger.warning("Failed to apply stealth patches, continuing without stealth")
        return page

    async def acquire(
        self,
        proxy_url: str = "",
        viewport: Optional[dict] = None,
    ) -> BrowserContext:
        """Acquire a browser context. Blocks if pool is full."""
        self._queued += 1
        await self._semaphore.acquire()
        self._queued -= 1
        self._active_contexts += 1
        try:
            context = await self._create_context(proxy_url=proxy_url, viewport=viewport)
            return context
        except Exception:
            self._active_contexts -= 1
            self._semaphore.release()
            raise

    async def release(self, context: BrowserContext) -> None:
        """Close context and release semaphore slot."""
        try:
            await context.close()
        except Exception:
            pass
        self._active_contexts -= 1
        self._semaphore.release()

    def status(self) -> dict:
        return {
            "max_contexts": self._max_contexts,
            "active_contexts": self._active_contexts,
            "queued_requests": self._queued,
        }

    async def shutdown(self) -> None:
        """Close browser and playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info("Browser pool shut down")
