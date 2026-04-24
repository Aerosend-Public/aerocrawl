"""Per-domain scrape strategy memoization.

Tracks which method succeeds per domain, reorders the fallback chain on
subsequent requests. Uses confidence-gated greedy with 5% exploration —
simple is right for our volume (~5.5K calls/mo, ~500 unique domains).

Schema:
  domain_strategy(domain, preferred_method, successes, failures,
                  last_success_at, last_failure_at, consec_failures,
                  stats_json, updated_at)

Decay: counters multiplied by 0.9 on each update — stale data fades without
needing a cron job.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.db import _get_db, _release_db

logger = structlog.get_logger()

# Tuning knobs
_MIN_SUCCESSES = 3.0          # absolute floor before promoting a method
_MIN_SUCCESS_RATE = 0.80      # required success rate within trailing window
_DECAY = 0.9                  # multiplicative decay on counter updates
_CONSEC_FAILURE_INVALIDATION = 3
_EXPLORATION_PROB = 0.05      # 5% of requests ignore preference (rediscovery)

# Subdomain split list — domains where subdomains behave so differently they
# deserve separate memoization rows.
_SUBDOMAIN_SPLIT: set[str] = {
    "github.com",
    "medium.com",
    "substack.com",
    "wordpress.com",
}


def domain_key(url: str) -> str:
    """Normalize a URL to its registrable-domain key.

    `tldextract` handles eTLD+1 correctly (e.g., `example.co.uk`). For sites
    on the subdomain-split list, use the full hostname instead.
    """
    try:
        from tldextract import extract
    except ImportError:
        # Fallback: naive hostname
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()

    try:
        ext = extract(url)
    except Exception:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()

    registered = ext.registered_domain.lower()
    if registered in _SUBDOMAIN_SPLIT and ext.subdomain:
        return f"{ext.subdomain}.{registered}"
    return registered or url


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calc_rate(stats: dict) -> float:
    """Success rate for a method's counters, with Laplace smoothing."""
    s = stats.get("s", 0.0)
    f = stats.get("f", 0.0)
    return (s + 1.0) / (s + f + 2.0)


async def get_preferred(domain: str) -> Optional[dict]:
    """Return the preferred method for a domain, or None if no confident choice.

    5% of calls return None (exploration) regardless of history — forces the
    default chain to run and keeps our stats honest.
    """
    if random.random() < _EXPLORATION_PROB:
        return None

    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT preferred_method, successes, failures, consec_failures FROM domain_strategy WHERE domain = ?",
            (domain,),
        )
        row = await cursor.fetchone()
    finally:
        await _release_db(db)

    if not row:
        return None

    method, successes, failures, consec = row[0], row[1] or 0.0, row[2] or 0.0, row[3] or 0
    if not method:
        return None
    if consec >= _CONSEC_FAILURE_INVALIDATION:
        return None
    if successes < _MIN_SUCCESSES:
        return None
    rate = (successes + 1.0) / (successes + failures + 2.0)
    if rate < _MIN_SUCCESS_RATE:
        return None
    return {
        "method": method,
        "successes": successes,
        "failures": failures,
        "consec_failures": consec,
        "rate": rate,
    }


async def record(domain: str, method: str, success: bool) -> None:
    """Update per-method counters + promote/demote preferred_method as needed.

    Counters apply _DECAY multiplicatively so old data fades away.
    """
    if not method or method.startswith("route:"):
        # Route hits are already O(1) — no chain to optimize. Skip.
        return

    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT preferred_method, successes, failures, consec_failures, stats_json FROM domain_strategy WHERE domain = ?",
            (domain,),
        )
        row = await cursor.fetchone()

        if row:
            preferred = row[0]
            stats = json.loads(row[4] or "{}")
        else:
            preferred = None
            stats = {}

        # Apply decay to all counters, then increment this one
        for m, entry in stats.items():
            entry["s"] = entry.get("s", 0.0) * _DECAY
            entry["f"] = entry.get("f", 0.0) * _DECAY

        if method not in stats:
            stats[method] = {"s": 0.0, "f": 0.0, "last_at": _now()}

        if success:
            stats[method]["s"] = stats[method].get("s", 0.0) + 1.0
            stats[method]["last_at"] = _now()
        else:
            stats[method]["f"] = stats[method].get("f", 0.0) + 1.0

        # Pick best candidate from decayed stats
        best_method = None
        best_rate = 0.0
        best_s = 0.0
        for m, entry in stats.items():
            s, f = entry.get("s", 0.0), entry.get("f", 0.0)
            rate = (s + 1.0) / (s + f + 2.0)
            # Minimum successes + minimum rate, plus a small hysteresis band
            # over current preferred to avoid flapping
            if s >= _MIN_SUCCESSES and rate >= _MIN_SUCCESS_RATE:
                if m == preferred:
                    if rate >= best_rate:
                        best_method, best_rate, best_s = m, rate, s
                else:
                    # Require a 10-point margin to displace the incumbent
                    margin = 0.10 if preferred else 0.0
                    if rate - best_rate > margin and s >= best_s:
                        best_method, best_rate, best_s = m, rate, s

        # Track consecutive failures of the preferred method only
        consec = (row[3] or 0) if row else 0
        if preferred and method == preferred:
            consec = 0 if success else consec + 1
        elif best_method and best_method != preferred:
            consec = 0

        new_preferred = best_method or preferred
        # Hard invalidation
        if consec >= _CONSEC_FAILURE_INVALIDATION:
            new_preferred = None
            consec = 0

        # Denormalized counters for the preferred method (fast path in get_preferred)
        pref_stats = stats.get(new_preferred, {}) if new_preferred else {}
        pref_s = pref_stats.get("s", 0.0)
        pref_f = pref_stats.get("f", 0.0)

        if row:
            await db.execute(
                """UPDATE domain_strategy
                   SET preferred_method = ?, successes = ?, failures = ?,
                       last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                       last_failure_at = CASE WHEN ? THEN last_failure_at ELSE ? END,
                       consec_failures = ?,
                       stats_json = ?,
                       updated_at = ?
                   WHERE domain = ?""",
                (
                    new_preferred, pref_s, pref_f,
                    int(success), _now(),
                    int(success), _now(),
                    consec,
                    json.dumps(stats),
                    _now(),
                    domain,
                ),
            )
        else:
            await db.execute(
                """INSERT INTO domain_strategy
                   (domain, preferred_method, successes, failures,
                    last_success_at, last_failure_at, consec_failures, stats_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    domain, new_preferred, pref_s, pref_f,
                    _now() if success else None,
                    None if success else _now(),
                    consec,
                    json.dumps(stats),
                    _now(),
                ),
            )
        await db.commit()
    except Exception as exc:
        logger.debug("strategy: record failed", domain=domain, method=method, error=str(exc))
    finally:
        await _release_db(db)


async def get_all() -> list[dict]:
    """Return all tracked domains for the admin dashboard."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT domain, preferred_method, successes, failures,
                      last_success_at, consec_failures, updated_at
               FROM domain_strategy ORDER BY updated_at DESC LIMIT 500"""
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await _release_db(db)
