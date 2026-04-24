"""Per-API-key rate limiter using Redis atomic INCR counters.

Two windows enforced simultaneously:
  - per-minute (default 60)
  - per-hour (default 1000)

Admin keys bypass rate limits. Limits are overridable per-key via
`api_keys.rate_limit_per_minute` / `api_keys.rate_limit_per_hour` columns
(nullable — NULL means use the global default from settings).

Implementation: one INCR per window per request. O(1) Redis ops.
On Redis outage, the limiter fails OPEN (returns allowed=True) to avoid
bricking the service — observable via structlog warning.
"""
from __future__ import annotations

import time

import structlog
from fastapi import HTTPException

from app.config import settings
from app.redis_client import get_redis

logger = structlog.get_logger()


def _current_minute_bucket() -> int:
    return int(time.time()) // 60


def _current_hour_bucket() -> int:
    return int(time.time()) // 3600


async def check_and_increment(
    key_id: str,
    is_admin: bool,
    per_minute: int | None = None,
    per_hour: int | None = None,
) -> None:
    """Raise HTTPException(429) if this key is over quota. Increments on success.

    Admins are never rate-limited. Missing Redis fails OPEN.
    """
    if is_admin or not settings.RATE_LIMIT_ENABLED:
        return

    m_limit = per_minute if per_minute is not None else settings.RATE_LIMIT_PER_MINUTE
    h_limit = per_hour if per_hour is not None else settings.RATE_LIMIT_PER_HOUR
    if m_limit <= 0 and h_limit <= 0:
        return

    m_bucket = _current_minute_bucket()
    h_bucket = _current_hour_bucket()
    m_key = f"ratelimit:{key_id}:m:{m_bucket}"
    h_key = f"ratelimit:{key_id}:h:{h_bucket}"

    try:
        redis = await get_redis()
        pipe = redis.pipeline()
        pipe.incr(m_key)
        pipe.expire(m_key, 65)
        pipe.incr(h_key)
        pipe.expire(h_key, 3700)
        results = await pipe.execute()
        m_count = int(results[0])
        h_count = int(results[2])
    except Exception as exc:
        logger.warning("rate_limit: redis unavailable, failing open", error=str(exc))
        return

    if m_limit > 0 and m_count > m_limit:
        logger.info(
            "rate_limit: minute cap hit",
            key_id=key_id, count=m_count, limit=m_limit,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {m_count}/{m_limit} per minute. Retry in <60s.",
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(m_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": "minute",
            },
        )
    if h_limit > 0 and h_count > h_limit:
        logger.info(
            "rate_limit: hour cap hit",
            key_id=key_id, count=h_count, limit=h_limit,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {h_count}/{h_limit} per hour.",
            headers={
                "Retry-After": "3600",
                "X-RateLimit-Limit": str(h_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": "hour",
            },
        )
