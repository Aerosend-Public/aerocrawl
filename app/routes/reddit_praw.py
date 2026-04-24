"""Reddit PRAW route — used only when OAuth creds are configured.

By default the existing CF-Worker + Reddit `.json` path in the main chain
handles Reddit. This route is an upgrade layer: if REDDIT_CLIENT_ID is set,
we use asyncpraw which gives us search, modlog, private subreddits, and
cleaner comment trees.

Matches reddit.com URLs that the CF worker can't handle: search, user pages,
private subs. Falls through to the main chain for everything else.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import structlog

from app.config import settings
from app.routes.base import RouteResult

logger = structlog.get_logger()


class RedditPrawRoute:
    name = "reddit_praw"
    description = "Reddit via asyncpraw (only when OAuth creds configured)"

    def matches(self, url: str) -> bool:
        # Only claim if creds are set AND URL is a reddit search URL (which
        # the .json worker handles poorly). Everything else stays on the main
        # reddit_worker path so we don't regress.
        if not settings.REDDIT_CLIENT_ID or not settings.REDDIT_CLIENT_SECRET:
            return False
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        if not any(d in host for d in ("reddit.com", "redd.it")):
            return False
        path = urlparse(url).path
        return "/search" in path or path.startswith("/user/")

    async def fetch(self, url: str, only_main_content: bool = True) -> Optional[RouteResult]:
        try:
            import asyncpraw
        except ImportError:
            logger.warning("reddit_praw: asyncpraw not installed")
            return None

        try:
            reddit = asyncpraw.Reddit(
                client_id=settings.REDDIT_CLIENT_ID,
                client_secret=settings.REDDIT_CLIENT_SECRET,
                user_agent=settings.REDDIT_USER_AGENT,
            )
        except Exception as exc:
            logger.debug("reddit_praw: client init failed", error=str(exc))
            return None

        try:
            parsed = urlparse(url)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if "/search" in path:
                # /r/<sub>/search or /search
                m = re.match(r"^/r/([^/]+)/search", path)
                subreddit_name = m.group(1) if m else "all"
                query = (qs.get("q") or [""])[0]
                sort = (qs.get("sort") or ["relevance"])[0]
                if not query:
                    return None
                sub = await reddit.subreddit(subreddit_name)
                hits = []
                async for post in sub.search(query, sort=sort, limit=25):
                    hits.append({
                        "title": post.title,
                        "author": str(post.author) if post.author else "[deleted]",
                        "score": post.score,
                        "num_comments": post.num_comments,
                        "url": f"https://reddit.com{post.permalink}",
                        "selftext": (post.selftext or "")[:500],
                    })
                md = f"# Reddit search in r/{subreddit_name}: \"{query}\"\n\n"
                md += f"**{len(hits)} results** · sort: {sort}\n\n---\n\n"
                for h in hits:
                    md += f"## {h['title']}\n"
                    md += f"u/{h['author']} · {h['score']} pts · {h['num_comments']} comments · [link]({h['url']})\n\n"
                    if h["selftext"]:
                        md += h["selftext"] + "\n\n"
                    md += "---\n\n"
                return RouteResult(
                    markdown=md, html="", final_url=url, status_code=200,
                    metadata={"title": f"Reddit search: {query}", "source_url": url, "status_code": 200},
                    route_name="reddit_praw:search",
                    raw_data={"hit_count": len(hits)},
                )

            if path.startswith("/user/"):
                m = re.match(r"^/user/([^/]+)", path)
                if not m:
                    return None
                username = m.group(1)
                redditor = await reddit.redditor(username)
                await redditor.load()
                md = f"# u/{username}\n\n"
                md += f"- **Karma:** {redditor.link_karma} link · {redditor.comment_karma} comment\n"
                md += f"- **Created:** {redditor.created_utc}\n\n"
                md += "## Recent submissions\n\n"
                async for post in redditor.submissions.new(limit=20):
                    md += f"- [{post.title}](https://reddit.com{post.permalink}) · {post.score} pts · r/{post.subreddit.display_name}\n"
                return RouteResult(
                    markdown=md, html="", final_url=url, status_code=200,
                    metadata={"title": f"u/{username}", "source_url": url, "status_code": 200},
                    route_name="reddit_praw:user",
                    raw_data={"username": username},
                )

            return None
        except Exception as exc:
            logger.debug("reddit_praw: fetch failed", url=url, error=str(exc))
            return None
        finally:
            try:
                await reddit.close()
            except Exception:
                pass
