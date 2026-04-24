# Aerocrawl V3.1 API Documentation

**Base URL:** `https://scraper.example.com` (replace with your domain)

**Auth:** All endpoints (except `/health`) require an API key via header:
```
Authorization: Bearer ac_live_xxxxx
```
or
```
X-API-Key: ac_live_xxxxx
```

Your API key is shown in the bootstrap banner when you first install Aerocrawl. You can also
find it with:

```bash
ssh root@<vps> "grep ^DEFAULT_API_KEY= /opt/aerocrawl/.env"
```

## What's new in V3.1

- **Redis result cache** (msgpack+zstd, 24h TTL). Same URL within 24h returns
  `cached: true` in ~200ms. Bypass with `"force_refresh": true`.
- **Smart API routing** — github.com, arxiv.org, pubmed, doi.org, openalex,
  news.ycombinator.com, RSS feeds auto-route to the site's API (much faster).
- **PDF extraction** — `.pdf` URLs auto-extracted via pymupdf + pdfplumber;
  scanned PDFs fall back to Gemini 2.5 Flash.
- **Image handling** — `formats: ["images"]` returns `{src, alt, caption}` triples.
  `vision: {describe_images: true}` auto-describes charts with Gemini Vision.
  `vision: {mode: "visual"}` sends full-page screenshot to Gemini instead of
  parsing HTML (useful for JS-rendered pricing tables).
- **Schema-first extract** — add `extract: {schema, prompt}` to `/scrape` for
  inline Gemini-validated JSON extraction. Firecrawl v2 /extract equivalent.
- **Zyte web unlocker** — replaces Tavily at step 9. Allowlisted (G2, Capterra,
  Crunchbase, Quora, etc.), $30/mo hard budget cap.
- **Per-key rate limit** — 60/min + 1000/hr (admin keys bypass). HTTP 429 on overage.
- **Per-domain memoization** — remembers which method wins per site, reorders chain.
- **New admin endpoints** — `/cache/stats`, `DELETE /cache`, `DELETE /cache/purge-all`,
  `/budget/zyte`, `/strategy`, `/strategy/probe`, `/route-info`.
- **Reliability** — nightly SQLite backup (systemd timer), deploy rollback with
  health gate, atomic Zyte budget reservation (no TOCTOU race), secrets
  redacted in error logs.
- **Speed** — default `wait_for` is `domcontentloaded` (was `networkidle`,
  3-5s faster), HTTP/2 connection pool, warm browser pool at startup,
  static fetch timeout 15s → 8s.

---

## API Keys

The bootstrap script generates two keys:
- **`DEFAULT_API_KEY`** (`ac_live_...`) — regular user key
- **`ADMIN_API_KEY`** (`ac_admin_...`) — admin key (can create/revoke keys)

Manage keys via the `/keys` admin endpoints.

---

## Endpoints

### GET /health

No auth required. Returns service status.

```bash
curl https://scraper.example.com/health
```

```json
{
  "status": "ok",
  "service": "aerocrawl",
  "version": "3.1.0",
  "tiers_active": "2/6",
  "message": "Aerocrawl — built by Aerosend. Claim your free Aerosend inboxes: https://meetings.hubspot.com/namit4/aerocrawl-free-inboxes",
  "browser_pool": {"max_contexts": 5, "active_contexts": 0, "queued_requests": 0},
  "redis": {"status": "connected", "pending_jobs": 0}
}
```

---

### POST /scrape

Scrape a single URL. Uses a 9-step fallback chain with anti-detection, residential proxies,
Cloudflare Workers, Tavily, and Zyte to maximize success rate.

