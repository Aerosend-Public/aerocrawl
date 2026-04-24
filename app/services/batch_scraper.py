from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import List

import structlog

from app.db import update_job
from app.redis_client import get_redis
from app.services.scraper import scrape_url

logger = structlog.get_logger()

_EXPIRE_SECONDS = 86400  # 24 hours


async def run_batch(job_id: str, config: dict) -> None:
    """Execute a batch scrape job. Called by arq worker."""
    urls: List[str] = config["urls"]
    formats: List[str] = config.get("formats", ["markdown"])
    proxy: str = config.get("proxy", "")
    only_main_content: bool = config.get("only_main_content", True)
    force_refresh: bool = config.get("force_refresh", False)

    redis = await get_redis()

    status_key = f"batch:{job_id}:status"
    results_key = f"batch:{job_id}:results"

    try:
        await update_job(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())
        await redis.hset(status_key, mapping={
            "status": "running",
            "completed": "0",
            "total": str(len(urls)),
        })

        for key in (status_key, results_key):
            await redis.expire(key, _EXPIRE_SECONDS)

        semaphore = asyncio.Semaphore(5)
        completed = 0

        async def _scrape_one(url: str) -> None:
            nonlocal completed

            async with semaphore:
                result = await scrape_url(
                    url=url,
                    formats=formats,
                    proxy=proxy,
                    only_main_content=only_main_content,
                    force_refresh=force_refresh,
                )

                result_data = {
                    "url": url,
                    "success": result.success,
                    "markdown": result.markdown,
                    "html": result.html,
                    "metadata": result.metadata,
                    "scrape_method": result.scrape_method,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }
                await redis.rpush(results_key, json.dumps(result_data))
                completed += 1
                await redis.hset(status_key, "completed", str(completed))

        tasks = [_scrape_one(url) for url in urls]
        await asyncio.gather(*tasks)

        await redis.hset(status_key, "status", "completed")
        await update_job(
            job_id,
            status="completed",
            pages_scraped=completed,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:
        logger.error("batch scrape failed", job_id=job_id, error=str(exc))
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
