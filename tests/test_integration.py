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
async def test_full_flow():
    """Full integration flow: health -> create admin -> create user -> list keys -> user can't list."""

    # Step 1: Health check (no auth)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Step 2: Create admin key directly in DB
        admin_id, admin_key = await create_api_key(name="integration-admin", is_admin=True)

        # Step 3: Admin creates a user key via API
        resp = await client.post(
            "/keys",
            json={"name": "integration-user", "team_member": "tester"},
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert resp.status_code == 200
        user_data = resp.json()
        assert user_data["name"] == "integration-user"
        assert user_data["key"].startswith("ns-")
        user_key = user_data["key"]

        # Step 4: Admin lists keys
        resp = await client.get(
            "/keys",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert resp.status_code == 200
        keys = resp.json()
        names = [k["name"] for k in keys]
        assert "integration-admin" in names
        assert "integration-user" in names

        # Step 5: User cannot list keys (403 — not admin)
        resp = await client.get(
            "/keys",
            headers={"Authorization": f"Bearer {user_key}"},
        )
        assert resp.status_code == 403

        # Step 6: User can access authenticated endpoints
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": f"Bearer {user_key}"},
        )
        assert resp.status_code == 200

        # Step 7: Admin revokes user key
        resp = await client.delete(
            f"/keys/{user_data['key_id']}",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert resp.status_code == 200

        # Step 8: Revoked user key is rejected
        resp = await client.post(
            "/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": f"Bearer {user_key}"},
        )
        assert resp.status_code == 401