```bash
curl -X POST https://scraper.example.com/scrape \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "formats": ["markdown", "metadata", "links"],
    "only_main_content": true
  }'
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | URL to scrape (http/https only) |
| `formats` | string[] | `["markdown"]` | Output: `markdown`, `html`, `screenshot`, `links`, `metadata`, `images` |
| `proxy` | string | `""` | `"proxybase"` or raw proxy URL |
| `wait_for` | string | `"domcontentloaded"` | Playwright wait: `networkidle`, `load`, `domcontentloaded` |
| `timeout_ms` | int | `20000` | Per-attempt timeout |
| `selector` | string | `""` | CSS selector to extract specific element |
| `only_main_content` | bool | `true` | Strip nav/footer/sidebar |
| `actions` | object[] | `[]` | Browser actions before scraping |
| `force_refresh` | bool | `false` | **V3** — bypass cache, always refetch |
| `vision` | object | `null` | **V3** — `{describe_images: bool, mode: "visual"}` |
| `extract` | object | `null` | **V3.1** — `{schema, prompt}` → Gemini validated JSON |

**Response:**

```json
{
  "success": true,
  "url": "https://example.com",
  "scrape_method": "httpx",
  "duration_ms": 450,
  "markdown": "# Example Domain\n\nThis domain is for...",
  "metadata": {
    "title": "Example Domain",
    "description": "",
    "language": "en",
    "status_code": 200,
    "source_url": "https://example.com"
  },
  "links": ["https://www.iana.org/domains/example"]
}
```

**Scrape methods:**

| Method | Description |
|--------|-------------|
| `static` | Fast httpx fetch, no browser (static pages) |
| `playwright+stealth` | Playwright with anti-detection patches |
| `playwright+stealth+proxybase` | Stealth + ProxyBase residential proxy |
| `cf_worker_reddit` | Reddit-specific Cloudflare Worker proxy |
| `cf_proxy` | General Cloudflare Worker proxy |
| `tavily` | Tavily Extract API fallback |
| `zyte` | Zyte web unlocker (budget-gated, allowlisted) |

**V3.1 Fallback Chain:**
```
-1. Redis cache lookup (msgpack+zstd, 24h TTL; bypass with force_refresh=true)
 0. Smart route dispatch → github/hackernews/academic/rss
 0.5. PDF detection (URL ext + magic bytes) → pymupdf → Gemini fallback for scans
 1. Reddit? → CF Worker Reddit (free)
 2. Static httpx fetch (non-JS sites, 8s timeout)
 3. Playwright+stealth (no proxy)
 4-5. Playwright+stealth + ProxyBase ×2
 6. CF General Proxy (Cloudflare edge)
 7. Tavily Extract (paid fallback)
 8. Zyte API (domain-allowlist + budget-gated, 120s timeout)
→ All fail? Returns success=false with block_type + methods_tried
```

**Error response (when all methods fail):**

```json
{
  "success": false,
  "url": "https://www.g2.com/products/example/reviews",
  "scrape_method": "",
  "duration_ms": 45000,
  "error": "All scraping methods failed. Matched: Attention Required! | Cloudflare",
  "block_type": "cloudflare_challenge",
  "block_detail": "Matched: Attention Required! | Cloudflare",
  "methods_tried": [
    "playwright+stealth",
    "playwright+stealth+proxybase",
    "playwright+stealth+proxybase",
    "cf_proxy",
    "tavily",
    "zyte"
  ],
  "metadata": {}
}
```

**Block types:**

| `block_type` | Meaning |
|---|---|
| `cloudflare_challenge` | Cloudflare JS challenge / Turnstile |
| `captcha` | PerimeterX, DataDome, reCAPTCHA |
| `auth_wall` | Site requires login (LinkedIn, Instagram) |
| `rate_limited` | 429 Too Many Requests |
| `ip_blocked` | 403 / network policy block |
| `empty_content` | Page loaded but no meaningful content |

---

### POST /screenshot

Take a PNG screenshot of a URL. Always uses Playwright.

```bash
curl -X POST https://scraper.example.com/screenshot \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "full_page": true}' \
  --output screenshot.png
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | URL to screenshot |
| `proxy` | string | `""` | Proxy setting |
| `wait_for` | string | `"networkidle"` | Wait condition |
| `timeout_ms` | int | `30000` | Timeout |
| `full_page` | bool | `true` | Full scrollable page vs viewport only |
| `viewport` | object | `{"width": 1280, "height": 720}` | Viewport size |

**Response:** PNG image bytes (`Content-Type: image/png`)

---

### POST /search

Web search via Cloudflare Worker. Scrapes Brave Search HTML, falls back to DuckDuckGo HTML.
Free, unlimited, no API key needed (uses CF Worker proxy). Returns structured results
compatible with Brave API response shape.

Requires Tier 1 (GEMINI_API_KEY) for ranked results.

