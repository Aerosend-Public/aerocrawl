from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.routes import dispatch as route_dispatch
from app.services import cache, strategy
from app.services.actions import execute_actions
from app.services.browser_pool import BrowserPool
from app.services.cf_proxy import scrape_via_cf_proxy
from app.services.content import (
    BlockResult,
    detect_block,
    extract_links,
    extract_main_content,
    extract_metadata,
    html_to_markdown,
)
from app.services.pdf_fetcher import (
    extract_pdf,
    fetch_pdf_bytes,
    looks_like_pdf_url,
)
from app.services.reddit_worker import is_reddit_url, scrape_via_reddit_worker
from app.services.static_fetcher import static_fetch
from app.services.tavily_client import get_tavily_client
from app.services.zyte_client import scrape_via_zyte

logger = structlog.get_logger()

# Module-level singleton
browser_pool: Optional[BrowserPool] = None


def init_pool(max_contexts: int = 5) -> BrowserPool:
    """Initialize the module-level browser pool singleton."""
    global browser_pool
    browser_pool = BrowserPool(max_contexts=max_contexts)
    return browser_pool


@dataclass
class ScrapeResult:
    success: bool = False
    markdown: str = ""
    html: str = ""
    raw_html: str = ""
    screenshot: str = ""  # base64
    links: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    scrape_method: str = ""
    duration_ms: int = 0
    error: str = ""
    action_results: list = field(default_factory=list)
    # V2 fields (only present on failure)
    block_type: str = ""
    block_detail: str = ""
    methods_tried: list = field(default_factory=list)
    # V3 cache fields
    cached: bool = False
    cache_age_seconds: int = 0
    # V3 PDF/image fields
    content_type: str = ""
    pages: int = 0
    images: list = field(default_factory=list)


def _result_from_cache_dict(d: dict) -> ScrapeResult:
    """Rebuild a ScrapeResult from a cache payload dict."""
    known_fields = {
        f for f in (
            "success", "markdown", "html", "raw_html", "screenshot",
            "links", "metadata", "scrape_method", "duration_ms", "error",
            "action_results", "block_type", "block_detail", "methods_tried",
            "cached", "cache_age_seconds",
            "content_type", "pages", "images",
        )
    }
    filtered = {k: v for k, v in d.items() if k in known_fields}
    return ScrapeResult(**filtered)


def _resolve_proxy(proxy: str) -> str:
    """Map shortcut names to actual proxy URLs."""
    if proxy == "proxybase":
        return settings.PROXY_URL
    return proxy


def _needs_browser(
    url: str,
    formats: List[str],
    actions: Optional[List[dict]],
) -> bool:
    """Determine if browser rendering is needed."""
    if actions:
        return True
    if "screenshot" in formats:
        return True
    domain = urlparse(url).netloc.lower()
    for js_domain in settings.js_heavy_list:
        if js_domain in domain:
            return True
    return False


async def _scrape_with_playwright(
    url: str,
    proxy_url: str = "",
    wait_for: str = "networkidle",
    timeout_ms: int = 30000,
    actions: Optional[List[dict]] = None,
    take_screenshot: bool = False,
) -> Tuple[str, str, int, str, list]:
    """Scrape using Playwright with stealth patches.

    Returns (html, final_url, status_code, screenshot_b64, action_results).
    """
    if browser_pool is None:
        raise RuntimeError("Browser pool not initialized. Call init_pool() first.")

    context = await browser_pool.acquire(proxy_url=proxy_url)
    try:
        page = await browser_pool.new_stealth_page(context)
        try:
            response = await page.goto(url, wait_until=wait_for, timeout=timeout_ms)
            status_code = response.status if response else 0
            final_url = page.url

            action_results: list = []
            if actions:
                action_results = await execute_actions(page, actions)

            screenshot_b64 = ""
            if take_screenshot:
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

            html = await page.content()
            return html, final_url, status_code, screenshot_b64, action_results
        finally:
            await page.close()
    finally:
        await browser_pool.release(context)


def _check_block(
    html: str,
    markdown: str,
    status_code: int,
    final_url: str,
) -> Optional[BlockResult]:
    """Run block detection on scraped content."""
    title = ""
    try:
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
    except Exception:
        pass

    return detect_block(
        html=html,
        markdown=markdown,
        status_code=status_code,
        final_url=final_url,
        title=title,
    )


