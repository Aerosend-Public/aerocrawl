from __future__ import annotations

import itertools
import json
import re
from datetime import datetime, timezone
from typing import List

import structlog

from app.config import settings
from app.db import update_job
from app.redis_client import get_redis
from app.services.scraper import scrape_url

logger = structlog.get_logger()

_EXPIRE_SECONDS = 86400  # 24 hours

# Round-robin iterator for Gemini API keys
_gemini_key_cycle = None


def _get_next_gemini_key() -> str:
    """Get the next Gemini API key via round-robin."""
    global _gemini_key_cycle
    keys = settings.gemini_key_list
    if not keys:
        raise ValueError("No Gemini API keys configured — set GEMINI_API_KEYS in .env")
    if _gemini_key_cycle is None:
        _gemini_key_cycle = itertools.cycle(keys)
    return next(_gemini_key_cycle)


async def _extract_with_gemini(system_prompt: str, user_message: str) -> str:
    """Call Google Gemini for structured extraction."""
    from google import genai

    api_key = _get_next_gemini_key()
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=f"{system_prompt}\n\n{user_message}",
    )
    return response.text


async def run_extract(job_id: str, config: dict) -> None:
    """Execute an LLM extract job using Google Gemini. Called by arq worker."""
    urls: List[str] = config["urls"]
    schema: dict = config.get("schema", {})
    prompt: str = config.get("prompt", "")

    redis = await get_redis()

    status_key = f"extract:{job_id}:status"
    results_key = f"extract:{job_id}:results"

    try:
        await update_job(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())
        await redis.hset(status_key, mapping={
            "status": "running",
            "urls_scraped": "0",
            "total_urls": str(len(urls)),
        })

        for key in (status_key, results_key):
            await redis.expire(key, _EXPIRE_SECONDS)

        # Step 1: Scrape all URLs (markdown format)
        scraped_contents: List[str] = []
        for i, url in enumerate(urls):
            result = await scrape_url(url=url, formats=["markdown"], only_main_content=True)
            if result.success and result.markdown:
                scraped_contents.append(f"--- Content from {url} ---\n{result.markdown}")
            await redis.hset(status_key, "urls_scraped", str(i + 1))

        if not scraped_contents:
            await redis.hset(status_key, mapping={"status": "failed"})
            await update_job(
                job_id,
                status="failed",
                error="No content could be scraped from any URL",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        combined_content = "\n\n".join(scraped_contents)

        # Step 2: LLM extraction via Gemini
        await redis.hset(status_key, "phase", "extracting")

        system_prompt = "You are a structured data extraction assistant. Extract data exactly as requested."
        user_message = (
            f"Extract structured data from the following web content.\n\n"
            f"Instructions: {prompt}\n\n"
            f"Schema: {json.dumps(schema)}\n\n"
            f"Content:\n{combined_content}\n\n"
            f"Respond with ONLY valid JSON matching the schema. No explanations."
        )

        raw_text = await _extract_with_gemini(system_prompt, user_message)
        logger.info("extract_completed", job_id=job_id, llm="gemini")

        # Strip markdown code fences if present
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        extracted = json.loads(cleaned)

        # Store results
        await redis.set(results_key, json.dumps(extracted))
        await redis.expire(results_key, _EXPIRE_SECONDS)
        await redis.hset(status_key, mapping={"status": "completed", "llm_used": "gemini"})
        await update_job(
            job_id,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    except json.JSONDecodeError as exc:
        logger.error("extract JSON parse failed", job_id=job_id, error=str(exc))
        try:
            await redis.hset(status_key, "status", "failed")
        except Exception:
            pass
        await update_job(
            job_id,
            status="failed",
            error=f"Failed to parse LLM response as JSON: {exc}",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:
        logger.error("extract failed", job_id=job_id, error=str(exc))
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
