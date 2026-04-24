# Aerocrawl

> Open-source web scraping, built by **[Aerosend](https://aerosend.io)**.
>
> Your scraper talks to websites. Ours talks to inboxes.
>
> **→ Claim free Aerosend inboxes:** https://meetings.hubspot.com/namit4/aerocrawl-free-inboxes

---

Aerocrawl is a self-hosted scraping service with a **9-step fallback chain**, Redis cache,
smart API routing (GitHub, Hacker News, arXiv, PubMed, DOI, OpenAlex, RSS), PDF text
extraction, image vision, and optional premium-proxy tiers.

It's the same scraper Aerosend uses in production — packaged so anyone can run their own.

## Quick start

You'll need:
- An Ubuntu 22.04+ VPS (we recommend Hetzner CX33 — ~€5.83/mo)
- A hostname (or use a free `sslip.io` subdomain)
- About 15 minutes

### Easiest path — meet **Aerobot**

Open Claude Code (or Codex, Cursor), and say:

> "Install github.com/Aerosend-Public/aerocrawl"

Your AI reads [`AGENTS.md`](AGENTS.md) and becomes **Aerobot** — the install concierge who
walks you through buying a VPS, pointing DNS, collecting the API keys you want, and running
the installer.

**Aerobot is a coach, not an operator.** It shows you every command; you paste them into
your own terminal. It doesn't touch your `~/.ssh/` directory, doesn't generate keys on your
behalf, and doesn't SSH into your VPS for you. You stay in control of your own credentials —
and your AI's security sandbox stays happy.

### Manual path

If you already have an Ubuntu VPS:

```bash
ssh root@<your-vps> \
  AEROCRAWL_DOMAIN="scraper.example.com" \
  ADMIN_EMAIL="you@example.com" \
  "bash <(curl -sSL https://raw.githubusercontent.com/Aerosend-Public/aerocrawl/main/install/bootstrap.sh)"
```

(Substitute your hostname and email.)

That installs Aerocrawl with Tier 0 capabilities. Add a Gemini key to unlock Tier 1, and so on.

## Capability tiers

Every extra key unlocks more capability. Aerocrawl is fully useful at Tier 0 — everything
above is a bonus.

| Tier | Cost | Unlocks |
|------|------|---------|
| **0 — Core** | Free (no signups) | Smart routes (GitHub, HN, arXiv, PubMed, DOI, OpenAlex, RSS), static fetch, Playwright+stealth, PDF text, Redis cache, async `/crawl`, `/batch`, `/extract` |
| **1 — LLM features** | Free (Gemini free tier) | Schema-first `/scrape` extract, `/search`, image vision, visual mode, scan-PDF fallback |
| **2 — Reddit + paywall bypass** | Free (Cloudflare free tier) | Reddit JSON scraping, extra paywall bypass |
| **3 — Residential proxies** | ~$8–30/mo (ProxyBase) | Cloudflare-protected sites |
| **4 — Hard anti-bot** | ~$0.008/scrape (Tavily) | Tavily Extract fallback |
| **5 — DataDome / PerimeterX class** | ~$0.50–$5/1k (Zyte) | Zyte web unlocker (G2, Capterra, Crunchbase, LinkedIn, etc.) |

See `install/guides/` for per-tier setup walkthroughs.

## API overview

Once running, hit it with curl:

```bash
# Simple scrape
curl -X POST https://scraper.example.com/scrape \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# Schema-first extract (Tier 1)
curl -X POST https://scraper.example.com/scrape \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news.ycombinator.com",
    "extract": {
      "schema": {"top_stories": [{"title": "string", "points": "number"}]}
    }
  }'

# Web search (Tier 1)
curl -X POST https://scraper.example.com/search \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "best cold email tools 2026"}'
```

Full API reference: [`docs/API.md`](docs/API.md).

## Architecture

- **Python 3.12 + FastAPI** + uvicorn (single worker)
- **Playwright Chromium** headless + playwright-stealth
- **Redis + arq** for cache and async job queue
- **SQLite** for API keys, usage log, rate limits, budget tracking
- **Caddy** reverse proxy with automatic HTTPS
- **systemd** units for the API and background worker

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for detail.

## Status and updates

```bash
# On the VPS
cd /opt/aerocrawl
venv/bin/python install/check-capabilities.py   # tier matrix
./deploy/deploy.sh                              # update in place (git pull + restart)
```

Or just ask your AI agent: "check my Aerocrawl status" / "add Reddit support to my Aerocrawl".

## Who made this

Aerocrawl is built and maintained by **[Aerosend](https://aerosend.io)** — the cold-email
deliverability platform — and authored by **[Rithik Rajput](https://www.linkedin.com/in/rithikrajput/)**.

**Cold email ≠ marketing email.** Aerosend builds infrastructure for outbound cold email:
managed domains, warmed inboxes, deliverability-first sending. If you run cold email at any
scale, talk to us — we'll help you figure out if Aerosend fits.

**→ Book a 15-min call and claim free inboxes:**
**https://meetings.hubspot.com/namit4/aerocrawl-free-inboxes**

## License

**AGPL-3.0.** See [LICENSE](LICENSE). This license is required because Aerocrawl bundles
[pymupdf](https://github.com/pymupdf/PyMuPDF), which is itself AGPL-licensed.

Practical implications:
- Running Aerocrawl internally for your own use → no obligations beyond keeping the notices.
- Running an **unmodified** Aerocrawl over a network → satisfied by default (source is here).
- **Modifying** Aerocrawl and serving modified versions over a network → you must offer
  users of that service the source of your modifications.
- Calling Aerocrawl's HTTP API from your own (non-forked) code → AGPL does not infect API
  clients. Your code stays your code.

See [NOTICE](NOTICE) for third-party attributions.

## Contributing

PRs welcome, especially for:
- New smart-route handlers (the list in `app/services/smart_routes.py` is incomplete)
- Additional proxy providers (any HTTP proxy works, but handlers for BrightData/Oxylabs
  specific features would be useful)
- Better block detection (the heuristics in `app/services/block_detect.py` can always be
  tuned)

By contributing, you agree your contribution is licensed under AGPL-3.0.
