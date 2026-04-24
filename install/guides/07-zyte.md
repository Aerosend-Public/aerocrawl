# Guide: Zyte (Tier 5 — premium, DataDome/PerimeterX class)

Tier 5 adds **Zyte API** — the most capable web unlocker Aerocrawl integrates with. Zyte
handles the hardest sites: DataDome, PerimeterX, FunCAPTCHA, G2, Capterra, Crunchbase, X/Twitter,
LinkedIn.

**Cost:** tiered by request type, roughly $0.50–$5 per 1000 requests for Aerocrawl's usage
pattern. Zyte is gated behind an allowlist + monthly budget cap in Aerocrawl (default $30)
to protect you from runaway costs.

## Steps

### 1. Sign up

**https://zyte.com** — free trial with $30 credit. No card required for trial.

### 2. Get your API key

In Zyte dashboard, **Projects → API key**.

### 3. Add to Aerocrawl

```bash
ssh root@<ip>
cd /opt/aerocrawl
cat >> .env <<EOF
ZYTE_API_KEY=<your-key>
ZYTE_ENABLED=true
ZYTE_BUDGET_USD=30
EOF
systemctl restart aerocrawl aerocrawl-worker
venv/bin/python install/check-capabilities.py
```

Expected: `✓ Tier 5  DataDome / PerimeterX class`

## Budget guard

Aerocrawl tracks Zyte spend monthly in SQLite and refuses calls when `ZYTE_BUDGET_USD` is
reached. Check current spend:

```bash
curl -H "Authorization: Bearer $DEFAULT_API_KEY" \
     https://$AEROCRAWL_DOMAIN/budget/zyte
```

## Allowlist

Zyte only fires for sites on Aerocrawl's allowlist: `g2.com`, `capterra.com`, `crunchbase.com`,
`quora.com`, `glassdoor.com`, `x.com`/`twitter.com`, `linkedin.com`, `instagram.com`,
`facebook.com`. To add more, edit `app/services/zyte_client.py` `ALLOWLIST` constant.