async def _try_playwright(
    url: str,
    proxy_url: str,
    method_name: str,
    wait_for: str,
    timeout_ms: int,
    actions: Optional[List[dict]],
    take_screenshot: bool,
    only_main_content: bool,
) -> Tuple[Optional[dict], Optional[BlockResult]]:
    """Attempt a Playwright scrape. Returns (result_dict, block_result).

    result_dict has keys: html, markdown, final_url, status_code, screenshot, action_results
    If blocked, returns (None, BlockResult).
    If error, returns (None, None).
    """
    try:
        html, final_url, status_code, screenshot, action_results = (
            await _scrape_with_playwright(
                url,
                proxy_url=proxy_url,
                wait_for=wait_for,
                timeout_ms=timeout_ms,
                actions=actions,
                take_screenshot=take_screenshot,
            )
        )

        markdown = html_to_markdown(html, only_main_content=only_main_content)
        block = _check_block(html, markdown, status_code, final_url)

        if block:
            logger.debug("playwright blocked", method=method_name, block_type=block.block_type, url=url)
            return None, block

        return {
            "html": html,
            "markdown": markdown,
            "final_url": final_url,
            "status_code": status_code,
            "screenshot": screenshot,
            "action_results": action_results,
        }, None

    except Exception as exc:
        logger.debug("playwright error", method=method_name, url=url, error=str(exc))
        return None, None


async def scrape_url(
    url: str,
    formats: Optional[List[str]] = None,
    proxy: str = "",
    wait_for: str = "networkidle",
    timeout_ms: int = 30000,
    selector: str = "",
    only_main_content: bool = True,
    actions: Optional[List[dict]] = None,
    force_refresh: bool = False,
    vision: Optional[dict] = None,
) -> ScrapeResult:
    """V3 scraping orchestrator with Redis cache wrapper.

    Cache hits short-circuit the whole chain; misses run the fallback chain
    and then populate the cache on success OR stable failure.

    `vision` accepts:
      - describe_images: bool — auto-describe chart/pricing images with Gemini
      - mode: "visual" — take a screenshot and extract via Gemini instead of HTML
    """
    formats = formats or ["markdown"]
    vision = vision or {}
    want_images = "images" in formats
    visual_mode = vision.get("mode") == "visual"
    describe_images = bool(vision.get("describe_images"))

    # Visual mode needs a screenshot even if caller didn't ask for one
    if visual_mode and "screenshot" not in formats:
        formats = list(formats) + ["screenshot"]

    cache_opts = {
        "formats": formats,
        "selector": selector,
        "only_main_content": only_main_content,
        "actions": actions,
        "wait_for": wait_for,
        "vision": vision,
    }

    if not force_refresh:
        hit = await cache.get(url, cache_opts)
        if hit is not None:
            logger.debug("cache hit", url=url, age=hit.get("cache_age_seconds"))
            return _result_from_cache_dict(hit)

    # ── Step 0: smart domain routing (API > HTML scraping) ──
    # Skip routes when the caller needs a screenshot or browser actions —
    # routes return API data, not rendered pages.
    if not actions and "screenshot" not in formats:
        start_route = time.monotonic()
        try:
            route_result = await route_dispatch(url, only_main_content=only_main_content)
        except Exception as exc:
            logger.debug("route dispatch error", url=url, error=str(exc))
            route_result = None
        if route_result is not None:
            result = ScrapeResult(
                success=True,
                markdown=route_result.markdown,
                html=route_result.html,
                metadata=route_result.metadata,
                scrape_method=f"route:{route_result.route_name}",
                duration_ms=int((time.monotonic() - start_route) * 1000),
            )
            try:
                await cache.set(url, cache_opts, result)
            except Exception:
                pass
            return result

    result = await _scrape_url_impl(
        url=url,
        formats=formats,
        proxy=proxy,
        wait_for=wait_for,
        timeout_ms=timeout_ms,
        selector=selector,
        only_main_content=only_main_content,
        actions=actions,
    )

    # Record strategy outcomes for all chain methods that were attempted
    try:
        domain = strategy.domain_key(url)
        winning_method = result.scrape_method
        for m in (result.methods_tried or []):
            # m won if it matches scrape_method AND result succeeded
            won = result.success and m == winning_method
            await strategy.record(domain, m, won)
    except Exception as exc:
        logger.debug("strategy record failed", error=str(exc))

    # Post-scrape vision + image extraction (only on successful HTML scrapes)
    if result.success and result.raw_html:
        if want_images or describe_images:
            try:
                from app.services.image_handler import extract_image_triples, describe_candidates
                triples = extract_image_triples(result.raw_html, url)
                if describe_images:
                    triples = await describe_candidates(triples)
                result.images = triples
            except Exception as exc:
                logger.debug("image extraction failed", error=str(exc))

    if result.success and visual_mode and result.screenshot:
        try:
            from app.services.image_handler import extract_page_via_screenshot
            vis = await extract_page_via_screenshot(result.screenshot, url)
            if vis and vis.get("markdown"):
                result.markdown = vis["markdown"]
                result.scrape_method = result.scrape_method + "+vision"
        except Exception as exc:
            logger.debug("visual mode failed", error=str(exc))

    # cache.set decides internally whether to store based on success/block_type
    try:
        await cache.set(url, cache_opts, result)
    except Exception as exc:
        logger.debug("cache set failed", url=url, error=str(exc))

    return result


