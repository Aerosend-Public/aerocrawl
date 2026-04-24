#!/usr/bin/env bash
# install/bootstrap.sh
# Aerocrawl VPS bootstrap. Idempotent — safe to re-run.
#
# Env vars (set by caller over SSH):
#   AEROCRAWL_DOMAIN   required — hostname for Caddy (e.g., scraper.example.com or 1-2-3-4.sslip.io)
#   ADMIN_EMAIL        required — used for Caddy ACME account
#   GEMINI_API_KEY     optional — Tier 1 unlock
#   REDDIT_PROXY_URL   optional — Tier 2 partial
#   CF_PROXY_URL       optional — Tier 2 partial
#   PROXY_URL          optional — Tier 3 unlock
#   TAVILY_API_KEY     optional — Tier 4 unlock
#   ZYTE_API_KEY       optional — Tier 5 unlock
#   ANONYMOUS_INSTALL_PING  optional — "1" to opt in to anonymous install telemetry
#
# What this does:
#   1. Pre-flight: Ubuntu 22.04+, 2+ GB RAM, 10+ GB disk free
#   2. apt install Python 3.12, Redis, Caddy, git, sqlite3, zstd, jq
#   3. git clone github.com/Aerosend-Public/aerocrawl -> /opt/aerocrawl
#   4. Build /opt/aerocrawl/.env (random API key + admin key, plus all optional vars)
#   5. python3.12 -m venv + pip install
#   6. playwright install chromium --with-deps
#   7. Install systemd units + nightly backup timer
#   8. Write Caddyfile, substituting AEROCRAWL_DOMAIN
#   9. systemctl enable --now aerocrawl aerocrawl-worker caddy
#   10. Poll https://$AEROCRAWL_DOMAIN/health for 30s; abort + journalctl on failure
#   11. Print success banner with the generated keys

set -euo pipefail

REPO_URL="https://github.com/Aerosend-Public/aerocrawl.git"
INSTALL_DIR="/opt/aerocrawl"
VERSION="3.1.0"

log() { echo -e "\e[1;34m[aerocrawl]\e[0m $*"; }
err() { echo -e "\e[1;31m[aerocrawl]\e[0m $*" >&2; }
die() { err "$*"; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "Must run as root (bootstrap uses apt and systemd)."
}

require_env() {
    local name="$1"
    [ -n "${!name:-}" ] || die "Required env var $name not set. See install/bootstrap.sh header."
}

preflight() {
    log "Pre-flight checks..."

    # OS
    if ! grep -q "^ID=ubuntu" /etc/os-release; then
        die "Ubuntu required (detected $(. /etc/os-release; echo $ID $VERSION_ID))"
    fi
    local ubuntu_major
    ubuntu_major=$(. /etc/os-release; echo "${VERSION_ID%%.*}")
    [ "$ubuntu_major" -ge 22 ] || die "Ubuntu 22.04+ required (found $ubuntu_major.x)"

    # RAM (MB)
    local ram_mb
    ram_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
    [ "$ram_mb" -ge 1800 ] || die "2+ GB RAM required (found ${ram_mb}MB)"

    # Disk (GB free on /)
    local disk_free
    disk_free=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
    [ "$disk_free" -ge 10 ] || die "10+ GB disk free required (found ${disk_free}GB)"

    log "Pre-flight OK: Ubuntu $ubuntu_major.x, ${ram_mb}MB RAM, ${disk_free}GB free."
}

install_packages() {
    log "Installing system packages..."
    export DEBIAN_FRONTEND=noninteractive

    # Python 3.12 from deadsnakes (not in default Ubuntu 22.04)
    if ! command -v python3.12 >/dev/null 2>&1; then
        add-apt-repository -y ppa:deadsnakes/ppa
    fi

    # Caddy official repo
    if ! command -v caddy >/dev/null 2>&1; then
        apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
            gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
            tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    fi

    apt update
    apt install -y \
        python3.12 python3.12-venv python3.12-dev \
        git redis-server caddy curl jq sqlite3 zstd \
        build-essential

    systemctl enable --now redis-server
}

clone_or_update_repo() {
    log "Cloning/updating $REPO_URL -> $INSTALL_DIR..."
    if [ -d "$INSTALL_DIR/.git" ]; then
        (cd "$INSTALL_DIR" && git fetch origin && git reset --hard origin/main)
    else
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
}

generate_api_key() {
    # 40-char alphanumeric + prefix
    local prefix="$1"
    local body
    body=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 40)
    echo "${prefix}_${body}"
}

build_env_file() {
    log "Building $INSTALL_DIR/.env..."

    local env_file="$INSTALL_DIR/.env"
    if [ -f "$env_file" ]; then
        log ".env already exists — preserving existing values, only adding missing ones."
    else
        touch "$env_file"
    fi

    upsert_env() {
        local key="$1"; local value="$2"
        if [ -z "$value" ]; then return; fi
        if grep -q "^${key}=" "$env_file"; then
            return  # preserve existing
        fi
        echo "${key}=${value}" >> "$env_file"
    }

    # Core (always required)
    upsert_env "AEROCRAWL_DOMAIN" "$AEROCRAWL_DOMAIN"
    upsert_env "ADMIN_EMAIL" "$ADMIN_EMAIL"
    upsert_env "DATABASE_URL" "sqlite+aiosqlite:////opt/aerocrawl/data/aerocrawl.db"
    upsert_env "REDIS_URL" "redis://localhost:6379/0"

    # Generated only if not already present
    if ! grep -q "^DEFAULT_API_KEY=" "$env_file"; then
        echo "DEFAULT_API_KEY=$(generate_api_key ac_live)" >> "$env_file"
    fi
    if ! grep -q "^ADMIN_API_KEY=" "$env_file"; then
        echo "ADMIN_API_KEY=$(generate_api_key ac_admin)" >> "$env_file"
    fi

    # Optional tier keys
    upsert_env "GEMINI_API_KEY" "${GEMINI_API_KEY:-}"
    upsert_env "REDDIT_PROXY_URL" "${REDDIT_PROXY_URL:-}"
    upsert_env "CF_PROXY_URL" "${CF_PROXY_URL:-}"
    upsert_env "PROXY_URL" "${PROXY_URL:-}"
    upsert_env "TAVILY_API_KEY" "${TAVILY_API_KEY:-}"
    upsert_env "ZYTE_API_KEY" "${ZYTE_API_KEY:-}"

    chmod 600 "$env_file"
    mkdir -p "$INSTALL_DIR/data"
}

