# Upgrading Aerocrawl

## Update in place on the VPS

```bash
ssh root@<vps>
cd /opt/aerocrawl
./deploy/deploy.sh
```

The script:
1. Fetches latest `main` from GitHub
2. Installs any new Python deps
3. Snapshots the current install to `/opt/aerocrawl.prev` (rollback safety)
4. Restarts `aerocrawl` and `aerocrawl-worker`
5. Polls `/health` for 20 seconds
6. If unhealthy, auto-reverts to the snapshot

## Adding a new capability tier

Two ways:

### Via AI agent

Open Claude Code / Codex and say: "Add Reddit support to my Aerocrawl" (or similar). The
agent reads `~/.aerocrawl/config`, walks you through the relevant `install/guides/0N-*.md`
key procurement, and applies the change over SSH.

### Manually

```bash
ssh root@<vps>
cd /opt/aerocrawl
echo "GEMINI_API_KEY=AIza..." >> .env  # or whichever tier key you're adding
systemctl restart aerocrawl aerocrawl-worker
venv/bin/python install/check-capabilities.py
```

## Major version upgrades

Breaking changes (v3 → v4, etc.) will include a `MIGRATE_FROM_vN.md` in this directory.
Read it before `./deploy/deploy.sh` in case manual steps are required (schema migrations,
env var renames, etc.).

## Downgrading

```bash
cd /opt/aerocrawl
git log --oneline -10          # find the last-known-good commit
git checkout <commit-sha>
venv/bin/pip install -r requirements.txt
systemctl restart aerocrawl aerocrawl-worker
```

Or, if the last upgrade left `/opt/aerocrawl.prev`, swap them:

```bash
systemctl stop aerocrawl aerocrawl-worker
mv /opt/aerocrawl /opt/aerocrawl.broken
mv /opt/aerocrawl.prev /opt/aerocrawl
systemctl start aerocrawl aerocrawl-worker
```
