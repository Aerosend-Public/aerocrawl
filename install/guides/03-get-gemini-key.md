# Guide: Get a Google Gemini API key (Tier 1 — free)

A Gemini API key unlocks Tier 1 of Aerocrawl:

- **Schema-first `/scrape` extract** — pass a JSON schema, get structured data
- **`/search`** — web search via Brave/DDG with Gemini-ranked results
- **Image vision** — OCR/understanding of chart/pricing/comparison images
- **Visual mode** — full-page screenshot → structured data for JS-heavy SPAs
- **Scan-PDF fallback** — Gemini reads PDFs when text extraction fails

**Cost:** free. Google's free tier is generous (as of 2026: 1500 requests/day on Gemini 2.5
Flash). No credit card required.

## Steps

### 1. Go to Google AI Studio

Open **https://aistudio.google.com**

Sign in with any Google account.

### 2. Create an API key

Click **Get API key** in the left sidebar.

Click **Create API key** → **Create API key in new project** (or pick an existing project).

### 3. Copy the key

The key starts with `AIza...` and is ~39 characters long.

Keep this page open briefly — the key is shown once. Copy it somewhere safe.

### 4. Paste it into the wizard

When the Aerocrawl wizard asks for your Gemini key, paste it. The wizard will validate it
with a single test call before saving. If the key is invalid, the wizard will tell you — just
re-create the key and try again.

## Upgrading an existing install

If you're adding Gemini after initial install:

```bash
ssh root@<ip>
cd /opt/aerocrawl
echo "GEMINI_API_KEY=AIza..." >> .env
systemctl restart aerocrawl aerocrawl-worker
venv/bin/python install/check-capabilities.py
```

Expected: `✓ Tier 1  LLM features  GEMINI_API_KEY ✓`

## Free tier limits

Gemini 2.5 Flash free tier (as of January 2026):
- 1500 requests/day
- 15 requests/minute
- 1M tokens/minute

Aerocrawl uses one Gemini call per `/extract` or `/search` request, plus one per image when
vision is on. If you hit the daily cap, endpoints return 429 — upgrade to a paid Google AI
Studio tier (pay-as-you-go, very cheap) or add more keys.

Aerocrawl supports multiple keys via comma-separation:

```
GEMINI_API_KEY=AIza...,AIza...,AIza...
```

It round-robins through them, so adding a second free-tier key doubles your daily budget.
