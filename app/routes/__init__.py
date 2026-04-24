"""Smart domain routing — intercepts URLs whose API is richer than HTML scraping.

Registry is an ordered list of handlers; first match wins. If no handler claims
the URL, the caller falls back to the 9-step scrape chain.
"""
from __future__ import annotations

from typing import Optional

import structlog

from app.routes.academic import AcademicRoute
from app.routes.base import RouteHandler, RouteResult
from app.routes.github import GitHubRoute
from app.routes.hackernews import HackerNewsRoute
from app.routes.reddit_praw import RedditPrawRoute
from app.routes.rss import RSSRoute

logger = structlog.get_logger()

# Ordered list — most specific/highest-value first.
_HANDLERS: list[RouteHandler] = [
    GitHubRoute(),
    HackerNewsRoute(),
    AcademicRoute(),
    RedditPrawRoute(),
    RSSRoute(),  # last — generic RSS sniffer is a universal fallback
]


async def dispatch(url: str, only_main_content: bool = True) -> Optional[RouteResult]:
    """Try each handler in order; return first non-None result."""
    for handler in _HANDLERS:
        try:
            if not handler.matches(url):
                continue
            result = await handler.fetch(url, only_main_content=only_main_content)
            if result is not None:
                logger.info("route match", handler=handler.name, url=url)
                return result
        except Exception as exc:
            logger.debug("route error", handler=handler.name, url=url, error=str(exc))
    return None


def list_routes() -> list[dict]:
    """Introspection for /scraper/route-info endpoint."""
    return [{"name": h.name, "description": h.description} for h in _HANDLERS]


async def which_route(url: str) -> Optional[str]:
    """Return the name of the handler that would fire for this URL, without fetching."""
    for handler in _HANDLERS:
        try:
            if handler.matches(url):
                return handler.name
        except Exception:
            continue
    return None
