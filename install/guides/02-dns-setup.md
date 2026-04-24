# Guide: Domain and DNS setup

Aerocrawl needs a hostname to serve HTTPS with Let's Encrypt via Caddy. You have two choices.

## Option A — Free sslip.io subdomain (zero setup, recommended for testing)

If your VPS IP is `203.0.113.42`, your hostname becomes:

```
203-0-113-42.sslip.io
```

sslip.io is a free wildcard DNS service that resolves `1-2-3-4.sslip.io → 1.2.3.4` automatically.
No account, no DNS records needed. Caddy still gets a real Let's Encrypt certificate because
sslip.io supports the ACME HTTP-01 challenge.

The wizard uses this by default. Good enough for real production use — you're not hiding an
IP anyway.

## Option B — Your own domain

If you have a domain (say `example.com`), pick a subdomain like `scraper.example.com`.

### 1. Add an A record

In your DNS provider (Cloudflare, Namecheap, Google Domains, Route 53 — anywhere):

| Type | Name    | Value        | TTL |
|------|---------|--------------|-----|
| A    | scraper | `<your IP>`  | 300 |

Use TTL 300 (5 min) while setting up so changes propagate fast.

**Cloudflare users:** turn OFF the orange proxy cloud (set to "DNS only"). If you want
Cloudflare in front, set it up **after** Aerocrawl is running — otherwise the ACME HTTP-01
challenge fails because Cloudflare's edge intercepts port 80.

### 2. Wait for propagation

```bash
dig +short scraper.example.com
```

Should return your IP. May take 1–10 minutes depending on TTL.

### 3. Done

Pass the hostname to the wizard or the bootstrap script:

```bash
AEROCRAWL_DOMAIN="scraper.example.com"
```

Caddy will get a Let's Encrypt certificate automatically on first start.

---

**Troubleshooting:**
- DNS not resolving after 10 min? Check your provider's DNS zone directly; some slow propagators
  take up to an hour.
- Caddy failing to get a cert? Check port 80 is reachable (`curl http://scraper.example.com`),
  and that no proxy (Cloudflare, etc.) is intercepting port 80.
