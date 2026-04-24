from __future__ import annotations

from urllib.parse import quote

import httpx
import structlog

from app.config import settings
from app.services.content import html_to_markdown, extract_metadata

logger = structlog.get_logger()


async def scrape_via_cf_proxy(url: str, only_main_content: bool = True) -> dict | None:
    """Scrape a URL via the general-purpose Cloudflare Worker proxy.

    The CF Worker fetches the URL from Cloudflare's edge network, which has
    higher IP trust than our VPS. Helps bypass basic IP reputation blocking.

    Returns dict with keys: html, markdown, metadata, final_url, status_code
    Returns None if proxy is not configured or request fails.
    """
    proxy_url = settings.CF_PROXY_URL
    if not proxy_url:
        logger.debug("cf_proxy: CF_PROXY_URL not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                proxy_url,
                params={"url": url},
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )

            if resp.status_code != 200:
                logger.debug("cf_proxy: non-200 from worker", status=resp.status_code, url=url)
                return None

            # The worker returns JSON with status, body, and final_url
            try:
                data = resp.json()
                html = data.get("body", "")
                final_url = data.get("final_url", url)
                origin_status = data.get("status", 200)
            except Exception:
                # Fallback: treat response as raw HTML
                html = resp.text
                final_url = url
                origin_status = 200

            if not html or len(html.strip()) < 50:
                logger.debug("cf_proxy: empty response", url=url)
                return None

            markdown = html_to_markdown(html, only_main_content=only_main_content)

            # Check if extracted markdown is actually useful content
            clean_md = markdown.strip()
            if len(clean_md) < 30:
                logger.debug("cf_proxy: empty markdown after extraction", url=url, md_len=len(clean_md))
                return None

            metadata = extract_metadata(html, final_url, origin_status)

            return {
                "html": html,
                "markdown": markdown,
                "metadata": metadata,
                "final_url": final_url,
                "status_code": origin_status,
            }

    except Exception as exc:
        logger.debug("cf_proxy: request failed", url=url, error=str(exc))
        return None
