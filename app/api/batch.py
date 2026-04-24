from __future__ import annotations

import json
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import verify_api_key
from app.db import create_job, get_job, update_job
from app.validation import validate_url

router = APIRouter(tags=["batch"])


class BatchScrapeRequest(BaseModel):
    urls: List[str]
    formats: Optional[List[str]] = None
    proxy: Optional[str] = None
    only_main_content: bool = True
    force_refresh_all: bool = False


@router.post("/batch/scrape")
async def start_batch(
    body: BatchScrapeRequest,
    request: Request,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    if len(body.urls) > 100:
        raise HTTPException(400, "Maximum 100 URLs allowed per batch request")

    for url in body.urls:
        validate_url(url)

    job_id = f"batch_{secrets.token_hex(8)}"

    config = {
        "urls": body.urls,
        "formats": body.formats or ["markdown"],
        "proxy": body.proxy or "",
        "only_main_content": body.only_main_content,
        "force_refresh": body.force_refresh_all,
    }

    await create_job(
        job_id=job_id,
        key_id=api_key["id"],
        job_type="batch",
        config=json.dumps(config),
    )

    # Enqueue to arq
    status = "queued"
    warning = None
    try:
        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            raise RuntimeError("arq pool not available")
        await arq_pool.enqueue_job("batch_job", job_id, config)
    except Exception as exc:
        status = "queue_failed"
        warning = f"Failed to enqueue job: {exc}"
        await update_job(job_id, status="queue_failed", error=warning)

    result: dict = {
        "success": True,
        "job_id": job_id,
        "total_urls": len(body.urls),
        "status": status,
        "poll_url": f"/batch/{job_id}",
    }
    if warning:
        result["warning"] = warning
    return result


@router.get("/batch/{job_id}")
async def get_batch_status(
    job_id: str,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    # Try Redis first
    try:
        from app.redis_client import get_redis
        redis = await get_redis()
        status_data = await redis.hgetall(f"batch:{job_id}:status")
        if status_data:
            results_raw = await redis.lrange(f"batch:{job_id}:results", 0, -1)
            results = [json.loads(r) for r in results_raw]
            return {
                "success": True,
                "job_id": job_id,
                "status": status_data.get("status", "unknown"),
                "completed": int(status_data.get("completed", 0)),
                "total": int(status_data.get("total", 0)),
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
        "error": job.get("error"),
    }
