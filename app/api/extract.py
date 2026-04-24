from __future__ import annotations

import json
import secrets
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.db import create_job, get_job, update_job
from app.validation import validate_url

router = APIRouter(tags=["extract"])


class ExtractRequest(BaseModel):
    urls: List[str]
    extract_schema: Optional[Dict] = Field(None, alias="schema")
    prompt: str = ""

    model_config = {"populate_by_name": True}


@router.post("/extract")
async def start_extract(
    body: ExtractRequest,
    request: Request,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    if len(body.urls) > 10:
        raise HTTPException(400, "Maximum 10 URLs allowed per extract request")

    for url in body.urls:
        validate_url(url)

    job_id = f"extract_{secrets.token_hex(8)}"

    config = {
        "urls": body.urls,
        "schema": body.extract_schema or {},
        "prompt": body.prompt,
    }

    await create_job(
        job_id=job_id,
        key_id=api_key["id"],
        job_type="extract",
        config=json.dumps(config),
    )

    # Enqueue to arq
    status = "queued"
    warning = None
    try:
        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool is None:
            raise RuntimeError("arq pool not available")
        await arq_pool.enqueue_job("extract_job", job_id, config)
    except Exception as exc:
        status = "queue_failed"
        warning = f"Failed to enqueue job: {exc}"
        await update_job(job_id, status="queue_failed", error=warning)

    result: dict = {
        "success": True,
        "job_id": job_id,
        "status": status,
        "poll_url": f"/extract/{job_id}",
    }
    if warning:
        result["warning"] = warning
    return result


@router.get("/extract/{job_id}")
async def get_extract_status(
    job_id: str,
    api_key: dict = Depends(verify_api_key),
) -> dict:
    # Try Redis first
    try:
        from app.redis_client import get_redis
        redis = await get_redis()
        status_data = await redis.hgetall(f"extract:{job_id}:status")
        if status_data:
            result_raw = await redis.get(f"extract:{job_id}:results")
            extracted = json.loads(result_raw) if result_raw else None
            return {
                "success": True,
                "job_id": job_id,
                "status": status_data.get("status", "unknown"),
                "urls_scraped": int(status_data.get("urls_scraped", 0)),
                "total_urls": int(status_data.get("total_urls", 0)),
                "data": extracted,
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
        "error": job.get("error"),
    }
