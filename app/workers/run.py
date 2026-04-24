from __future__ import annotations

from arq.connections import RedisSettings


def _get_settings():
    from app.config import settings
    return settings


async def crawl_job(ctx: dict, job_id: str, config: dict) -> None:
    from app.services.crawler import run_crawl
    await run_crawl(job_id, config)


async def batch_job(ctx: dict, job_id: str, config: dict) -> None:
    from app.services.batch_scraper import run_batch
    await run_batch(job_id, config)


async def extract_job(ctx: dict, job_id: str, config: dict) -> None:
    from app.services.extractor import run_extract
    await run_extract(job_id, config)


async def startup(ctx: dict) -> None:
    from app.db import init_db
    from app.services.scraper import init_pool
    s = _get_settings()
    await init_db()
    init_pool(max_contexts=s.MAX_BROWSER_CONTEXTS)


async def shutdown(ctx: dict) -> None:
    from app.services.scraper import browser_pool
    if browser_pool:
        await browser_pool.shutdown()


class WorkerSettings:
    functions = [crawl_job, batch_job, extract_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_get_settings().REDIS_URL)
    max_jobs = 3
    job_timeout = 600
