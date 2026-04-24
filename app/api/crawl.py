from __future__ import annotations

import json
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import verify_api_key
from app.db import create_job, get_job, update_job
from app.validation import validate_url

router = APIRouter(tags=["crawl"])


class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 100
    max_depth: int = 3
    include_paths: Optional[List[str]] = None
    exclude_paths: Optional[List[str]] = None
    formats: Optional[List[str]] = None
    only_main_content: bool = True
    proxy: Optional[str] = None
    max_concurrency: int = 3
    force_refresh_all: bool = False


@router.post("/crawl")
async def start_crawl(
    body: CrawlRequest,
    request: Request,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    validate_url(body.url)

    if body.max_pages > 500:
        raise HTTPException(400, "max_pages cannot exceed 500")

    job_id = f"crawl_{secrets.token_hex(8)}"

    config = {
        "url": body.url,
        "max_pages": min(body.max_pages, 500),
        "max_depth": body.max_depth,
        "include_paths": body.include_paths or [],
        "exclude_paths": body.exclude_paths or [],
        "formats": body.formats or ["markdown"],
        "only_main_content": body.only_main_content,
        "proxy": body.proxy or "",
        "max_concurrency": min(body.max_concurrency, 5),
        "force_refresh": body.force_refresh_all,
    }

    await create_job(
        job_id=job_id,
        key_id=api_key["id"],
        job_type="crawl",
        config=json.dumps(config),
    )

    # Enqueue to arq
    status = "queued"
    warning = None
    try:
        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            raise RuntimeError("arq pool not available")
        await arq_pool.enqueue_job("crawl_job", job_id, config)
    except Exception as exc:
        status = "queue_failed"
        warning = f"Failed to enqueue job: {exc}"
        await update_job(job_id, status="queue_failed", error=warning)

    result: dict = {
        "success": True,
        "job_id": job_id,
        "status": status,
        "poll_url": f"/crawl/{job_id}",
    }
    if warning:
        result["warning"] = warning
    return result


@router.get("/crawl/{job_id}")
async def get_crawl_status(
    job_id: str,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    # Try Redis first
    try:
        from app.redis_client import get_redis
        redis = await get_redis()
        status_data = await redis.hgetall(f"crawl:{job_id}:status")
        if status_data:
            results_raw = await redis.lrange(f"crawl:{job_id}:results", 0, -1)
            results = [json.loads(r) for r in results_raw]
            return {
                "success": True,
                "job_id": job_id,
                "status": status_data.get("status", "unknown"),
                "pages_scraped": int(status_data.get("pages_scraped", 0)),
                "max_pages": int(status_data.get("max_pages", 0)),
                "results": results,
            }
    except Exception:
        pass

    # Fallback to SQLite
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "success": True,
        "job_id": job_id,
        "status": job["status"],
        "pages_scraped": job.get("pages_scraped", 0),
        "pages_total": job.get("pages_total"),
        "error": job.get("error"),
    }


@router.delete("/crawl/{job_id}")
async def cancel_crawl(
    job_id: str,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    try:
        from app.redis_client import get_redis
        redis = await get_redis()
        await redis.hset(f"crawl:{job_id}:status", "status", "cancelled")
    except Exception:
        pass

    return {"success": True, "job_id": job_id, "status": "cancelled"}
