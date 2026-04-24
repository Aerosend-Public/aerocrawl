#!/usr/bin/env bash
# install/provision-hetzner.sh
# Provision a Hetzner Cloud CX33 for Aerocrawl via API.
#
# Env vars:
#   HCLOUD_TOKEN           required
#   AEROCRAWL_REGION       optional (fsn1|ash|hil); auto-guesses from tz otherwise
#   AEROCRAWL_SERVER_NAME  optional; default "aerocrawl-$(date +%s)"
#
# Prints the new server IP on stdout. Stderr carries progress logs.

set -euo pipefail

HCLOUD_TOKEN="${HCLOUD_TOKEN:-}"
SERVER_TYPE="cx33"
IMAGE="ubuntu-22.04"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519.pub}"
SERVER_NAME="${AEROCRAWL_SERVER_NAME:-aerocrawl-$(date +%s)}"

log()  { echo -e "\e[1;34m[provision]\e[0m $*" >&2; }
die()  { echo -e "\e[1;31m[provision]\e[0m $*" >&2; exit 1; }

api() {
    local method="$1"; local endpoint="$2"; shift 2
    curl -sS -X "$method" \
        -H "Authorization: Bearer $HCLOUD_TOKEN" \
        -H "Content-Type: application/json" \
        "https://api.hetzner.cloud/v1$endpoint" "$@"
}

guess_region() {
    case "$(date +%Z)" in
        EST|EDT|CST|CDT|PST|PDT|MST|MDT) echo "ash" ;;  # US East — Ashburn
        *) echo "fsn1" ;;                                 # default EU — Nuremberg
    esac
}

main() {
    [ -n "$HCLOUD_TOKEN" ] || die "HCLOUD_TOKEN not set. Create one at https://console.hetzner.cloud"
    [ -f "$SSH_KEY_PATH" ] || die "SSH public key not found at $SSH_KEY_PATH (generate with ssh-keygen)"

    local region="${AEROCRAWL_REGION:-$(guess_region)}"
    log "Region: $region"

    # Upload SSH key (idempotent — check if exists by fingerprint)
    local pubkey_body key_id
    pubkey_body=$(cat "$SSH_KEY_PATH")
    log "Uploading SSH key..."
    key_id=$(api POST /ssh_keys -d "{\"name\":\"$SERVER_NAME-key\",\"public_key\":$(jq -Rs . <<< "$pubkey_body")}" \
        | jq -r '.ssh_key.id // empty')

    if [ -z "$key_id" ]; then
        # Key already uploaded — look it up by public_key value
        key_id=$(api GET /ssh_keys \
            | jq --arg pk "$pubkey_body" '.ssh_keys[] | select(.public_key==$pk) | .id' \
            | head -1)
    fi
    [ -n "$key_id" ] || die "Could not upload or find SSH key"

    # Create firewall (idempotent by name)
    log "Setting up firewall..."
    local fw_id
    fw_id=$(api GET "/firewalls?name=aerocrawl-fw" | jq -r '.firewalls[0].id // empty')
    if [ -z "$fw_id" ]; then
        fw_id=$(api POST /firewalls -d '{
          "name": "aerocrawl-fw",
          "rules": [
            {"direction":"in","protocol":"tcp","port":"22","source_ips":["0.0.0.0/0","::/0"]},
            {"direction":"in","protocol":"tcp","port":"80","source_ips":["0.0.0.0/0","::/0"]},
            {"direction":"in","protocol":"tcp","port":"443","source_ips":["0.0.0.0/0","::/0"]}
          ]
        }' | jq -r '.firewall.id')
    fi
    [ -n "$fw_id" ] || die "Firewall setup failed"

    # Create server
    log "Creating CX33 $SERVER_NAME in $region..."
    local create_resp
    create_resp=$(api POST /servers -d "{
      \"name\": \"$SERVER_NAME\",
      \"server_type\": \"$SERVER_TYPE\",
      \"image\": \"$IMAGE\",
      \"location\": \"$region\",
      \"ssh_keys\": [$key_id],
      \"firewalls\": [{\"firewall\": $fw_id}],
      \"start_after_create\": true
    }")
    local server_id
    server_id=$(echo "$create_resp" | jq -r '.server.id // empty')
    [ -n "$server_id" ] || { echo "$create_resp" | jq . >&2; die "Server creation failed"; }

    log "Waiting for boot..."
    local ip=""
    for i in $(seq 1 30); do
        ip=$(api GET "/servers/$server_id" | jq -r '.server.public_net.ipv4.ip // empty')
        local status
        status=$(api GET "/servers/$server_id" | jq -r '.server.status // empty')
        if [ -n "$ip" ] && [ "$status" = "running" ]; then
            log "Server running at $ip"
            break
        fi
        sleep 3
    done
    [ -n "$ip" ] || die "Server did not come up in 90s"

    # Wait for SSH to be reachable
    log "Waiting for SSH..."
    for i in $(seq 1 20); do
        if nc -z -w2 "$ip" 22 2>/dev/null; then
            log "SSH ready."
            break
        fi
        sleep 3
    done

    # Print IP to stdout (caller consumes this)
    echo "$ip"
}

main "$@"