async def _scrape_url_impl(
    url: str,
    formats: List[str],
    proxy: str,
    wait_for: str,
    timeout_ms: int,
    selector: str,
    only_main_content: bool,
    actions: Optional[List[dict]],
) -> ScrapeResult:
    """Core scraping orchestrator (fallback chain, no cache).

    Chain:
      0. Reddit CF Worker (if reddit.com)
      1. Static fetch (httpx, no browser)
      2. Playwright+stealth (no proxy)
      3-4. Playwright+stealth + ProxyBase (×2)
      5. CF General Proxy
      6. Tavily Extract (paid fallback) — replaced by Zyte in Phase 2
    """
    start = time.monotonic()
    result = ScrapeResult()
    take_screenshot = "screenshot" in formats
    has_actions = bool(actions)
    methods_tried: list[str] = []
    last_block: Optional[BlockResult] = None

    # ── Strategy memoization: check preferred method for this domain ──
    try:
        preferred = await strategy.get_preferred(strategy.domain_key(url))
    except Exception:
        preferred = None
    preferred_method = (preferred or {}).get("method", "")

    # ── Step 0.5: PDF detection and extraction ──
    # Gated on URL extension — avoids a speculative HTTP round-trip for every
    # non-PDF request. Redirects-to-PDF from HTML-looking URLs won't be caught
    # here but will be detected later in static_fetcher's content-type check.
    if not has_actions and not take_screenshot and looks_like_pdf_url(url):
        try:
            pdf_fetched = await fetch_pdf_bytes(url, timeout_s=min(timeout_ms / 1000, 30))
        except Exception:
            pdf_fetched = None
        if pdf_fetched:
            pdf_bytes, final_url = pdf_fetched
            pdf_result = await extract_pdf(url, pdf_bytes=pdf_bytes)
            if pdf_result:
                methods_tried.append("pdf")
                result.success = True
                result.markdown = pdf_result["markdown"]
                result.scrape_method = f"pdf:{pdf_result.get('extractor', 'pymupdf')}"
                result.content_type = "application/pdf"
                result.pages = pdf_result.get("pages", 0)
                result.metadata = {
                    "title": "",
                    "description": "",
                    "language": "",
                    "og_title": "",
                    "og_description": "",
                    "og_image": "",
                    "robots": "",
                    "status_code": 200,
                    "source_url": final_url,
                    "pages": pdf_result.get("pages", 0),
                    "tables": pdf_result.get("table_count", 0),
                }
                result.duration_ms = int((time.monotonic() - start) * 1000)
                result.methods_tried = methods_tried
                return result

    # Helper to finalize a successful result
    def _finalize(
        html: str,
        markdown: str,
        final_url: str,
        status_code: int,
        method: str,
        screenshot: str = "",
        action_results: list | None = None,
    ) -> ScrapeResult:
        # Apply selector if provided
        if selector and html:
            soup = BeautifulSoup(html, "lxml")
            selected = soup.select_one(selector)
            if selected:
                html = str(selected)
                markdown = html_to_markdown(html, only_main_content=only_main_content)

        result.raw_html = html
        result.scrape_method = method
        result.action_results = action_results or []
        result.screenshot = screenshot

        if "markdown" in formats:
            result.markdown = markdown
        if "html" in formats:
            result.html = extract_main_content(html) if only_main_content else html
        if "links" in formats and html:
            result.links = extract_links(html, final_url)
        if html:
            result.metadata = extract_metadata(html, final_url, status_code)
        else:
            # Tavily doesn't return HTML, build minimal metadata
            result.metadata = {
                "title": "", "description": "", "language": "",
                "og_title": "", "og_description": "", "og_image": "",
                "robots": "", "status_code": status_code, "source_url": final_url,
            }
        result.success = True
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    try:
        # ── Step 0: Reddit CF Worker ──
        if is_reddit_url(url):
            worker_result = await scrape_via_reddit_worker(url)
            if worker_result:
                methods_tried.append("cf_worker_reddit")
                block = _check_block(
                    worker_result["html"],
                    worker_result["markdown"],
                    worker_result["status_code"],
                    worker_result["final_url"],
                )
                if not block:
                    return _finalize(
                        html=worker_result["html"],
                        markdown=worker_result["markdown"],
                        final_url=worker_result["final_url"],
                        status_code=worker_result["status_code"],
                        method="cf_worker_reddit",
                    )
                last_block = block
            # Don't fall through to normal Playwright for Reddit — it always fails.
            # Skip directly to CF proxy and Tavily.
            methods_tried.append("cf_worker_reddit")

            # Try CF proxy for Reddit
            cf_result = await scrape_via_cf_proxy(url, only_main_content=only_main_content)
            if cf_result:
                methods_tried.append("cf_proxy")
                block = _check_block(cf_result["html"], cf_result["markdown"], cf_result["status_code"], cf_result["final_url"])
                if not block:
                    return _finalize(
                        html=cf_result["html"], markdown=cf_result["markdown"],
                        final_url=cf_result["final_url"], status_code=cf_result["status_code"],
                        method="cf_proxy",
                    )
                last_block = block

            # Reddit falls back to Zyte if CF worker + CF proxy failed (rare).
            # Allowlist blocks this unless reddit.com is added; Tavily kept as
            # a last-resort when Zyte is disabled.
            zyte_result = await scrape_via_zyte(url)
            if zyte_result:
                methods_tried.append("zyte")
                html = zyte_result["html"]
                markdown = html_to_markdown(html, only_main_content=only_main_content)
                return _finalize(
                    html=html,
                    markdown=markdown,
                    final_url=zyte_result["final_url"],
                    status_code=zyte_result["status_code"],
                    method="zyte",
                )
            methods_tried.append("zyte")

            if settings.TAVILY_API_KEYS and not settings.ZYTE_ENABLED:
                tavily = get_tavily_client()
                tavily_result = await tavily.extract(url)
                if tavily_result:
                    methods_tried.append("tavily")
                    return _finalize(
                        html=tavily_result.get("html", ""),
                        markdown=tavily_result["markdown"],
                        final_url=tavily_result["final_url"],
                        status_code=tavily_result["status_code"],
                        method="tavily",
                    )
                methods_tried.append("tavily")

            # All Reddit methods exhausted
            result.methods_tried = methods_tried
            if last_block:
                result.block_type = last_block.block_type
                result.block_detail = last_block.detail
                result.error = f"All scraping methods failed for Reddit URL"
            else:
                result.error = "All scraping methods failed for Reddit URL"
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result

        # ── Step 1: Static fetch (non-JS-heavy only) ──
        if not _needs_browser(url, formats, actions):
            static_result = await static_fetch(url, timeout_ms=min(timeout_ms, 15000))
            if static_result:
                html = static_result["html"]
                markdown = html_to_markdown(html, only_main_content=only_main_content)
                block = _check_block(html, markdown, static_result["status_code"], static_result["final_url"])
                if not block:
                    methods_tried.append("static")
                    return _finalize(
                        html=html, markdown=markdown,
                        final_url=static_result["final_url"],
                        status_code=static_result["status_code"],
                        method="static",
                    )
                last_block = block
                methods_tried.append("static")

        # ── Step 2: Playwright+stealth (no proxy) ──
        pw_result, block = await _try_playwright(
            url, proxy_url="", method_name="playwright+stealth",
            wait_for=wait_for, timeout_ms=timeout_ms, actions=actions,
            take_screenshot=take_screenshot, only_main_content=only_main_content,
        )
        methods_tried.append("playwright+stealth")
        if pw_result:
            return _finalize(
                html=pw_result["html"], markdown=pw_result["markdown"],
                final_url=pw_result["final_url"], status_code=pw_result["status_code"],
                method="playwright+stealth", screenshot=pw_result["screenshot"],
                action_results=pw_result["action_results"],
            )
        if block:
            last_block = block

        # ── Steps 3-6: Playwright+stealth with proxies (only if no actions) ──
        # Actions require consistent browser session, proxy retries won't help
        if not has_actions:
            proxy_attempts = []

            # Resolve caller-provided proxy
            caller_proxy = _resolve_proxy(proxy) if proxy else ""

            # Build proxy attempt list
            if caller_proxy:
                # Caller specified a proxy — use it for 2 attempts
                proxy_attempts = [
                    (caller_proxy, "playwright+stealth+proxy"),
                    (caller_proxy, "playwright+stealth+proxy"),
                ]
            else:
                # Default: ProxyBase x2.
                proxy_attempts = [
                    (settings.PROXY_URL, "playwright+stealth+proxybase"),
                    (settings.PROXY_URL, "playwright+stealth+proxybase"),
                ] if settings.PROXY_URL else []

            for proxy_url, method_name in proxy_attempts:
                pw_result, block = await _try_playwright(
                    url, proxy_url=proxy_url, method_name=method_name,
                    wait_for=wait_for, timeout_ms=timeout_ms, actions=None,
                    take_screenshot=take_screenshot, only_main_content=only_main_content,
                )
                methods_tried.append(method_name)
                if pw_result:
                    return _finalize(
                        html=pw_result["html"], markdown=pw_result["markdown"],
                        final_url=pw_result["final_url"], status_code=pw_result["status_code"],
                        method=method_name, screenshot=pw_result["screenshot"],
                        action_results=pw_result["action_results"],
                    )
                if block:
                    last_block = block

            # ── Step 7: CF General Proxy ──
            cf_result = await scrape_via_cf_proxy(url, only_main_content=only_main_content)
            if cf_result:
                methods_tried.append("cf_proxy")
                block = _check_block(
                    cf_result["html"], cf_result["markdown"],
                    cf_result["status_code"], cf_result["final_url"],
                )
                if not block:
                    return _finalize(
                        html=cf_result["html"], markdown=cf_result["markdown"],
                        final_url=cf_result["final_url"], status_code=cf_result["status_code"],
                        method="cf_proxy",
                    )
                last_block = block
            else:
                methods_tried.append("cf_proxy")

            # ── Step 8: Zyte web unlocker (allowlist + budget gated) ──
            zyte_result = await scrape_via_zyte(url)
            if zyte_result:
                methods_tried.append("zyte")
                html = zyte_result["html"]
                markdown = html_to_markdown(html, only_main_content=only_main_content)
                block = _check_block(html, markdown, zyte_result["status_code"], zyte_result["final_url"])
                if not block:
                    return _finalize(
                        html=html,
                        markdown=markdown,
                        final_url=zyte_result["final_url"],
                        status_code=zyte_result["status_code"],
                        method="zyte",
                    )
                last_block = block
            else:
                methods_tried.append("zyte")

            # ── Step 9: Tavily Extract (emergency fallback, disabled by default) ──
            if settings.TAVILY_API_KEYS and not settings.ZYTE_ENABLED:
                tavily = get_tavily_client()
                tavily_result = await tavily.extract(url)
                if tavily_result:
                    methods_tried.append("tavily")
                    return _finalize(
                        html=tavily_result.get("html", ""),
                        markdown=tavily_result["markdown"],
                        final_url=tavily_result["final_url"],
                        status_code=tavily_result["status_code"],
                        method="tavily",
                    )
                methods_tried.append("tavily")

        # ── All methods exhausted ──
        result.methods_tried = methods_tried
        if last_block:
            result.block_type = last_block.block_type
            result.block_detail = last_block.detail
            result.error = f"All scraping methods failed. {last_block.detail}"
        else:
            result.error = "All scraping methods failed"

    except Exception as exc:
        result.error = str(exc)
        result.methods_tried = methods_tried
        logger.error("scrape_url failed", url=url, error=str(exc))

    result.duration_ms = int((time.monotonic() - start) * 1000)
    return result
