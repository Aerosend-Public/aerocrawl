# Guide: Deploy Cloudflare Workers (Tier 2 — free)

Tier 2 unlocks two capabilities by deploying two Cloudflare Workers from your own account:

- **Reddit JSON scraping** — bypasses the IP block Reddit applies to most VPS providers
- **General paywall bypass** — extra fallback route for non-Reddit sites

Both Workers are free on Cloudflare's free tier (100k requests/day).

You'll create:
- **Reddit Worker** → `REDDIT_PROXY_URL` env var
- **General Worker** → `CF_PROXY_URL` env var

## Prerequisites

- A free Cloudflare account → **https://dash.cloudflare.com/sign-up**
- The `wrangler` CLI (installed with Node 18+)

## Reddit Worker {#reddit}

### 1. Sign into Cloudflare

```bash
npm install -g wrangler
wrangler login
```

A browser window opens for consent.

### 2. Create the Worker

```bash
mkdir -p /tmp/aerocrawl-reddit-worker
cd /tmp/aerocrawl-reddit-worker

cat > wrangler.toml <<'EOF'
name = "aerocrawl-reddit-proxy"
main = "worker.js"
compatibility_date = "2024-01-01"
EOF

cat > worker.js <<'EOF'
// Reddit proxy — forwards /?url=https://old.reddit.com/... to the target,
// adding a desktop User-Agent and returning the JSON.
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = url.searchParams.get("url");
    if (!target) {
      return new Response("Usage: ?url=https://old.reddit.com/r/foo.json", { status: 400 });
    }
    if (!/^https:\/\/(old|www|new|i)\.reddit\.com\//.test(target)) {
      return new Response("URL must be reddit.com", { status: 400 });
    }
    const resp = await fetch(target, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json,text/html",
      },
    });
    return new Response(resp.body, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/json",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
EOF

wrangler deploy
```

Wrangler prints your Worker URL, something like:
```
https://aerocrawl-reddit-proxy.<your-subdomain>.workers.dev
```

This URL is your `REDDIT_PROXY_URL`. Save it.

## General Worker {#general}

Same dance, different Worker:

```bash
mkdir -p /tmp/aerocrawl-general-worker
cd /tmp/aerocrawl-general-worker

cat > wrangler.toml <<'EOF'
name = "aerocrawl-general-proxy"
main = "worker.js"
compatibility_date = "2024-01-01"
EOF

cat > worker.js <<'EOF'
// General-purpose proxy — forwards /?url=<any-https-url>, useful for bypassing
// IP-based rate limits on sites that don't block Cloudflare edge IPs.
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = url.searchParams.get("url");
    if (!target) return new Response("Usage: ?url=...", { status: 400 });
    try {
      new URL(target);
    } catch {
      return new Response("Invalid target URL", { status: 400 });
    }
    const resp = await fetch(target, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
      },
      redirect: "follow",
    });
    return new Response(resp.body, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "text/html",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
EOF

wrangler deploy
```

Save the resulting URL as `CF_PROXY_URL`.

## Wiring into Aerocrawl

Add both to `/opt/aerocrawl/.env`:

```bash
ssh root@<ip>
cd /opt/aerocrawl
cat >> .env <<EOF
REDDIT_PROXY_URL=https://aerocrawl-reddit-proxy.<your-subdomain>.workers.dev
CF_PROXY_URL=https://aerocrawl-general-proxy.<your-subdomain>.workers.dev
EOF
systemctl restart aerocrawl aerocrawl-worker
venv/bin/python install/check-capabilities.py
```

Expected: `✓ Tier 2  Reddit + paywall bypass`

## Rate limits

Cloudflare free tier gives each Worker:
- 100,000 requests/day
- 10 ms CPU time per request (ample for these thin proxies)

If you exceed that, Aerocrawl gracefully falls back to the next step in the chain. You'll see
a log line like `cf_proxy_rate_limited, falling back to proxybase`.
