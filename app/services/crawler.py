from __future__ import annotations

import asyncio
import json
import urllib.robotparser
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse

import httpx
import structlog

from app.db import update_job
from app.redis_client import get_redis
from app.services.content import extract_links
from app.services.scraper import scrape_url

logger = structlog.get_logger()

_EXPIRE_SECONDS = 86400  # 24 hours


def _matches_patterns(path: str, patterns: List[str]) -> bool:
    """Check if a URL path matches any of the regex patterns."""
    import re
    for pattern in patterns:
        if re.search(pattern, path):
            return True
    return False


async def run_crawl(job_id: str, config: dict) -> None:
    """Execute a crawl job. Called by arq worker."""
    url = config["url"]
    max_pages = min(config.get("max_pages", 100), 500)
    max_depth = min(config.get("max_depth", 3), 10)
    include_paths: List[str] = config.get("include_paths", [])
    exclude_paths: List[str] = config.get("exclude_paths", [])
    formats: List[str] = config.get("formats", ["markdown"])
    only_main_content: bool = config.get("only_main_content", True)
    proxy: str = config.get("proxy", "")
    max_concurrency: int = min(config.get("max_concurrency", 3), 5)
    force_refresh: bool = config.get("force_refresh", False)

    parsed = urlparse(url)
    base_domain = parsed.netloc

    redis = await get_redis()

    # Key prefixes
    status_key = f"crawl:{job_id}:status"
    seen_key = f"crawl:{job_id}:seen"
    queue_key = f"crawl:{job_id}:queue"
    results_key = f"crawl:{job_id}:results"

    try:
        # Fetch and parse robots.txt
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        crawl_delay: float = 0
        try:
            async with httpx.AsyncClient(timeout=10) as http_client:
                robots_resp = await http_client.get(robots_url)
                if robots_resp.status_code == 200:
                    rp.parse(robots_resp.text.splitlines())
                    delay = rp.crawl_delay("*")
                    if delay is not None:
                        crawl_delay = float(delay)
                else:
                    # No robots.txt or error — allow all
                    rp.parse([])
        except Exception:
            # Cannot fetch robots.txt — allow all
            rp.parse([])

        # Update job status
        await update_job(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())
        await redis.hset(status_key, mapping={
            "status": "running",
            "pages_scraped": "0",
            "max_pages": str(max_pages),
        })

        # Seed the queue
        await redis.sadd(seen_key, url)
        await redis.rpush(queue_key, json.dumps({"url": url, "depth": 0}))

        # Set TTL on all keys
        for key in (status_key, seen_key, queue_key, results_key):
            await redis.expire(key, _EXPIRE_SECONDS)

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _scrape_one(target_url: str, depth: int) -> None:
            async with semaphore:
                # Check cancellation
                current_status = await redis.hget(status_key, "status")
                if current_status == "cancelled":
                    return

                # Check pages_scraped atomically from Redis
                current_count = int(await redis.hget(status_key, "pages_scraped") or 0)
                if current_count >= max_pages:
                    return

                # Check robots.txt
                if not rp.can_fetch("*", target_url):
                    logger.info("robots.txt disallowed", url=target_url, job_id=job_id)
                    return

                # Respect crawl delay
                if crawl_delay > 0:
                    await asyncio.sleep(crawl_delay)

                result = await scrape_url(
                    url=target_url,
                    formats=formats,
                    proxy=proxy,
                    only_main_content=only_main_content,
                    force_refresh=force_refresh,
                )

                # Atomic increment via Redis HINCRBY
                new_count = await redis.hincrby(status_key, "pages_scraped", 1)

                # Store result
                result_data = {
                    "url": target_url,
                    "success": result.success,
                    "markdown": result.markdown,
                    "html": result.html,
                    "metadata": result.metadata,
                    "scrape_method": result.scrape_method,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }
                await redis.rpush(results_key, json.dumps(result_data))

                # Extract and enqueue new links
                if result.success and depth < max_depth:
                    links = extract_links(result.raw_html, target_url, same_domain_only=True)
                    for link in links:
                        link_parsed = urlparse(link)
                        if link_parsed.netloc != base_domain:
                            continue

                        path = link_parsed.path
                        if include_paths and not _matches_patterns(path, include_paths):
                            continue
                        if exclude_paths and _matches_patterns(path, exclude_paths):
                            continue

                        added = await redis.sadd(seen_key, link)
                        if added:
                            await redis.rpush(queue_key, json.dumps({"url": link, "depth": depth + 1}))

        # Main crawl loop
        while True:
            pages_scraped = int(await redis.hget(status_key, "pages_scraped") or 0)
            if pages_scraped >= max_pages:
                break
            # Check cancellation
            current_status = await redis.hget(status_key, "status")
            if current_status == "cancelled":
                break

            # Dequeue a batch
            batch: list = []
            for _ in range(max_concurrency):
                item = await redis.lpop(queue_key)
                if item is None:
                    break
                batch.append(json.loads(item))

            if not batch:
                break

            # Scrape concurrently
            tasks = [_scrape_one(item["url"], item["depth"]) for item in batch]
            await asyncio.gather(*tasks)

        # Final status
        final_status = await redis.hget(status_key, "status")
        final_count = int(await redis.hget(status_key, "pages_scraped") or 0)
        if final_status == "cancelled":
            completion_status = "cancelled"
        else:
            completion_status = "completed"

        await redis.hset(status_key, mapping={
            "status": completion_status,
            "pages_scraped": str(final_count),
        })
        await update_job(
            job_id,
            status=completion_status,
            pages_scraped=final_count,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:
        logger.error("crawl failed", job_id=job_id, error=str(exc))
        try:
            await redis.hset(status_key, "status", "failed")
        except Exception:
            pass
        await update_job(
            job_id,
            status="failed",
            error=str(exc),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
