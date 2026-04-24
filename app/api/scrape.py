from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel

from app.auth import verify_api_key
from app.db import log_usage
from app.services.scraper import scrape_url
from app.validation import validate_url

router = APIRouter(tags=["scrape"])


class ScrapeRequest(BaseModel):
    url: str
    formats: Optional[List[str]] = None
    proxy: Optional[str] = None
    wait_for: Optional[str] = None
    timeout_ms: Optional[int] = None
    selector: Optional[str] = None
    only_main_content: bool = True
    actions: Optional[List[Dict]] = None
    force_refresh: bool = False
    vision: Optional[Dict] = None
    # Firecrawl-style schema-first extract: scrape → Gemini → validated JSON
    extract: Optional[Dict] = None  # {"schema": {...}, "prompt": "..."}


@router.post("/scrape")
async def scrape(
    body: ScrapeRequest,
    response: Response,
    api_key: dict = Depends(verify_api_key),
    x_cache_bypass: Optional[str] = Header(default=None),
) -> dict:
    validate_url(body.url)

    force_refresh = body.force_refresh or bool(x_cache_bypass)

    result = await scrape_url(
        url=body.url,
        formats=body.formats or ["markdown"],
        proxy=body.proxy or "",
        # Changed default: `domcontentloaded` is ~3-5s faster on analytics-heavy
        # sites where `networkidle` never actually idles. Callers who need the
        # old behavior can still pass `wait_for: "networkidle"`.
        wait_for=body.wait_for or "domcontentloaded",
        timeout_ms=body.timeout_ms or 20000,  # was 30000 — fail fast
        selector=body.selector or "",
        only_main_content=body.only_main_content,
        actions=body.actions,
        force_refresh=force_refresh,
        vision=body.vision,
    )

    # X-Cache header for ops visibility
    if result.cached:
        response.headers["X-Cache"] = "HIT"
    elif force_refresh:
        response.headers["X-Cache"] = "BYPASS"
    else:
        response.headers["X-Cache"] = "MISS"

    await log_usage(
        key_id=api_key["id"],
        endpoint="/scrape",
        url=body.url,
        status_code=result.metadata.get("status_code"),
        duration_ms=result.duration_ms,
        scrape_method="cache" if result.cached else result.scrape_method,
        error=result.error or None,
    )

    out: dict = {
        "success": result.success,
        "url": body.url,
        "scrape_method": result.scrape_method,
        "duration_ms": result.duration_ms,
        "metadata": result.metadata,
        "cached": result.cached,
    }
    if result.cached:
        out["cache_age_seconds"] = result.cache_age_seconds
    if result.markdown:
        out["markdown"] = result.markdown
    if result.html:
        out["html"] = result.html
    if result.screenshot:
        out["screenshot"] = result.screenshot
    if result.links:
        out["links"] = result.links
    if result.action_results:
        out["action_results"] = result.action_results
    if result.error:
        out["error"] = result.error
    if result.block_type:
        out["block_type"] = result.block_type
        out["block_detail"] = result.block_detail
    if result.methods_tried:
        out["methods_tried"] = result.methods_tried
    if result.content_type:
        out["content_type"] = result.content_type
    if result.pages:
        out["pages"] = result.pages
    if result.images:
        out["images"] = result.images

    # Schema-first extract: runs Gemini over the scraped markdown against
    # a JSON schema. Retries once on malformed JSON. Returns `extracted` on
    # success or `extract_error` on failure — scrape result stays intact.
    if body.extract and result.success and result.markdown:
        from app.services.extractor_sync import extract_structured
        try:
            extracted = await extract_structured(
                markdown=result.markdown,
                schema=body.extract.get("schema") or {},
                prompt=body.extract.get("prompt") or "",
                source_url=body.url,
            )
            if extracted is not None:
                out["extracted"] = extracted
            else:
                out["extract_error"] = "LLM failed to produce valid JSON matching the schema"
        except Exception as exc:
            out["extract_error"] = f"{type(exc).__name__}: {exc}"

    return out
