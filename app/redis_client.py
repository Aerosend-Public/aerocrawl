from __future__ import annotations

from typing import Optional

import structlog
from redis.asyncio import Redis

from app.config import settings

logger = structlog.get_logger()

_redis: Optional[Redis] = None


async def get_redis() -> Redis:
    """Lazy-init and return the shared Redis connection."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        logger.info("Redis connection created", url=settings.REDIS_URL)
    return _redis


async def close_redis() -> None:
    """Close the shared Redis connection."""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
        logger.info("Redis connection closed")
