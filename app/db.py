from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    key_hash TEXT UNIQUE NOT NULL,
    key_prefix TEXT NOT NULL,
    name TEXT NOT NULL,
    team_member TEXT,
    is_admin INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    rate_limit_per_minute INTEGER,  -- NULL = use global default from settings
    rate_limit_per_hour INTEGER
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id TEXT NOT NULL REFERENCES api_keys(id),
    endpoint TEXT NOT NULL,
    url TEXT,
    status_code INTEGER,
    duration_ms INTEGER,
    scrape_method TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    key_id TEXT NOT NULL REFERENCES api_keys(id),
    type TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
    config TEXT DEFAULT '{}',
    pages_scraped INTEGER DEFAULT 0,
    pages_total INTEGER,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);

-- V3: Zyte budget ledger
CREATE TABLE IF NOT EXISTS budget_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    url TEXT,
    cost_usd REAL NOT NULL,
    success INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    ym TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_log_ym ON budget_log(provider, ym);

-- V3: per-domain strategy memoization (Phase 5)
CREATE TABLE IF NOT EXISTS domain_strategy (
    domain TEXT PRIMARY KEY,
    preferred_method TEXT,
    successes REAL NOT NULL DEFAULT 0,
    failures REAL NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_failure_at TEXT,
    consec_failures INTEGER NOT NULL DEFAULT 0,
    stats_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""

# Module-level connection cache for :memory: databases (test support)
_shared_conn: Optional[aiosqlite.Connection] = None


def _get_db_path() -> str:
    return os.environ.get("AEROCRAWL_DB_PATH", "data/aerocrawl.db")


async def _get_db() -> aiosqlite.Connection:
    global _shared_conn
    path = _get_db_path()

    # For :memory: databases, reuse a single connection so all callers
    # share the same in-memory state (needed for tests).
    if path == ":memory:":
        if _shared_conn is None:
            _shared_conn = await aiosqlite.connect(":memory:")
            _shared_conn.row_factory = aiosqlite.Row
        return _shared_conn

    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def _release_db(db: aiosqlite.Connection) -> None:
    """Close the connection unless it's the shared :memory: connection."""
    if db is _shared_conn:
        return
    await db.close()


async def reset_shared_conn() -> None:
    """Close and reset the shared in-memory connection (for test teardown)."""
    global _shared_conn
    if _shared_conn is not None:
        await _shared_conn.close()
        _shared_conn = None


def _hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    path = _get_db_path()
    if path != ":memory:":
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
    db = await _get_db()
    try:
        await db.executescript(SCHEMA)
        # Light migrations for V3.1: add columns that may be missing on
        # pre-existing DBs. SQLite doesn't have IF NOT EXISTS for ALTER TABLE.
        for alter, column in [
            ("ALTER TABLE api_keys ADD COLUMN rate_limit_per_minute INTEGER", "rate_limit_per_minute"),
            ("ALTER TABLE api_keys ADD COLUMN rate_limit_per_hour INTEGER", "rate_limit_per_hour"),
        ]:
            cursor = await db.execute("PRAGMA table_info(api_keys)")
            cols = {row[1] for row in await cursor.fetchall()}
            if column not in cols:
                try:
                    await db.execute(alter)
                except Exception:
                    pass
        await db.commit()
    finally:
        await _release_db(db)


async def create_api_key(
    name: str,
    team_member: Optional[str] = None,
    is_admin: bool = False,
) -> Tuple[str, str]:
    """Create a new API key. Returns (key_id, full_key)."""
    key_id = secrets.token_hex(16)
    raw_secret = secrets.token_hex(16)
    full_key = f"ns-{raw_secret}"
    key_hash = _hash_key(full_key)
    key_prefix = full_key[:10]

    db = await _get_db()
    try:
        await db.execute(
            """INSERT INTO api_keys (id, key_hash, key_prefix, name, team_member, is_admin, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (key_id, key_hash, key_prefix, name, team_member, int(is_admin), _now()),
        )
        await db.commit()
    finally:
        await _release_db(db)

    return key_id, full_key


async def get_key_by_hash(full_key: str) -> Optional[dict]:
    """Look up an API key by its full value (hashed). Updates last_used_at."""
    key_hash = _hash_key(full_key)
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        await db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?",
            (_now(), key_hash),
        )
        await db.commit()
        return result
    finally:
        await _release_db(db)


async def list_keys() -> list:
    """List all API keys (without hashes)."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT id, key_prefix, name, team_member, is_admin, active, created_at, last_used_at FROM api_keys"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await _release_db(db)


async def revoke_key(key_id: str) -> None:
    """Deactivate an API key."""
    db = await _get_db()
    try:
        await db.execute("UPDATE api_keys SET active = 0 WHERE id = ?", (key_id,))
        await db.commit()
    finally:
        await _release_db(db)


async def log_usage(
    key_id: str,
    endpoint: str,
    url: Optional[str] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    scrape_method: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Log an API usage event."""
    db = await _get_db()
    try:
        await db.execute(
            """INSERT INTO usage_log (key_id, endpoint, url, status_code, duration_ms, scrape_method, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (key_id, endpoint, url, status_code, duration_ms, scrape_method, error, _now()),
        )
        await db.commit()
    finally:
        await _release_db(db)


async def get_usage_stats(key_id: str, days: int = 30) -> dict:
    """Get usage statistics for a key over the given number of days."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT
                 COUNT(*) as total_requests,
                 COUNT(CASE WHEN error IS NULL THEN 1 END) as successful,
                 COUNT(CASE WHEN error IS NOT NULL THEN 1 END) as failed,
                 AVG(duration_ms) as avg_duration_ms
               FROM usage_log
               WHERE key_id = ?
                 AND created_at >= datetime('now', ?)""",
            (key_id, f"-{days} days"),
        )
        row = await cursor.fetchone()
        result = dict(row) if row else {}

        cursor2 = await db.execute(
            """SELECT endpoint, COUNT(*) as count
               FROM usage_log
               WHERE key_id = ? AND created_at >= datetime('now', ?)
               GROUP BY endpoint ORDER BY count DESC""",
            (key_id, f"-{days} days"),
        )
        by_endpoint = [dict(r) for r in await cursor2.fetchall()]
        result["by_endpoint"] = by_endpoint
        return result
    finally:
        await _release_db(db)


async def create_job(
    job_id: str,
    key_id: str,
    job_type: str,
    config: str = "{}",
) -> None:
    """Create a new job record."""
    db = await _get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, key_id, type, status, config, created_at)
               VALUES (?, ?, ?, 'queued', ?, ?)""",
            (job_id, key_id, job_type, config, _now()),
        )
        await db.commit()
    finally:
        await _release_db(db)


VALID_JOB_FIELDS = {"status", "pages_scraped", "pages_total", "started_at", "completed_at", "error"}


async def update_job(job_id: str, **fields: object) -> None:
    """Update job fields."""
    if not fields:
        return
    invalid = set(fields) - VALID_JOB_FIELDS
    if invalid:
        raise ValueError(f"Invalid job fields: {invalid}")
    set_clauses = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    db = await _get_db()
    try:
        await db.execute(
            f"UPDATE jobs SET {set_clauses} WHERE id = ?", values
        )
        await db.commit()
    finally:
        await _release_db(db)


async def get_job(job_id: str) -> Optional[dict]:
    """Get a job by ID."""
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await _release_db(db)
