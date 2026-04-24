"""Zyte API web unlocker client — Step 9 of the fallback chain.

Zyte wins Proxyway 2025 (93% success on 15 hardest sites, CF Enterprise +
DataDome + Kasada). Tiered pricing: easy sites pennies, hard-tier ~$1/CPM.

Design constraints:
- Domain allowlist (no accidental hard-tier bills on random URLs)
- Monthly budget hard cap ($30) enforced via budget_guard
- Cost read from response header `Zyte-Request-Cost`
- Auth: HTTP Basic with API key as username, empty password
"""
from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.services import budget_guard

logger = structlog.get_logger()

_ZYTE_ENDPOINT = "https://api.zyte.com/v1/extract"
_PROVIDER = "zyte"


def _is_allowlisted(url: str) -> bool:
    """True if URL's host matches the Zyte allowlist."""
    if not settings.zyte_allowlist:
        return False
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(d in host for d in settings.zyte_allowlist)


def _parse_cost(headers) -> float:
    """Read per-request cost from Zyte response headers.

    Zyte currently doesn't include per-call cost in response headers/body;
    this is here for forwards-compat in case they add it. When absent, the
    caller falls back to `ZYTE_ESTIMATED_COST_PER_CALL` (see config.py).
    """
    for h in ("Zyte-Request-Cost", "zyte-request-cost", "x-zyte-cost"):
        raw = headers.get(h)
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    return 0.0


async def scrape_via_zyte(url: str, timeout_s: float = 120.0) -> Optional[dict]:
    """Scrape a URL via Zyte API. Returns None on skip/failure.

    Skipped when:
      - ZYTE_ENABLED is false or no API key configured
      - URL host is not on the allowlist
      - monthly budget would be exceeded
    """
    if not settings.ZYTE_ENABLED or not settings.ZYTE_API_KEY:
        return None

    if not _is_allowlisted(url):
        logger.debug("zyte: url not on allowlist, skipping", url=url)
        return None

    # Atomic budget reservation — conditional INSERT, no TOCTOU race.
    reserved = await budget_guard.reserve_spend(
        _PROVIDER,
        settings.ZYTE_ESTIMATED_COST_PER_CALL,
        settings.ZYTE_MONTHLY_BUDGET_USD,
        url=url,
    )
    if not reserved:
        spent = await budget_guard.current_spend(_PROVIDER)
        logger.warning(
            "zyte: monthly budget exhausted",
            spent_usd=spent,
            cap_usd=settings.ZYTE_MONTHLY_BUDGET_USD,
            url=url,
        )
        return None

    basic = base64.b64encode(f"{settings.ZYTE_API_KEY}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/json",
    }
    body = {
        "url": url,
        "browserHtml": True,
        "httpResponseHeaders": True,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(_ZYTE_ENDPOINT, json=body, headers=headers)
    except httpx.TimeoutException:
        await budget_guard.settle_spend(_PROVIDER, url=url, actual_cost_usd=0.0, success=False)
        logger.warning("zyte: timeout", url=url, timeout_s=timeout_s)
        return None
    except Exception as exc:
        await budget_guard.settle_spend(_PROVIDER, url=url, actual_cost_usd=0.0, success=False)
        logger.warning(
            "zyte: request error",
            url=url,
            error_type=type(exc).__name__,
        )
        return None

    cost = _parse_cost(resp.headers)

    if resp.status_code != 200:
        # Zyte docs: ban responses (520) are NOT billed. Settle to real cost.
        await budget_guard.settle_spend(_PROVIDER, url=url, actual_cost_usd=cost, success=False)
        logger.warning(
            "zyte: non-200 response",
            status=resp.status_code,
            url=url,
            cost=cost,
            body=resp.text[:300],
        )
        return None

    try:
        data = resp.json()
    except Exception:
        await budget_guard.settle_spend(_PROVIDER, url=url, actual_cost_usd=cost, success=False)
        logger.warning("zyte: invalid json response", url=url)
        return None

    html = data.get("browserHtml") or ""
    status_code = data.get("statusCode", 200)
    final_url = data.get("url", url)

    if not html or len(html.strip()) < 50:
        await budget_guard.settle_spend(_PROVIDER, url=url, actual_cost_usd=cost, success=False)
        logger.debug("zyte: empty response", url=url, cost=cost)
        return None

    # Zyte doesn't report cost in response — use configured estimate for
    # budget tracking. If they ever expose a real header, _parse_cost picks
    # it up and overrides the estimate.
    effective_cost = cost or settings.ZYTE_ESTIMATED_COST_PER_CALL
    await budget_guard.settle_spend(_PROVIDER, url=url, actual_cost_usd=effective_cost, success=True)
    logger.info(
        "zyte: success",
        url=url,
        cost_reported=cost,
        cost_tracked=effective_cost,
        status=status_code,
    )

    return {
        "html": html,
        "final_url": final_url,
        "status_code": status_code,
        "cost_usd": effective_cost,
    }