```bash
curl -X POST https://scraper.example.com/search \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "aerosend email deliverability",
    "count": 5
  }'
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Search query (min 1 char) |
| `count` | int | `10` | Max results to return (1-20) |

**Response:**

```json
{
  "success": true,
  "query": "aerosend email deliverability",
  "search_engine": "brave_html",
  "result_count": 5,
  "duration_ms": 638,
  "results": [
    {
      "title": "Email Deliverability Platform for Cold Emails | Aerosend",
      "url": "https://www.aerosend.io/",
      "description": "Aerosend helps you send high-deliverability emails..."
    }
  ]
}
```

`search_engine` is `"brave_html"` or `"duckduckgo_html"`. On failure, returns `"success": false`
with `"error"` field.

---

### POST /map

Discover all URLs on a site via sitemaps and link extraction. No browser needed — fast.

```bash
curl -X POST https://scraper.example.com/map \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.python.org", "max_urls": 100}'
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Site URL |
| `max_urls` | int | `500` | Maximum URLs to return |
| `include_paths` | string[] | `[]` | Regex filters — only include matching paths |
| `exclude_paths` | string[] | `[]` | Regex filters — exclude matching paths |

**Response:**

```json
{
  "success": true,
  "urls": ["https://www.python.org/", "https://www.python.org/about/", "..."],
  "total": 63,
  "sources": {"sitemap": 50, "robots_txt": 0, "page_links": 13}
}
```

---

### POST /crawl (async)

Recursively crawl a website. Returns a job ID — poll for results.

```bash
# Start crawl
curl -X POST https://scraper.example.com/crawl \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "max_pages": 50,
    "max_depth": 3,
    "formats": ["markdown", "metadata"]
  }'

# Response: {"success": true, "job_id": "crawl_abc123", "status": "queued"}

# Poll for results
curl https://scraper.example.com/crawl/crawl_abc123 \
  -H "Authorization: Bearer ac_live_xxxxx"

# Cancel
curl -X DELETE https://scraper.example.com/crawl/crawl_abc123 \
  -H "Authorization: Bearer ac_live_xxxxx"
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Starting URL |
| `max_pages` | int | `100` | Max pages to scrape (limit: 500) |
| `max_depth` | int | `3` | Max link depth from seed URL |
| `include_paths` | string[] | `[]` | Regex path filters |
| `exclude_paths` | string[] | `[]` | Regex path filters |
| `formats` | string[] | `["markdown"]` | Output formats per page |
| `only_main_content` | bool | `true` | Strip nav/footer |
| `proxy` | string | `""` | Proxy setting |
| `max_concurrency` | int | `3` | Concurrent scrapes (max 5) |

---

### POST /batch/scrape (async)

Scrape multiple URLs in parallel. Returns job ID.

```bash
curl -X POST https://scraper.example.com/batch/scrape \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://a.com", "https://b.com", "https://c.com"],
    "formats": ["markdown", "metadata"]
  }'

# Poll: GET /batch/{job_id}
```

**Limits:** Max 100 URLs per batch.

---

### POST /extract (async)

LLM-powered structured data extraction. Scrapes URL(s), sends to Gemini, returns structured JSON.
Requires Tier 1 (GEMINI_API_KEY).

```bash
curl -X POST https://scraper.example.com/extract \
  -H "Authorization: Bearer ac_live_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://example.com/pricing"],
    "schema": {
      "type": "object",
      "properties": {
        "plans": {
          "type": "array",
          "items": {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "string"}}}
        }
      }
    },
    "prompt": "Extract all pricing plans"
  }'

# Poll: GET /extract/{job_id}
```

**Limits:** Max 10 URLs per extract. Uses Gemini 2.5 Flash.

---

### Admin Endpoints

#### POST /keys (admin only)
```bash
curl -X POST https://scraper.example.com/keys \
  -H "Authorization: Bearer ac_admin_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{"name": "Team Key", "team_member": "Alice"}'
```

#### GET /keys (admin only)
```bash
curl https://scraper.example.com/keys \
  -H "Authorization: Bearer ac_admin_xxxxx"
```

#### DELETE /keys/{key_id} (admin only)
```bash
curl -X DELETE https://scraper.example.com/keys/{key_id} \
  -H "Authorization: Bearer ac_admin_xxxxx"
```

#### GET /usage
```bash
curl https://scraper.example.com/usage \
  -H "Authorization: Bearer ac_live_xxxxx"
```

---

## V3.1 Endpoints

### Cache management

```bash
# Hit/miss counters (O(1))
GET /cache/stats -H "Authorization: Bearer ac_live_xxxxx"

# Purge one URL + all format variants
DELETE /cache?url=https://example.com -H "Authorization: Bearer ac_admin_xxxxx"