install_python_deps() {
    log "Installing Python dependencies..."
    cd "$INSTALL_DIR"
    if [ ! -d venv ]; then
        python3.12 -m venv venv
    fi
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
}

install_playwright() {
    log "Installing Playwright Chromium (may take 2-3 min)..."
    cd "$INSTALL_DIR"
    venv/bin/playwright install chromium --with-deps
}

install_systemd_units() {
    log "Installing systemd units..."
    install -m 644 "$INSTALL_DIR/deploy/aerocrawl.service" /etc/systemd/system/
    install -m 644 "$INSTALL_DIR/deploy/aerocrawl-worker.service" /etc/systemd/system/
    if [ -f "$INSTALL_DIR/deploy/aerocrawl-backup.service" ]; then
        install -m 644 "$INSTALL_DIR/deploy/aerocrawl-backup.service" /etc/systemd/system/
    fi
    if [ -f "$INSTALL_DIR/deploy/aerocrawl-backup.timer" ]; then
        install -m 644 "$INSTALL_DIR/deploy/aerocrawl-backup.timer" /etc/systemd/system/
    fi
    systemctl daemon-reload
}

write_caddyfile() {
    log "Writing Caddyfile..."
    export AEROCRAWL_DOMAIN
    envsubst < "$INSTALL_DIR/deploy/Caddyfile.template" > /etc/caddy/Caddyfile
    mkdir -p /var/log/caddy
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
}

start_services() {
    log "Starting services..."
    systemctl enable --now aerocrawl aerocrawl-worker caddy
    if systemctl list-unit-files | grep -q aerocrawl-backup.timer; then
        systemctl enable --now aerocrawl-backup.timer
    fi
}

health_poll() {
    log "Polling https://$AEROCRAWL_DOMAIN/health (up to 30s)..."
    local start
    start=$(date +%s)
    local deadline=$((start + 30))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -sSfk "https://$AEROCRAWL_DOMAIN/health" >/dev/null 2>&1; then
            log "Healthy."
            return 0
        fi
        sleep 2
    done

    err "Health check failed after 30s."
    err "journalctl -u aerocrawl -n 40:"
    journalctl -u aerocrawl -n 40 --no-pager || true
    err "journalctl -u caddy -n 20:"
    journalctl -u caddy -n 20 --no-pager || true
    return 1
}

maybe_ping_telemetry() {
    [ "${ANONYMOUS_INSTALL_PING:-0}" = "1" ] || return 0
    local tiers_active
    tiers_active=$(venv/bin/python install/check-capabilities.py --json 2>/dev/null | \
        python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for r in d if r["active"]))') || tiers_active="?"
    curl -sS -X POST https://aerosend.io/api/aerocrawl/install-ping \
        -H 'Content-Type: application/json' \
        -d "{\"version\":\"$VERSION\",\"tiers_active\":\"$tiers_active\"}" >/dev/null 2>&1 || true
}

print_banner() {
    local user_key admin_key
    user_key=$(grep "^DEFAULT_API_KEY=" "$INSTALL_DIR/.env" | cut -d= -f2)
    admin_key=$(grep "^ADMIN_API_KEY=" "$INSTALL_DIR/.env" | cut -d= -f2)
    cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ Aerocrawl is live at https://$AEROCRAWL_DOMAIN
  Built by Aerosend — cold-email deliverability that works.
  Your scraper talks to websites. Ours talks to inboxes.

  Claim your free Aerosend inboxes (book a 15-min call):
  → https://meetings.hubspot.com/namit4/aerocrawl-free-inboxes
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

API base URL:  https://$AEROCRAWL_DOMAIN
User API key:  $user_key
Admin key:     $admin_key

SAVE THESE NOW — they are not shown again.

Smoke test:
    curl -H 'Authorization: Bearer $user_key' \\
         -H 'Content-Type: application/json' \\
         https://$AEROCRAWL_DOMAIN/scrape \\
         -d '{"url":"https://github.com"}'

Upgrade tiers any time:
    cd $INSTALL_DIR && venv/bin/python install/check-capabilities.py

EOF
}

main() {
    require_root
    require_env AEROCRAWL_DOMAIN
    require_env ADMIN_EMAIL
    preflight
    install_packages
    clone_or_update_repo
    build_env_file
    install_python_deps
    install_playwright
    install_systemd_units
    write_caddyfile
    start_services
    cd "$INSTALL_DIR"
    health_poll || die "Aerocrawl failed to come up healthy. See journalctl output above."
    maybe_ping_telemetry
    print_banner
}

main "$@"
