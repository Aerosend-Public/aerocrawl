"""Universal RSS/Atom feed route.

Sniffs common feed paths (/feed, /rss, /atom.xml, /index.xml) OR accepts
direct feed URLs (ending in .xml/.rss). feedparser handles both formats.

Lowest-priority handler — runs only when more specific routes don't match.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import structlog

from app.routes.base import RouteResult

logger = structlog.get_logger()

_FEED_URL_MARKERS = ("/feed", "/rss", "/atom", "feed.xml", "rss.xml", "atom.xml", "index.xml")
_FEED_CONTENT_TYPES = ("application/rss", "application/atom", "application/xml", "text/xml")


class RSSRoute:
    name = "rss"
    description = "Universal RSS/Atom feed handler — last in the dispatch chain"

    def matches(self, url: str) -> bool:
        lower = url.lower()
        # Only claim URLs that look like feeds. For generic HTML URLs, a
        # discovery probe would be better but adds latency for every scrape —
        # keep this handler strict and let callers explicitly request feed URLs.
        return any(m in lower for m in _FEED_URL_MARKERS) or lower.endswith((".xml", ".rss", ".atom"))

    async def fetch(self, url: str, only_main_content: bool = True) -> Optional[RouteResult]:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Aerocrawl/3.0"})
        except Exception as exc:
            logger.debug("rss: fetch failed", url=url, error=str(exc))
            return None

        if resp.status_code != 200:
            return None

        ct = (resp.headers.get("content-type") or "").lower()
        body = resp.content
        # Only parse if it looks like a feed — avoid accidentally parsing HTML
        if not any(marker in ct for marker in _FEED_CONTENT_TYPES):
            # Fall back to body sniff — feedparser is tolerant but we want to
            # bail fast for HTML so scrape chain takes over
            sniff = body[:500].decode("utf-8", errors="replace").lstrip().lower()
            if not (sniff.startswith("<?xml") or sniff.startswith("<rss") or "<feed" in sniff):
                return None

        # feedparser is sync — offload to thread
        parsed = await asyncio.to_thread(feedparser.parse, body)
        if parsed.bozo and not parsed.entries:
            logger.debug("rss: parse failed", url=url, bozo=str(parsed.bozo_exception))
            return None

        feed = parsed.feed
        entries = parsed.entries

        md = f"# {feed.get('title', 'Feed')}\n\n"
        if feed.get("subtitle"):
            md += f"{feed['subtitle']}\n\n"
        if feed.get("link"):
            md += f"Source: {feed['link']}\n\n"
        md += f"**{len(entries)} entries**\n\n---\n\n"

        for e in entries[:50]:  # cap to avoid giant markdown
            title = e.get("title", "")
            link = e.get("link", "")
            published = e.get("published") or e.get("updated") or ""
            author = e.get("author") or ""
            summary = e.get("summary") or ""
            md += f"## {title}\n\n"
            if published:
                md += f"_{published}_"
                if author:
                    md += f" · {author}"
                md += "\n\n"
            if summary:
                md += summary + "\n\n"
            if link:
                md += f"[link]({link})\n\n"
            md += "---\n\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={
                "title": feed.get("title", ""),
                "description": feed.get("subtitle", ""),
                "source_url": url,
                "status_code": 200,
            },
            route_name="rss",
            raw_data={"entry_count": len(entries)},
        )
