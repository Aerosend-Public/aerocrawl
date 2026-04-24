"""Hacker News route — uses Algolia HN Search API (free, unlimited).

Handles:
  - https://news.ycombinator.com/item?id=N → post + all comments
  - https://news.ycombinator.com/ or /news → front page
  - https://news.ycombinator.com/user?id=X → user submissions
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from app.routes.base import RouteResult

logger = structlog.get_logger()

_ALGOLIA = "https://hn.algolia.com/api/v1"
_FIREBASE = "https://hacker-news.firebaseio.com/v0"


class HackerNewsRoute:
    name = "hackernews"
    description = "Hacker News via Algolia HN Search API (free, unlimited)"

    def matches(self, url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        return host in ("news.ycombinator.com", "www.ycombinator.com", "ycombinator.com")

    async def fetch(self, url: str, only_main_content: bool = True) -> Optional[RouteResult]:
        parsed = urlparse(url)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/item" and "id" in qs:
            return await self._fetch_item(qs["id"][0], url)
        if path == "/user" and "id" in qs:
            return await self._fetch_user(qs["id"][0], url)
        if path in ("", "/", "/news", "/newest"):
            return await self._fetch_frontpage(url, path)

        return None

    async def _algolia_get(self, path: str) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(f"{_ALGOLIA}{path}")
        except Exception as exc:
            logger.debug("hn: request failed", path=path, error=str(exc))
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    async def _fetch_item(self, item_id: str, url: str) -> Optional[RouteResult]:
        data = await self._algolia_get(f"/items/{item_id}")
        if not data:
            return None

        title = data.get("title") or ""
        author = data.get("author") or ""
        points = data.get("points")
        story_url = data.get("url") or ""
        text = data.get("text") or ""

        md = f"# {title}\n\n"
        md += f"**@{author}**"
        if points is not None:
            md += f" · {points} points"
        md += "\n\n"
        if story_url:
            md += f"Link: {story_url}\n\n"
        if text:
            md += text + "\n\n"

        def _walk_comments(children: list, depth: int = 0) -> str:
            out = ""
            for c in children or []:
                indent = "  " * depth
                author = c.get("author") or "[deleted]"
                body = (c.get("text") or "").strip()
                if body:
                    out += f"{indent}**@{author}:** {body}\n\n"
                out += _walk_comments(c.get("children") or [], depth + 1)
            return out

        comments_md = _walk_comments(data.get("children") or [])
        if comments_md:
            md += "## Comments\n\n" + comments_md

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": title, "description": text[:200], "source_url": url, "status_code": 200},
            route_name="hackernews:item",
            raw_data={"item_id": item_id, "comment_count": len(data.get("children") or [])},
        )

    async def _fetch_user(self, user_id: str, url: str) -> Optional[RouteResult]:
        data = await self._algolia_get(f"/users/{user_id}")
        if not data:
            return None
        md = f"# @{user_id}\n\n"
        if data.get("karma") is not None:
            md += f"Karma: {data['karma']}\n\n"
        if data.get("about"):
            md += data["about"] + "\n\n"
        if data.get("created_at"):
            md += f"Joined: {data['created_at']}\n\n"
        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": f"@{user_id}", "source_url": url, "status_code": 200},
            route_name="hackernews:user",
            raw_data=data,
        )

    async def _fetch_frontpage(self, url: str, path: str) -> Optional[RouteResult]:
        endpoint = "/search?tags=front_page" if path != "/newest" else "/search_by_date?tags=story"
        data = await self._algolia_get(endpoint)
        if not data or "hits" not in data:
            return None
        md = "# Hacker News — Front Page\n\n"
        for hit in (data.get("hits") or [])[:30]:
            title = hit.get("title") or ""
            author = hit.get("author") or ""
            points = hit.get("points")
            obj_id = hit.get("objectID")
            story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={obj_id}"
            md += f"- **[{title}]({story_url})** · @{author}"
            if points:
                md += f" · {points} pts"
            md += f" · [discussion](https://news.ycombinator.com/item?id={obj_id})\n"
        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": "Hacker News", "source_url": url, "status_code": 200},
            route_name="hackernews:frontpage",
            raw_data={"count": len(data.get("hits") or [])},
        )
