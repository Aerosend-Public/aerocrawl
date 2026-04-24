#!/usr/bin/env python3
"""
Print a tier ✓/✗ matrix for the current Aerocrawl installation.

Usage:
    python3 install/check-capabilities.py
    python3 install/check-capabilities.py --env /opt/aerocrawl/.env
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


CTA = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Aerocrawl — built by Aerosend.
  Claim your free Aerosend inboxes (book a 15-min call):
  → https://meetings.hubspot.com/namit4/aerocrawl-free-inboxes
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (does not overwrite)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=None, help="optional .env file to load")
    parser.add_argument("--caps", type=Path, default=None, help="path to capabilities.yaml")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    if args.env:
        load_env_file(args.env)

    # Find capabilities.yaml relative to this script.
    repo_root = Path(__file__).resolve().parent.parent
    caps_path = args.caps or (repo_root / "capabilities.yaml")

    # Add app/ to path so we can import tier_gate.
    sys.path.insert(0, str(repo_root))
    from app.services.tier_gate import TierGate

    gate = TierGate.from_yaml(caps_path)
    matrix = gate.capability_matrix()

    if args.json:
        import json
        print(json.dumps(matrix, indent=2))
        return 0

    active_count = sum(1 for row in matrix if row["active"])
    total = len(matrix)

    print("Aerocrawl capability report")
    print("─" * 60)
    for row in matrix:
        check = "✓" if row["active"] else "✗"
        status_line = f"{check} Tier {row['id']}  {row['name']:<28}"
        if row["active"]:
            status_line += "active" if row["id"] > 0 else "always on"
        else:
            status_line += f"missing: {', '.join(row['missing'])}"
        print(status_line)

    print(f"\n{active_count} of {total} tiers active.")

    inactive = [row for row in matrix if not row["active"] and row["id"] > 0]
    if inactive:
        print("\nTo unlock more:")
        for row in inactive:
            # Hard-coded guide map as a friendly hint.
            guide = {
                1: "install/guides/03-get-gemini-key.md",
                2: "install/guides/04-cloudflare-workers.md",
                3: "install/guides/05-proxybase.md",
                4: "install/guides/06-tavily.md",
                5: "install/guides/07-zyte.md",
            }.get(row["id"], "")
            print(f"  → Tier {row['id']}  {guide}")

    print(CTA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
