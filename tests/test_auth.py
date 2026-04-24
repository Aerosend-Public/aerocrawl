from __future__ import annotations

import os

os.environ["AEROCRAWL_DB_PATH"] = ":memory:"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import create_api_key, init_db, reset_shared_conn
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await reset_shared_conn()
    await init_db()
    yield
    await reset_shared_conn()


@pytest.mark.asyncio
async def test_health_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_scrape_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/scrape", json={"url": "https://example.com"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_key_passes():
    _, full_key = await create_api_key(name="valid-test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": f"Bearer {full_key}"},
        )
    assert resp.status_code == 200
    # Endpoint is now implemented — check it returns a valid scrape response
    data = resp.json()
    assert "success" in data


@pytest.mark.asyncio
async def test_invalid_key_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": "Bearer ns-00000000000000000000000000000000"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_x_api_key_header_works():
    _, full_key = await create_api_key(name="x-api-key-test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"X-API-Key": full_key},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_non_ns_prefix_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": "Bearer sk-not-a-valid-prefix-key"},
        )
    assert resp.status_code == 401
    assert "format" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_revoked_key_rejected():
    from app.db import revoke_key

    key_id, full_key = await create_api_key(name="revoke-test")
    await revoke_key(key_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": f"Bearer {full_key}"},
        )
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()
