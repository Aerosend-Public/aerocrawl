# tests/test_tier_gate.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.tier_gate import TierGate, TierLockedError


REPO_ROOT = Path(__file__).resolve().parent.parent
CAPS_YAML = REPO_ROOT / "capabilities.yaml"


@pytest.fixture
def gate() -> TierGate:
    return TierGate.from_yaml(CAPS_YAML)


def test_tier_0_is_always_active(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ["GEMINI_API_KEY", "REDDIT_PROXY_URL", "CF_PROXY_URL", "PROXY_URL", "TAVILY_API_KEY", "ZYTE_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    assert gate.is_tier_active(0) is True


def test_tier_1_inactive_without_gemini_key(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gate.is_tier_active(1) is False


def test_tier_1_active_with_gemini_key(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    assert gate.is_tier_active(1) is True


def test_tier_2_requires_both_cf_keys(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDDIT_PROXY_URL", raising=False)
    monkeypatch.delenv("CF_PROXY_URL", raising=False)
    assert gate.is_tier_active(2) is False

    monkeypatch.setenv("REDDIT_PROXY_URL", "https://x.workers.dev")
    assert gate.is_tier_active(2) is False  # still missing CF_PROXY_URL

    monkeypatch.setenv("CF_PROXY_URL", "https://y.workers.dev")
    assert gate.is_tier_active(2) is True


def test_feature_tier_lookup(gate: TierGate) -> None:
    assert gate.required_tier("llm_extract") == 1
    assert gate.required_tier("reddit_scrape") == 2
    assert gate.required_tier("zyte_unlock") == 5


def test_unknown_feature_raises(gate: TierGate) -> None:
    with pytest.raises(KeyError):
        gate.required_tier("nonexistent_feature")


def test_check_feature_available_when_tier_active(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    gate.check_feature("llm_extract")  # should not raise


def test_check_feature_raises_tier_locked(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(TierLockedError) as exc:
        gate.check_feature("llm_extract")
    err = exc.value
    assert err.tier == 1
    assert err.feature == "llm_extract"
    assert "GEMINI_API_KEY" in err.missing_env_vars
    assert "install/guides/03-get-gemini-key.md" in err.how_to_unlock


def test_capability_matrix_shape(gate: TierGate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("REDDIT_PROXY_URL", raising=False)
    monkeypatch.delenv("CF_PROXY_URL", raising=False)
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("ZYTE_API_KEY", raising=False)

    matrix = gate.capability_matrix()
    assert len(matrix) == 6  # tiers 0-5
    assert matrix[0]["active"] is True
    assert matrix[1]["active"] is True
    assert matrix[2]["active"] is False
    assert matrix[2]["missing"] == ["REDDIT_PROXY_URL", "CF_PROXY_URL"]
