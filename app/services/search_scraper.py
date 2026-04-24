from __future__ import annotations

from urllib.parse import quote_plus, unquote, parse_qs, urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger()


def _parse_brave_results(html: str, count: int) -> list[dict]:
    """Parse Brave Search HTML into structured results."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []

    for snippet in soup.select('[data-type="web"]'):
        if len(results) >= count:
            break

        link = snippet.select_one("a.l1")
        if not link or not link.get("href"):
            continue

        url = link["href"]
        if not url.startswith("http"):
            continue

        title_el = link.select_one(".title")
        title = title_el.get_text(strip=True) if title_el else ""

        desc_el = snippet.select_one(".generic-snippet .content")
        description = desc_el.get_text(strip=True) if desc_el else ""

        if title:
            results.append({
                "title": title,
                "url": url,
                "description": description,
            })

    return results


def _parse_ddg_results(html: str, count: int) -> list[dict]:
    """Parse DuckDuckGo HTML search results into structured results."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []

    for result_div in soup.select(".result"):
        if len(results) >= count:
            break

        link = result_div.select_one(".result__a")
        if not link or not link.get("href"):
            continue

        # DDG wraps URLs in redirect: //duckduckgo.com/l/?uddg=<encoded_url>
        raw_href = link["href"]
        parsed = urlparse(raw_href)
        qs = parse_qs(parsed.query)
        url = unquote(qs["uddg"][0]) if "uddg" in qs else raw_href
        if not url.startswith("http"):
            continue

        title = link.get_text(strip=True)

        snippet = result_div.select_one(".result__snippet")
        description = snippet.get_text(strip=True) if snippet else ""

        if title:
            results.append({
                "title": title,
                "url": url,
                "description": description,
            })

    return results


_BRAVE_SEARCH_URL = "https://search.brave.com/search"
_DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
_CAPTCHA_INDICATORS = ["captcha", "unusual traffic", "verify you are human", "are you a robot", "i'm not a robot"]
_MIN_RESULTS_THRESHOLD = 3


async def _fetch_via_cf_worker(search_url: str, cf_proxy_url: str) -> str | None:
    """Fetch a search URL via the CF Worker proxy. Returns HTML body or None."""
    import inspect
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(cf_proxy_url, params={"url": search_url})
            if resp.status_code != 200:
                logger.debug("cf_worker non-200", status=resp.status_code, search_url=search_url)
                return None

            json_result = resp.json()
            # Support both sync (real httpx) and async (test mocks) .json()
            if inspect.isawaitable(json_result):
                json_result = await json_result
            data: dict = json_result
            body = data.get("body", "")
            origin_status = data.get("status", 0)

            if origin_status != 200 or not body or len(body.strip()) < 100:
                logger.debug("cf_worker empty/error", origin_status=origin_status, search_url=search_url)
                return None

            return body
    except Exception as exc:
        logger.debug("cf_worker fetch error", search_url=search_url, error=str(exc))
        return None


def _is_captcha(html: str) -> bool:
    """Check if HTML contains CAPTCHA indicators."""
    lower = html.lower()
    return any(indicator in lower for indicator in _CAPTCHA_INDICATORS)


async def search(query: str, count: int, cf_proxy_url: str) -> dict:
    """Search orchestrator: Brave HTML → DuckDuckGo HTML fallback.

    Returns a dict matching the /search endpoint response shape.
    """
    import time
    start = time.monotonic()
    count = min(count, 20)

    # Step 1: Try Brave
    brave_url = f"{_BRAVE_SEARCH_URL}?q={quote_plus(query)}"
    brave_html = await _fetch_via_cf_worker(brave_url, cf_proxy_url)

    if brave_html and not _is_captcha(brave_html):
        results = _parse_brave_results(brave_html, count)
        if len(results) >= _MIN_RESULTS_THRESHOLD:
            duration = int((time.monotonic() - start) * 1000)
            return {
                "success": True,
                "query": query,
                "search_engine": "brave_html",
                "result_count": len(results),
                "duration_ms": duration,
                "results": results,
            }
        logger.debug("brave_html too few results", count=len(results), query=query)

    # Step 2: Fallback to DuckDuckGo
    ddg_url = f"{_DDG_SEARCH_URL}?q={quote_plus(query)}"
    ddg_html = await _fetch_via_cf_worker(ddg_url, cf_proxy_url)

    if ddg_html and not _is_captcha(ddg_html):
        results = _parse_ddg_results(ddg_html, count)
        if results:
            duration = int((time.monotonic() - start) * 1000)
            return {
                "success": True,
                "query": query,
                "search_engine": "duckduckgo_html",
                "result_count": len(results),
                "duration_ms": duration,
                "results": results,
            }

    # Both failed
    duration = int((time.monotonic() - start) * 1000)
    return {
        "success": False,
        "query": query,
        "search_engine": "",
        "result_count": 0,
        "duration_ms": duration,
        "results": [],
        "error": "All search engines failed",
    }
