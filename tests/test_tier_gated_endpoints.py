# tests/test_tier_gated_endpoints.py
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# Dummy key record returned by mocked auth
_FAKE_KEY = {"id": 1, "name": "test", "active": True, "is_admin": False}


@pytest.fixture
def client_without_gemini(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Reset the singleton tier gate so it re-reads env
    import app.services.tier_gate as tg
    tg._GATE = None

    from app.main import app
    # Override auth dependency to bypass DB lookup
    from app.auth import verify_api_key
    app.dependency_overrides[verify_api_key] = lambda: _FAKE_KEY
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()
    tg._GATE = None


def test_extract_returns_402_without_gemini(client_without_gemini: TestClient) -> None:
    resp = client_without_gemini.post(
        "/extract",
        json={"urls": ["https://example.com"], "schema": {"title": "string"}},
        headers={"Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 402
    body = resp.json()
    assert body["error"] == "tier_locked"
    assert body["tier"] == 1
    assert "GEMINI_API_KEY" in body["requires"]
    assert "03-get-gemini-key.md" in body["how_to_unlock"]


def test_search_returns_402_without_gemini(client_without_gemini: TestClient) -> None:
    resp = client_without_gemini.post(
        "/search",
        json={"query": "test query"},
        headers={"Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 402
    body = resp.json()
    assert body["error"] == "tier_locked"
    assert body["tier"] == 1


def test_scrape_with_extract_returns_402_without_gemini(client_without_gemini: TestClient) -> None:
    resp = client_without_gemini.post(
        "/scrape",
        json={
            "url": "https://example.com",
            "extract": {"schema": {"title": "string"}},
        },
        headers={"Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 402
    body = resp.json()
    assert body["error"] == "tier_locked"
    assert body["tier"] == 1
