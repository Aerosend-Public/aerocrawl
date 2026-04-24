# app/services/tier_gate.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class TierLockedError(Exception):
    """Raised when a request hits a feature whose tier is not active."""

    def __init__(
        self,
        feature: str,
        tier: int,
        missing_env_vars: list[str],
        how_to_unlock: str,
    ) -> None:
        self.feature = feature
        self.tier = tier
        self.missing_env_vars = missing_env_vars
        self.how_to_unlock = how_to_unlock
        super().__init__(
            f"Feature '{feature}' requires tier {tier}; missing: {missing_env_vars}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "tier_locked",
            "feature": self.feature,
            "tier": self.tier,
            "requires": self.missing_env_vars,
            "how_to_unlock": self.how_to_unlock,
        }


@dataclass(frozen=True)
class _Tier:
    id: int
    name: str
    required_env_vars: list[str]
    guide_paths: list[str]  # per required env, the how_to_get path
    always_on: bool
    unlocks: list[str]


class TierGate:
    REPO_BASE_URL = "https://github.com/Aerosend-Public/aerocrawl/blob/main"

    def __init__(self, tiers: list[_Tier], feature_tiers: dict[str, int]) -> None:
        self._tiers = {t.id: t for t in tiers}
        self._feature_tiers = feature_tiers

    @classmethod
    def from_yaml(cls, path: Path) -> "TierGate":
        data = yaml.safe_load(path.read_text())
        tiers = []
        for raw in data["tiers"]:
            required_env = [r["env"] for r in raw.get("requires", [])]
            guide_paths = [r.get("how_to_get", "") for r in raw.get("requires", [])]
            tiers.append(
                _Tier(
                    id=raw["id"],
                    name=raw["name"],
                    required_env_vars=required_env,
                    guide_paths=guide_paths,
                    always_on=raw.get("always_on", False),
                    unlocks=list(raw.get("unlocks", [])),
                )
            )
        feature_tiers = dict(data.get("feature_tiers", {}))
        return cls(tiers, feature_tiers)

    def is_tier_active(self, tier_id: int) -> bool:
        tier = self._tiers[tier_id]
        if tier.always_on:
            return True
        return all(os.environ.get(v) for v in tier.required_env_vars)

    def required_tier(self, feature: str) -> int:
        if feature not in self._feature_tiers:
            raise KeyError(f"unknown feature: {feature}")
        return self._feature_tiers[feature]

    def check_feature(self, feature: str) -> None:
        tier_id = self.required_tier(feature)
        tier = self._tiers[tier_id]
        if self.is_tier_active(tier_id):
            return
        missing = [v for v in tier.required_env_vars if not os.environ.get(v)]
        guide = tier.guide_paths[0] if tier.guide_paths else ""
        how_to_unlock = f"{self.REPO_BASE_URL}/{guide}" if guide else f"{self.REPO_BASE_URL}/README.md"
        raise TierLockedError(
            feature=feature,
            tier=tier_id,
            missing_env_vars=missing,
            how_to_unlock=how_to_unlock,
        )

    def capability_matrix(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for tier_id in sorted(self._tiers.keys()):
            tier = self._tiers[tier_id]
            active = self.is_tier_active(tier_id)
            missing = [v for v in tier.required_env_vars if not os.environ.get(v)]
            rows.append(
                {
                    "id": tier.id,
                    "name": tier.name,
                    "active": active,
                    "unlocks": tier.unlocks,
                    "missing": missing,
                }
            )
        return rows


_GATE: TierGate | None = None


def get_tier_gate() -> TierGate:
    global _GATE
    if _GATE is None:
        caps_path = Path(__file__).resolve().parent.parent.parent / "capabilities.yaml"
        _GATE = TierGate.from_yaml(caps_path)
    return _GATE
