from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from app.config import settings
from app.db import create_api_key, init_db, list_keys
from app.services.scraper import browser_pool, init_pool

logger = structlog.get_logger()

_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _start_time
    _start_time = time.time()

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Initialize browser pool and pre-launch the shared Chromium so the first
    # request doesn't pay the ~500ms browser-launch cost.
    pool = init_pool(settings.MAX_BROWSER_CONTEXTS)
    try:
        await pool._ensure_browser()
        logger.info("Browser pool initialized + warmed", max_contexts=settings.MAX_BROWSER_CONTEXTS)
    except Exception as exc:
        logger.warning("Browser warm-up skipped", error=str(exc))

    # Pre-create shared httpx client (HTTP/2, connection pool)
    try:
        from app.services.http_client import get_shared_client
        await get_shared_client()
        logger.info("Shared httpx client ready (http2=True)")
    except Exception as exc:
        logger.warning("http_client init failed", error=str(exc))

    # Try to connect Redis + create shared arq pool (optional — might not be available in dev/test)
    app.state.arq_pool = None
    try:
        from app.redis_client import get_redis
        await get_redis()
        logger.info("Redis connected")

        from arq import create_pool
        from arq.connections import RedisSettings
        app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        logger.info("arq pool initialized")
    except Exception as exc:
        logger.warning("Redis/arq not available", error=str(exc))

    # Bootstrap admin key if no keys exist and ADMIN_BOOTSTRAP_NAME is set
    if settings.ADMIN_BOOTSTRAP_NAME:
        existing = await list_keys()
        if not existing:
            key_id, full_key = await create_api_key(
                name=settings.ADMIN_BOOTSTRAP_NAME,
                is_admin=True,
            )
            print(f"\n{'=' * 60}")
            print(f"  ADMIN API KEY (store securely — shown only once)")
            print(f"  Name: {settings.ADMIN_BOOTSTRAP_NAME}")
            print(f"  Key:  {full_key}")
            print(f"{'=' * 60}\n")
            logger.info("Bootstrap admin key created", name=settings.ADMIN_BOOTSTRAP_NAME, key_id=key_id)

    logger.info("NinjaScraper started", env=settings.ENV, port=settings.PORT)
    yield

    # Shutdown browser pool
    from app.services.scraper import browser_pool as _pool
    if _pool is not None:
        await _pool.shutdown()
        logger.info("Browser pool shut down")

    # Close arq pool
    if getattr(app.state, "arq_pool", None) is not None:
        try:
            await app.state.arq_pool.close()
            logger.info("arq pool closed")
        except Exception:
            pass

    # Close shared httpx client
    try:
        from app.services.http_client import close_shared_client
        await close_shared_client()
    except Exception:
        pass

    # Close Redis
    try:
        from app.redis_client import close_redis
        await close_redis()
    except Exception:
        pass

    logger.info("NinjaScraper stopping")


app = FastAPI(
    title="NinjaScraper",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
from app.api.keys import router as keys_router  # noqa: E402
from app.api.usage import router as usage_router  # noqa: E402
from app.api.scrape import router as scrape_router  # noqa: E402
from app.api.screenshot import router as screenshot_router  # noqa: E402
from app.api.map import router as map_router  # noqa: E402
from app.api.crawl import router as crawl_router  # noqa: E402
from app.api.batch import router as batch_router  # noqa: E402
from app.api.extract import router as extract_router  # noqa: E402
from app.api.search import router as search_router  # noqa: E402
from app.api.cache_admin import router as cache_router  # noqa: E402
from app.api.budget import router as budget_router  # noqa: E402
from app.api.routes_info import router as routes_info_router  # noqa: E402
from app.api.strategy_admin import router as strategy_router  # noqa: E402

app.include_router(keys_router)
app.include_router(usage_router)
app.include_router(scrape_router)
app.include_router(screenshot_router)
app.include_router(map_router)
app.include_router(crawl_router)
app.include_router(batch_router)
app.include_router(extract_router)
app.include_router(search_router)
app.include_router(cache_router)
app.include_router(budget_router)
app.include_router(routes_info_router)
app.include_router(strategy_router)


@app.get("/health")
async def health() -> dict:
    from app.services.scraper import browser_pool as _pool

    uptime = time.time() - _start_time if _start_time else 0
    result: dict = {
        "status": "ok",
        "uptime": round(uptime, 2),
        "version": "0.1.0",
    }
    if _pool is not None:
        result["browser_pool"] = _pool.status()

    # Redis status
    try:
        from app.redis_client import get_redis
        redis = await get_redis()
        await redis.ping()
        pending = await redis.llen("arq:queue")
        result["redis"] = {"status": "connected", "pending_jobs": pending}
    except Exception:
        result["redis"] = {"status": "disconnected"}

    return result