# Mass-purge cache namespace (admin)
DELETE /cache/purge-all -H "Authorization: Bearer ac_admin_xxxxx"
```

### Budget / Zyte

```bash
# Current-month Zyte spend vs cap + allowlist
GET /budget/zyte -H "Authorization: Bearer ac_live_xxxxx"
```

Returns:
```json
{"provider": "zyte", "ym": "2026-04", "calls": 1, "successes": 1,
 "failures": 0, "spent_usd": 0.01, "avg_cost_usd": 0.01,
 "cap_usd": 30.0, "remaining_usd": 29.99, "at_cap": false,
 "allowlist": ["g2.com", "capterra.com", "crunchbase.com", "quora.com", "glassdoor.com"]}
```

### Per-domain strategy

```bash
# Per-domain memoized method + success/failure counts (last 500 domains)
GET /strategy -H "Authorization: Bearer ac_live_xxxxx"

# Run 7-canary chain health probe (admin only, ~60s)
POST /strategy/probe -H "Authorization: Bearer ac_admin_xxxxx"
```

### Route info (debug)

```bash
# Which smart-route handler (if any) would fire for this URL?
GET /route-info?url=https://github.com/anthropics/claude-code -H "Authorization: Bearer ac_live_xxxxx"
```

Returns `{"matched_route": "github", "will_use_route": true, "registry": [...]}`.

---

## Response fields (V3/V3.1 additions)

| Field | Type | When present | Description |
|---|---|---|---|
| `cached` | bool | always | Whether this result came from Redis cache |
| `cache_age_seconds` | int | when `cached=true` | How old the cached copy is |
| `content_type` | string | PDFs and explicit non-HTML | Response MIME |
| `pages` | int | PDFs | Page count from pymupdf |
| `images` | object[] | when `"images"` in `formats` | `{src, alt, caption, width, height, informational, description?}` |
| `extracted` | object/array | when `extract` param sent and Gemini succeeds | Validated JSON per schema |
| `extract_error` | string | when extract fails | Reason (schema mismatch, LLM 503, etc.) |

## Response headers (V3.1)

- `X-Cache: HIT | MISS | BYPASS` — cache state
- `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Window`, `Retry-After` — only on HTTP 429

## Rate limits

- **60 requests/minute** and **1000/hour** per API key (admin keys bypass)
- HTTP 429 on overage with the headers above

## Site Compatibility (V3.1)

### Works automatically
Smart routes (github, arxiv, pubmed, doi, openalex, HN, RSS), main chain (Wikipedia, docs,
most JS-heavy SaaS, Reddit, medium, Trustpilot, npmjs, Quora, Glassdoor), PDFs (auto-extracted).

### Works via Zyte (~$0.01/call)
**Crunchbase**, **Capterra**, **X/Twitter** — allowlisted + budget-gated.

### Genuinely blocked (V3.1)

| Site | block_type | Alternative |
|---|---|---|
| **G2.com** | Zyte returns HTTP 520 "Website Ban" specifically | `/search` `site:g2.com ...` |
| **LinkedIn** profiles | All methods fail, no block_type | Use a LinkedIn data API |
| **Instagram** | `empty_content` (auth wall) | N/A |
| **Facebook** | `auth_wall` | N/A |

---

## Browser Actions

Use `actions` in `/scrape` to interact with the page before extracting content:

```json
{
  "actions": [
    {"type": "click", "selector": "#load-more"},
    {"type": "wait", "milliseconds": 2000},
    {"type": "scroll", "direction": "down", "amount": 3},
    {"type": "type", "selector": "#search", "text": "query"},
    {"type": "press_key", "key": "Enter"},
    {"type": "screenshot"}
  ]
}
```

| Action | Parameters | Description |
|--------|-----------|-------------|
| `click` | `selector` | Click an element |
| `type` | `selector`, `text` | Type into input |
| `scroll` | `direction` (up/down), `amount` | Scroll viewport |
| `wait` | `milliseconds` | Wait for content |
| `press_key` | `key` | Press keyboard key |
| `screenshot` | — | Capture at this point |

---

## Rate Limits & Constraints

- **60 requests/minute** and **1000/hour** per API key
- **5 concurrent browser contexts** — 6th request queues until one frees up
- **Static pages use httpx** (no browser) — most requests won't touch the pool
- **Batch limit:** 100 URLs
- **Crawl limit:** 500 pages max
- **Extract limit:** 10 URLs
- **Results expire:** 24 hours for async jobs (crawl/batch/extract)

## Proxy Options

| Value | Provider |
|-------|----------|
| `""` | No proxy (default) |
| `"proxybase"` | ProxyBase rotating residential (Tier 3) |
| `http://user:pass@host:port` | Custom proxy |
