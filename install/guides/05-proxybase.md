# Guide: ProxyBase (Tier 3 — paid, residential proxies)

Tier 3 adds rotating residential proxies, dramatically increasing your success rate on
Cloudflare-protected and other anti-bot sites. Aerocrawl uses **ProxyBase** as its proxy
provider.

**Cost:** ~$8–30/month depending on traffic. Smallest plan (~$8) covers ~10 GB of proxy
bandwidth, which is enough for ~50k–100k scrapes of typical-sized pages.

## Steps

### 1. Sign up

**https://proxybase.com** (or your preferred residential proxy provider — Aerocrawl accepts
any HTTP/HTTPS proxy URL).

Pick the smallest residential plan. Most providers offer a 1–7 day free trial.

### 2. Get your proxy URL

In the ProxyBase dashboard, look for the endpoint format (usually):

```
http://<username>:<password>@gate.proxybase.io:10000
```

Copy this URL including credentials.

### 3. Add to Aerocrawl

```bash
ssh root@<ip>
cd /opt/aerocrawl
echo "PROXY_URL=http://user:pass@gate.proxybase.io:10000" >> .env
systemctl restart aerocrawl aerocrawl-worker
venv/bin/python install/check-capabilities.py
```

Expected: `✓ Tier 3  Residential proxies`

## When proxies kick in

Aerocrawl's fallback chain only uses proxies when the preceding steps fail — so you're not
paying for bandwidth on easy sites. The proxy steps are tried only for Cloudflare-challenged
or IP-blocked responses.

## Other providers

Any HTTP proxy URL works. Tested with:
- IPRoyal (`http://user:pass@geo.iproyal.com:12321`)
- Bright Data, Oxylabs, Smartproxy (format similar)
- Your own Squid/3proxy if you run it

Set `PROXY_URL` to the full URL including auth.
