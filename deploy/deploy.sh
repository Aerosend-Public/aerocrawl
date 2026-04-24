#!/usr/bin/env bash
# deploy/deploy.sh — update Aerocrawl in place on the VPS.
# Run on the VPS as root:
#   cd /opt/aerocrawl && ./deploy/deploy.sh
#
# What it does:
#   1. git pull
#   2. pip install any new deps
#   3. snapshot /opt/aerocrawl to /opt/aerocrawl.prev (rollback target)
#   4. systemctl restart aerocrawl aerocrawl-worker
#   5. poll /health for 20s; revert from snapshot if unhealthy

set -euo pipefail

INSTALL_DIR="/opt/aerocrawl"
SNAPSHOT_DIR="/opt/aerocrawl.prev"

log() { echo -e "\e[1;34m[deploy]\e[0m $*"; }
err() { echo -e "\e[1;31m[deploy]\e[0m $*" >&2; }

cd "$INSTALL_DIR"

log "git pull..."
git fetch origin
git reset --hard origin/main

log "pip install..."
venv/bin/pip install -r requirements.txt

log "Snapshotting current install for rollback..."
rm -rf "$SNAPSHOT_DIR"
cp -a "$INSTALL_DIR" "$SNAPSHOT_DIR"

log "Restarting services..."
systemctl restart aerocrawl aerocrawl-worker

source "$INSTALL_DIR/.env"
log "Polling https://$AEROCRAWL_DOMAIN/health (up to 20s)..."
for i in $(seq 1 10); do
    if curl -sSfk "https://$AEROCRAWL_DOMAIN/health" >/dev/null 2>&1; then
        log "Healthy after ${i}*2s. Deploy complete."
        exit 0
    fi
    sleep 2
done

err "Unhealthy after 20s. Rolling back..."
rm -rf "$INSTALL_DIR"
mv "$SNAPSHOT_DIR" "$INSTALL_DIR"
systemctl restart aerocrawl aerocrawl-worker
err "Rolled back. Check logs: journalctl -u aerocrawl -n 100"
exit 1
