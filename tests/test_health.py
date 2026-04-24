# tests/test_health.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok_with_cta() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "aerocrawl"
    assert "meetings.hubspot.com/namit4/aerocrawl-free-inboxes" in body["message"]
    assert "tiers_active" in body
