from __future__ import annotations

import os

os.environ["AEROCRAWL_DB_PATH"] = ":memory:"

import pytest
import pytest_asyncio

from app.db import (
    create_api_key,
    get_key_by_hash,
    init_db,
    list_keys,
    log_usage,
    get_usage_stats,
    reset_shared_conn,
    revoke_key,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await reset_shared_conn()
    await init_db()
    yield
    await reset_shared_conn()


@pytest.mark.asyncio
async def test_create_and_retrieve_key():
    key_id, full_key = await create_api_key(name="test-key", team_member="tester")
    assert full_key.startswith("ns-")
    assert len(full_key) == 35  # "ns-" + 32 hex chars

    record = await get_key_by_hash(full_key)
    assert record is not None
    assert record["id"] == key_id
    assert record["name"] == "test-key"
    assert record["team_member"] == "tester"
    assert record["active"] == 1
    assert record["is_admin"] == 0


@pytest.mark.asyncio
async def test_create_admin_key():
    key_id, full_key = await create_api_key(name="admin", is_admin=True)
    record = await get_key_by_hash(full_key)
    assert record["is_admin"] == 1


@pytest.mark.asyncio
async def test_list_keys():
    await create_api_key(name="key-1")
    await create_api_key(name="key-2")
    keys = await list_keys()
    names = [k["name"] for k in keys]
    assert "key-1" in names
    assert "key-2" in names
    # Ensure no hash is returned
    for k in keys:
        assert "key_hash" not in k


@pytest.mark.asyncio
async def test_revoke_key():
    key_id, full_key = await create_api_key(name="to-revoke")
    await revoke_key(key_id)
    record = await get_key_by_hash(full_key)
    assert record is not None
    assert record["active"] == 0


@pytest.mark.asyncio
async def test_invalid_key_returns_none():
    result = await get_key_by_hash("ns-0000000000000000000000000000dead")
    assert result is None


@pytest.mark.asyncio
async def test_log_usage_and_stats():
    key_id, _ = await create_api_key(name="usage-test")
    await log_usage(key_id, endpoint="/scrape", url="https://example.com", status_code=200, duration_ms=150)
    await log_usage(key_id, endpoint="/scrape", url="https://fail.com", status_code=500, duration_ms=300, error="timeout")
    await log_usage(key_id, endpoint="/screenshot", url="https://example.com", status_code=200, duration_ms=200)

    stats = await get_usage_stats(key_id, days=30)
    assert stats["total_requests"] == 3
    assert stats["successful"] == 2
    assert stats["failed"] == 1
    assert len(stats["by_endpoint"]) == 2
