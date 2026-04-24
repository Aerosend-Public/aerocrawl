# Aerocrawl Architecture

This doc describes how Aerocrawl is structured and how requests flow through it.

## Runtime

```
             ┌─────────────┐
  internet → │    Caddy    │ ← Let's Encrypt, HTTPS termination
             └──────┬──────┘
                    │ localhost:8001
             ┌──────┴──────┐
             │   FastAPI   │ ← auth, tier gating, rate limiting
             └──────┬──────┘
                    │
     ┌──────────────┼──────────────┐
     │              │              │
┌────┴────┐   ┌─────┴─────┐   ┌────┴────┐
│  Redis  │   │  SQLite   │   │ arq     │
│  cache  │   │  (api     │   │ worker  │
│  +      │   │   keys,   │   │ (crawl, │
│  rate   │   │   usage,  │   │  batch, │
│  limit  │   │   budget) │   │  extract│
└─────────┘   └───────────┘   │   async)│
                              └─────────┘
```

## Fallback chain (POST /scrape)

```
-1. Redis cache lookup (msgpack + zstd, 24h TTL)
 0. Smart route dispatch (github | hackernews | academic | rss → API, richer than HTML)
 0.5. PDF detection → pymupdf → Gemini (Tier 1) fallback for scan PDFs
 1. Reddit? → CF Reddit Worker (Tier 2, instant, free)
 2. Static httpx fetch (non-JS sites)
 3. Playwright + stealth (no proxy)
 4-5. Playwright + stealth + ProxyBase (Tier 3, twice)
 6. CF General Worker (Tier 2)
 7. Tavily (Tier 4)
 8. Zyte (Tier 5, domain-allowlisted + budget-gated)
→ All fail? Returns `success: false` with `block_type` + `methods_tried`.
```

Per-domain memoization biases the chain order based on what worked previously — if ProxyBase
consistently beats other methods for `example.com`, it tries ProxyBase first.

## Capability tier gating

Runtime tier checks happen in `app/services/tier_gate.py`. Each feature is mapped to a
minimum tier in `capabilities.yaml` under `feature_tiers`. When an endpoint needs a feature,
it calls `get_tier_gate().check_feature("foo")`:

- If the required env vars are set → proceeds.
- If not → raises `TierLockedError`, which the endpoint handler converts to a 402 response
  pointing the caller at the relevant unlock guide.

This keeps tier logic in one place and makes adding new tiers a YAML edit.

## Persistence

- **Redis:** scrape-result cache (24h default), rate-limit sliding windows, arq job queue.
  Flush-safe — Aerocrawl degrades gracefully if Redis is down.
- **SQLite:** API keys, usage log, per-key rate-limit overrides, monthly budget tracking,
  per-domain strategy memoization. Backed up nightly to `data/backups/` via systemd timer.

## Systemd units

- `aerocrawl.service` — uvicorn, FastAPI
- `aerocrawl-worker.service` — arq worker, processes /crawl, /batch, /extract async jobs
- `aerocrawl-backup.timer` → `aerocrawl-backup.service` — nightly SQLite backup (zstd)

## Security

- API keys required on all endpoints except `/health`.
- SSRF protection in `app/validation.py` — rejects localhost, private RFC1918, link-local.
- Zyte budget guard enforced atomically (INSERT with conditional) to prevent TOCTOU races.
- No outbound calls to hosts outside the request-specified URL (plus the API providers named
  in `capabilities.yaml`).
