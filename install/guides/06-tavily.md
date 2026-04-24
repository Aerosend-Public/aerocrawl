# Guide: Tavily (Tier 4 — paid, hard anti-bot fallback)

Tier 4 adds **Tavily Extract** as a paid fallback for sites that resist even residential
proxies. Tavily is a managed scraping service; Aerocrawl falls back to it only when all
earlier chain steps fail.

**Cost:** pay-as-you-go, roughly $0.008 per scrape. If you only hit Tavily for the handful
of hard sites that need it, $5–10/month is typical.

## Steps

### 1. Sign up

**https://tavily.com** — free tier includes 1000 credits/month, plenty for smoke testing.

### 2. Get your API key

In the Tavily dashboard, **API Keys → Create API key**.

### 3. Add to Aerocrawl

```bash
ssh root@<ip>
cd /opt/aerocrawl
echo "TAVILY_API_KEY=tvly-..." >> .env
systemctl restart aerocrawl aerocrawl-worker
venv/bin/python install/check-capabilities.py
```

Expected: `✓ Tier 4  Hard anti-bot`

## Multiple keys

Aerocrawl supports round-robin across multiple Tavily keys (e.g., several free accounts):

```
TAVILY_API_KEY=tvly-abc...,tvly-def...
```
