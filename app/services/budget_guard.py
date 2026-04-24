"""Monthly budget guard for paid scraping providers.

Enforces a hard cap (default: Zyte $30/mo). Before each paid call:
  - read current-month spend for provider
  - if spend >= cap → return False, skip the call
  - after success → log actual cost from provider response
  - post Slack alert (once per month) when spend crosses threshold

Monthly rollover: spend resets at UTC 00:00 on the 1st of each month.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from app.config import settings
from app.db import _get_db, _release_db

logger = structlog.get_logger()


def _current_ym() -> str:
    """Returns YYYY-MM for the current UTC month."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def current_spend(provider: str, ym: Optional[str] = None) -> float:
    """Return current month's total spend (USD) for a provider."""
    ym = ym or _current_ym()
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM budget_log WHERE provider = ? AND ym = ?",
            (provider, ym),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0
    finally:
        await _release_db(db)


async def can_spend(provider: str, cap_usd: float, min_headroom_usd: float = 0.05) -> bool:
    """Check whether a new call is within the monthly cap.

    Non-atomic check — callers should prefer `reserve_spend()` for TOCTOU
    safety. This function stays for read-only "will this fit?" queries.
    """
    if cap_usd <= 0:
        return False
    spent = await current_spend(provider)
    available = cap_usd - spent - min_headroom_usd
    return available > 0


async def reserve_spend(
    provider: str,
    estimated_cost_usd: float,
    cap_usd: float,
    url: Optional[str] = None,
) -> bool:
    """Atomically reserve budget for a paid call. Returns True if reserved.

    Uses a conditional INSERT so that concurrent callers can't both read
    `spent < cap` and then both spend — one SQL statement decides.

    On success: a placeholder row is inserted for `estimated_cost_usd` with
    `success=0` (pending). The caller should call `settle_spend()` after
    the call to update the actual cost and success flag.

    On failure (cap hit): returns False and writes nothing.
    """
    if cap_usd <= 0 or estimated_cost_usd <= 0:
        return False
    ym = _current_ym()
    now = datetime.now(timezone.utc).isoformat()

    db = await _get_db()
    try:
        # Conditional INSERT — only writes when current_spend + estimate <= cap.
        # The sub-select scans this month's rows; at our volume this is fast.
        cursor = await db.execute(
            """INSERT INTO budget_log (provider, url, cost_usd, success, created_at, ym)
               SELECT ?, ?, ?, 0, ?, ?
               WHERE (
                 SELECT COALESCE(SUM(cost_usd), 0.0)
                 FROM budget_log
                 WHERE provider = ? AND ym = ?
               ) + ? <= ?""",
            (
                provider, url, estimated_cost_usd, now, ym,
                provider, ym,
                estimated_cost_usd, cap_usd,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await _release_db(db)


async def settle_spend(
    provider: str,
    url: Optional[str],
    actual_cost_usd: float,
    success: bool,
) -> None:
    """Update the most recent pending row for this provider+url with the
    actual cost and success flag. Should be called after reserve_spend().

    If actual cost is higher than the reserved estimate, this still settles
    — we over-ran the cap, but only by the one call, and the next reserve
    will correctly reject.
    """
    ym = _current_ym()
    db = await _get_db()
    try:
        await db.execute(
            """UPDATE budget_log
               SET cost_usd = ?, success = ?
               WHERE id = (
                 SELECT id FROM budget_log
                 WHERE provider = ? AND ym = ? AND success = 0 AND url IS ?
                 ORDER BY id DESC LIMIT 1
               )""",
            (actual_cost_usd, int(success), provider, ym, url),
        )
        await db.commit()
    finally:
        await _release_db(db)


async def record_spend(
    provider: str,
    cost_usd: float,
    url: Optional[str] = None,
    success: bool = True,
) -> None:
    """Log a paid call to the budget ledger."""
    if cost_usd <= 0:
        return
    ym = _current_ym()
    now = datetime.now(timezone.utc).isoformat()
    db = await _get_db()
    try:
        await db.execute(
            """INSERT INTO budget_log (provider, url, cost_usd, success, created_at, ym)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (provider, url, cost_usd, int(success), now, ym),
        )
        await db.commit()
    finally:
        await _release_db(db)

    # Threshold alert (Zyte only — other providers can opt in later)
    if provider == "zyte":
        cap = settings.ZYTE_MONTHLY_BUDGET_USD
        threshold_pct = settings.ZYTE_ALERT_THRESHOLD_PCT
        if cap > 0 and threshold_pct > 0:
            spent = await current_spend(provider, ym)
            pct = spent / cap
            if pct >= threshold_pct:
                await _maybe_alert_slack(provider, ym, spent, cap, pct)


async def _maybe_alert_slack(provider: str, ym: str, spent: float, cap: float, pct: float) -> None:
    """Post a Slack alert once per month per provider, gated via Redis SETNX."""
    if not settings.SLACK_BOT_TOKEN or not settings.SLACK_CHANNEL_PIPELINE:
        return
    try:
        from app.redis_client import get_redis
        redis = await get_redis()
        alert_key = f"budget_alert:{provider}:{ym}:threshold"
        sent = await redis.set(alert_key, "1", nx=True, ex=40 * 86400)
        if not sent:
            return  # already alerted this month
    except Exception as exc:
        logger.debug("slack alert: redis check failed", error=str(exc))
        return

    text = (
        f":rotating_light: *{provider.upper()} spend at {pct*100:.0f}% of monthly cap*\n"
        f"• Spent: ${spent:.2f} / ${cap:.2f}\n"
        f"• Month: {ym}\n"
        f"• Provider will hard-stop on further calls when cap is reached.\n"
        f"• Check: `GET /scraper/budget/{provider}`"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "channel": settings.SLACK_CHANNEL_PIPELINE,
                    "text": text,
                    "unfurl_links": False,
                },
            )
        if resp.status_code != 200 or not resp.json().get("ok"):
            logger.warning("slack alert failed", status=resp.status_code, body=resp.text[:200])
        else:
            logger.info("slack alert sent", provider=provider, pct=pct)
    except Exception as exc:
        logger.warning("slack alert post error", error=str(exc))


async def monthly_summary(provider: str) -> dict:
    """Dashboard-friendly summary for the current month."""
    ym = _current_ym()
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT
                 COUNT(*) as calls,
                 COUNT(CASE WHEN success = 1 THEN 1 END) as successes,
                 COUNT(CASE WHEN success = 0 THEN 1 END) as failures,
                 COALESCE(SUM(cost_usd), 0.0) as spent_usd,
                 COALESCE(AVG(cost_usd), 0.0) as avg_cost_usd
               FROM budget_log WHERE provider = ? AND ym = ?""",
            (provider, ym),
        )
        row = await cursor.fetchone()
        if not row:
            return {"provider": provider, "ym": ym, "calls": 0, "spent_usd": 0.0}
        return {
            "provider": provider,
            "ym": ym,
            "calls": row[0],
            "successes": row[1],
            "failures": row[2],
            "spent_usd": round(float(row[3]), 4),
            "avg_cost_usd": round(float(row[4]), 4),
        }
    finally:
        await _release_db(db)
