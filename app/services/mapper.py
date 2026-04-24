from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from app.services.content import extract_links

logger = structlog.get_logger()

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_TIMEOUT = 15.0


async def _fetch_text(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Fetch a URL and return text, or None on failure."""
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        logger.debug("fetch failed", url=url, error=str(exc))
    return None


def _parse_sitemap_urls(xml_text: str) -> tuple:
    """Parse a sitemap XML. Returns (urls: list[str], nested_sitemaps: list[str])."""
    urls: List[str] = []
    nested: List[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return urls, nested

    # Check for sitemap index
    for sitemap_el in root.findall(f"{_SITEMAP_NS}sitemap"):
        loc = sitemap_el.find(f"{_SITEMAP_NS}loc")
        if loc is not None and loc.text:
            nested.append(loc.text.strip())

    # Check for url entries
    for url_el in root.findall(f"{_SITEMAP_NS}url"):
        loc = url_el.find(f"{_SITEMAP_NS}loc")
        if loc is not None and loc.text:
            urls.append(loc.text.strip())

    return urls, nested


def _extract_sitemaps_from_robots(robots_text: str) -> List[str]:
    """Extract Sitemap: directives from robots.txt."""
    sitemaps: List[str] = []
    for line in robots_text.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                sitemaps.append(url)
    return sitemaps


def _matches_patterns(path: str, patterns: List[str]) -> bool:
    """Check if a URL path matches any of the regex patterns."""
    for pattern in patterns:
        if re.search(pattern, path):
            return True
    return False


async def discover_urls(
    url: str,
    max_urls: int = 500,
    include_paths: Optional[List[str]] = None,
    exclude_paths: Optional[List[str]] = None,
    include_subdomains: bool = False,
) -> dict:
    """Discover URLs from a website via robots.txt, sitemaps, and page links."""
    parsed = urlparse(url)
    base_domain = parsed.netloc
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    all_urls: Set[str] = set()
    sources: Dict[str, int] = {"sitemap": 0, "robots_txt": 0, "page_links": 0}

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=_TIMEOUT,
        headers={"User-Agent": "Aerocrawl/1.0"},
    ) as client:
        # Step 1: Fetch robots.txt for Sitemap directives
        robots_url = f"{base_url}/robots.txt"
        robots_text = await _fetch_text(client, robots_url)
        sitemap_urls_from_robots: List[str] = []
        if robots_text:
            sitemap_urls_from_robots = _extract_sitemaps_from_robots(robots_text)

        # Step 2: Fetch sitemaps (from robots.txt + default sitemap.xml)
        sitemap_queue: List[str] = list(sitemap_urls_from_robots)
        default_sitemap = f"{base_url}/sitemap.xml"
        if default_sitemap not in sitemap_queue:
            sitemap_queue.append(default_sitemap)

        visited_sitemaps: Set[str] = set()
        while sitemap_queue and len(all_urls) < max_urls:
            sitemap_url = sitemap_queue.pop(0)
            if sitemap_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sitemap_url)

            xml_text = await _fetch_text(client, sitemap_url)
            if not xml_text:
                continue

            urls, nested = _parse_sitemap_urls(xml_text)
            for u in urls:
                if len(all_urls) >= max_urls:
                    break
                all_urls.add(u)
                sources["sitemap"] += 1

            for nested_url in nested:
                if nested_url not in visited_sitemaps:
                    sitemap_queue.append(nested_url)

        # Step 3: Fetch homepage and extract links
        homepage_html = await _fetch_text(client, url)
        if homepage_html:
            page_links = extract_links(homepage_html, url, same_domain_only=True)
            for link in page_links:
                if len(all_urls) >= max_urls:
                    break
                if link not in all_urls:
                    all_urls.add(link)
                    sources["page_links"] += 1

    # Step 4: Filter by domain
    if not include_subdomains:
        all_urls = {
            u for u in all_urls
            if urlparse(u).netloc == base_domain
        }

    # Step 5: Filter by include_paths / exclude_paths
    if include_paths:
        all_urls = {
            u for u in all_urls
            if _matches_patterns(urlparse(u).path, include_paths)
        }

    if exclude_paths:
        all_urls = {
            u for u in all_urls
            if not _matches_patterns(urlparse(u).path, exclude_paths)
        }

    sorted_urls = sorted(all_urls)[:max_urls]

    return {
        "urls": sorted_urls,
        "total": len(sorted_urls),
        "sources": sources,
    }
