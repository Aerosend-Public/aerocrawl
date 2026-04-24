from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.browser_pool import BrowserPool, parse_proxy


@pytest.mark.asyncio
async def test_pool_status():
    """Verify initial status shows 0 active."""
    pool = BrowserPool(max_contexts=3)
    status = pool.status()
    assert status["max_contexts"] == 3
    assert status["active_contexts"] == 0
    assert status["queued_requests"] == 0


@pytest.mark.asyncio
async def test_pool_respects_max_contexts():
    """Mock _create_context — verify 3rd acquire blocks until release."""
    pool = BrowserPool(max_contexts=2)

    mock_context = AsyncMock()
    mock_context.close = AsyncMock()

    with patch.object(pool, "_create_context", return_value=mock_context):
        # Acquire 2 contexts (should succeed immediately)
        ctx1 = await pool.acquire()
        ctx2 = await pool.acquire()
        assert pool.status()["active_contexts"] == 2

        # 3rd acquire should block — use a flag to check
        acquired_third = False

        async def try_acquire():
            nonlocal acquired_third
            await pool.acquire()
            acquired_third = True

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)
        assert not acquired_third, "3rd acquire should block when pool is full"
        assert pool.status()["queued_requests"] == 1

        # Release one — 3rd should now proceed
        await pool.release(ctx1)
        await asyncio.sleep(0.05)
        assert acquired_third, "3rd acquire should succeed after release"
        assert pool.status()["active_contexts"] == 2

        # Cleanup
        await pool.release(ctx2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_parse_proxy_full():
    result = parse_proxy("http://user:pass@proxy.example.com:8080")
    assert result is not None
    assert result["server"] == "http://proxy.example.com:8080"
    assert result["username"] == "user"
    assert result["password"] == "pass"


def test_parse_proxy_empty():
    assert parse_proxy("") is None
