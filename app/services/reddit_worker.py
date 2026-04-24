from __future__ import annotations

import json
from urllib.parse import quote, urlparse

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()


def is_reddit_url(url: str) -> bool:
    """Check if URL is a Reddit domain."""
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in ("reddit.com", "redd.it"))


async def scrape_via_reddit_worker(url: str) -> dict | None:
    """Scrape Reddit URL via the Cloudflare Worker proxy.

    Returns dict with keys: html, markdown, final_url, status_code
    Returns None if worker is not configured or request fails.
    """
    worker_url = settings.REDDIT_PROXY_URL
    if not worker_url:
        logger.debug("reddit_worker: REDDIT_PROXY_URL not configured")
        return None

    try:
        # Extract the Reddit path from the URL
        parsed = urlparse(url)
        reddit_path = parsed.path
        if parsed.query:
            reddit_path += f"?{parsed.query}"

        # Check if this is a JSON endpoint request
        is_json = ".json" in reddit_path

        # If not already a .json URL, try fetching as JSON for richer data
        if not is_json:
            json_path = reddit_path.rstrip("/") + ".json"
            result = await _fetch_via_worker(worker_url, json_path)
            if result and result["status_code"] == 200:
                # Convert Reddit JSON to readable markdown
                markdown = _reddit_json_to_markdown(result["body"])
                if markdown:
                    return {
                        "html": result["body"],
                        "markdown": markdown,
                        "final_url": url,
                        "status_code": 200,
                    }

        # Fallback: fetch the original URL through the worker
        result = await _fetch_via_worker(worker_url, reddit_path)
        if result and result["status_code"] == 200:
            body = result["body"]
            # If it's JSON, convert it
            if is_json:
                markdown = _reddit_json_to_markdown(body)
                return {
                    "html": body,
                    "markdown": markdown or body,
                    "final_url": url,
                    "status_code": 200,
                }
            return {
                "html": body,
                "markdown": body,
                "final_url": url,
                "status_code": result["status_code"],
            }

        logger.warning("reddit_worker: failed to fetch", url=url, status=result.get("status_code") if result else "no response")
        return None

    except Exception as exc:
        logger.error("reddit_worker: error", url=url, error=str(exc))
        return None


async def _fetch_via_worker(worker_url: str, reddit_path: str) -> dict | None:
    """Make request to the CF Worker."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                worker_url,
                params={"path": reddit_path},
                headers={"Accept": "application/json"},
            )
            return {
                "body": resp.text,
                "status_code": resp.status_code,
            }
    except Exception as exc:
        logger.debug("reddit_worker: request failed", error=str(exc))
        return None


def _reddit_json_to_markdown(body: str) -> str:
    """Convert Reddit JSON API response to readable markdown."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""

    lines: list[str] = []

    # Handle Listing response (subreddit pages)
    if isinstance(data, dict) and data.get("kind") == "Listing":
        children = data.get("data", {}).get("children", [])
        for child in children:
            post = child.get("data", {})
            title = post.get("title", "")
            author = post.get("author", "")
            score = post.get("score", 0)
            selftext = post.get("selftext", "")
            url = post.get("url", "")
            num_comments = post.get("num_comments", 0)
            permalink = post.get("permalink", "")

            lines.append(f"## {title}")
            lines.append(f"**u/{author}** | {score} points | {num_comments} comments")
            if permalink:
                lines.append(f"[permalink](https://reddit.com{permalink})")
            if selftext:
                lines.append(f"\n{selftext}")
            if url and url != f"https://www.reddit.com{permalink}":
                lines.append(f"\nLink: {url}")
            lines.append("\n---\n")

    # Handle post detail (array of Listings — [post, comments])
    elif isinstance(data, list) and len(data) >= 1:
        for listing in data:
            if isinstance(listing, dict) and listing.get("kind") == "Listing":
                children = listing.get("data", {}).get("children", [])
                for child in children:
                    item = child.get("data", {})
                    if child.get("kind") == "t3":  # Post
                        lines.append(f"# {item.get('title', '')}")
                        lines.append(f"**u/{item.get('author', '')}** | {item.get('score', 0)} points")
                        if item.get("selftext"):
                            lines.append(f"\n{item['selftext']}")
                    elif child.get("kind") == "t1":  # Comment
                        depth = "  " * item.get("depth", 0)
                        lines.append(f"{depth}**u/{item.get('author', '')}** ({item.get('score', 0)} pts):")
                        lines.append(f"{depth}{item.get('body', '')}")
                        lines.append("")

    return "\n".join(lines) if lines else body
