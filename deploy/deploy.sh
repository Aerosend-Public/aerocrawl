#!/bin/bash
# Deploy Aerocrawl to Hetzner VPS with safe rollback + health gate
set -e

SERVER="root@203.0.113.42"
REMOTE_DIR="/opt/aerocrawl"
PREV_DIR="/opt/aerocrawl.prev"
HEALTH_URL="http://localhost:8001/health"

echo "=== Deploying Aerocrawl ==="

# 0. Snapshot current code to .prev for rollback
echo "Snapshotting current deployment to $PREV_DIR..."
ssh "$SERVER" "if [ -d $REMOTE_DIR ]; then rm -rf $PREV_DIR && cp -a $REMOTE_DIR $PREV_DIR; fi"

# 1. Sync code
echo "Syncing code..."
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude 'data/*.db' \
      --exclude '.env' --exclude '.git' --exclude 'tests' --exclude 'backups' \
      ./ "$SERVER:$REMOTE_DIR/"

# 2. Install dependencies
echo "Installing dependencies..."
ssh "$SERVER" "cd $REMOTE_DIR && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && .venv/bin/playwright install chromium --with-deps"

# 3. Ensure Redis is installed and running
echo "Checking Redis..."
ssh "$SERVER" "which redis-server || (apt-get update -qq && apt-get install -y -qq redis-server)"
ssh "$SERVER" "systemctl enable redis-server && systemctl start redis-server"

# 4. Copy service files (including backup timer)
echo "Setting up systemd services..."
ssh "$SERVER" "cp $REMOTE_DIR/deploy/aerocrawl.service /etc/systemd/system/ && \
               cp $REMOTE_DIR/deploy/aerocrawl-worker.service /etc/systemd/system/ && \
               cp $REMOTE_DIR/deploy/aerocrawl-backup.service /etc/systemd/system/ && \
               cp $REMOTE_DIR/deploy/aerocrawl-backup.timer /etc/systemd/system/ && \
               chmod +x $REMOTE_DIR/deploy/backup.sh && \
               systemctl daemon-reload"

# 5. Enable and restart services
echo "Restarting services..."
ssh "$SERVER" "systemctl enable aerocrawl aerocrawl-worker aerocrawl-backup.timer && \
               systemctl restart aerocrawl aerocrawl-worker && \
               systemctl start aerocrawl-backup.timer"

# 6. Post-deploy health gate (10 retries × 2s = 20s max)
echo "Health gate: waiting for /health to respond..."
HEALTH_OK=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  if ssh "$SERVER" "curl -sS -f -m 3 $HEALTH_URL >/dev/null 2>&1"; then
    HEALTH_OK=1
    echo "  Health check passed on attempt $i"
    break
  fi
  sleep 2
done

if [ "$HEALTH_OK" != "1" ]; then
  echo "!!! Health check FAILED after 10 attempts — rolling back"
  ssh "$SERVER" "systemctl stop aerocrawl aerocrawl-worker && \
                 rm -rf $REMOTE_DIR && \
                 mv $PREV_DIR $REMOTE_DIR && \
                 systemctl restart aerocrawl aerocrawl-worker"
  echo "Rolled back to previous deployment. Investigate the new code before retrying."
  exit 1
fi

# 7. Check status
echo "Checking status..."
ssh "$SERVER" "systemctl is-active aerocrawl aerocrawl-worker aerocrawl-backup.timer"

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Next steps (if first deploy):"
echo "  1. Create .env on VPS: cp /opt/aerocrawl/.env.example /opt/aerocrawl/.env && nano /opt/aerocrawl/.env"
echo "  2. Add to Caddy config (scraper.example.com block):"
echo "     handle_path /scraper/* {"
echo "       reverse_proxy localhost:8001"
echo "     }"
echo "  3. Reload Caddy: systemctl reload caddy"
echo "  4. Restart services: systemctl restart aerocrawl aerocrawl-worker"
echo "  5. Check admin key in logs: journalctl -u aerocrawl | head -30"
echo ""
echo "  URL: https://scraper.example.com/scraper/"
