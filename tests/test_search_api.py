from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock, patch

# Stub out Playwright modules so app.main can be imported without a browser install
for _mod in ("playwright_stealth", "playwright", "playwright.async_api"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest


@pytest.fixture
def client():
    """Create a test client with dependency override for auth."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import verify_api_key

    async def _mock_auth():
        return {"id": 1, "name": "test", "is_admin": False}

    app.dependency_overrides[verify_api_key] = _mock_auth
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_search_endpoint_returns_results(client):
    """POST /search returns structured results."""
    mock_result = {
        "success": True,
        "query": "test",
        "search_engine": "brave_html",
        "result_count": 1,
        "duration_ms": 100,
        "results": [{"title": "Test", "url": "https://example.com", "description": "A test"}],
    }
    with patch("app.api.search.search_fn", new_callable=AsyncMock, return_value=mock_result):
        resp = client.post("/search", json={"query": "test"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["search_engine"] == "brave_html"
    assert len(data["results"]) == 1


def test_search_endpoint_validates_empty_query(client):
    """POST /search rejects empty query."""
    resp = client.post("/search", json={"query": ""})
    assert resp.status_code == 422
