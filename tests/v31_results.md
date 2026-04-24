# NinjaScraper V3.1 Comprehensive Test Report

**Run:** 2026-04-24T12:24:03.312435+00:00  
**Target:** https://scraper.example.com/scraper  
**Overall:** 50/57 passed

## Speed summary

| URL | Method | Cold | Cached | Speedup |
|---|---|---|---|---|
| `https://example.com` | playwright+stealth | 705ms | 613ms | 1.15× |
| `https://www.aerosend.io/` | playwright+stealth | 1850ms | 1528ms | 1.21× |
| `https://en.wikipedia.org/wiki/Aerodynamics` | playwright+stealth | 1546ms | 223ms | 6.93× |
| `https://github.com/anthropics/claude-code` | route:github:repo | 786ms | 216ms | 3.64× |
| `https://arxiv.org/abs/2310.06770` | playwright+stealth | 1321ms | 216ms | 6.12× |
| `https://news.ycombinator.com/news` | route:hackernews:frontpage | 608ms | 223ms | 2.73× |
| `https://www.mailforge.ai/` | playwright+stealth | 2065ms | 220ms | 9.39× |
| `https://instantly.ai/` | playwright+stealth | 1784ms | 1620ms | 1.1× |
| `https://www.sec.gov/files/form10-k.pdf` | pdf:pymupdf | 3574ms | 220ms | 16.25× |

## Concurrency

| Test | Result | Detail |
|---|---|---|
| 5x parallel different URLs | PASS | 5/5 succeeded in 2367ms total |
| 10x parallel same URL (cache) | FAIL | 0/10 cache hits in 1608ms |
| 25x parallel /budget/zyte | PASS | 25 ok, 0 rate-limited in 890ms |

## Reliability

| Test | Result | Detail |
|---|---|---|
| force_refresh bypasses cache | FAIL | after prime: cached=False, after force_refresh: cached=False |
| DELETE /cache?url=... invalidates | FAIL | deleted 0 keys, post-purge cached=False |
| schema-first extract | PASS | extracted=None, error=LLM failed to produce valid JSON matching the schema |
| /budget/zyte shows $30 cap | PASS | cap=30.0, spent=0.01, remaining=29.99 |

## Features

| Test | Result | Latency | Detail |
|---|---|---|---|
| formats=['images'] returns triples | PASS | 1982ms | 46 images, informational=0 |
| PDF extraction with page count | PASS | 3478ms | pages=19, md_len=40649 |
| POST /map discovers URLs | PASS | 5587ms | total=30, sources={'sitemap': 30, 'robots_txt': 0, 'page_links': 0} |
| POST /strategy/probe canaries | PASS | 138175ms | 6/7 canaries succeeded; methods=['cf_worker_reddit', 'playwright+stealth', 'route:github:repo', 'route:hackernews:frontpage'] |

## Site compatibility matrix

### auth_wall

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.linkedin.com/in/rithikrajput` | ✗ | — | 13532ms | 0 | All scraping methods failed |
| `x.com/anthropicai` | ✓ | zyte | 70485ms | 265 | — |
| `www.instagram.com/anthropic/` | ✗ | — | 63674ms | 0 | empty_content |

### docs

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `en.wikipedia.org/wiki/Web_scraping` | ✓ | playwright+stealth | 2901ms | 39175 | — |
| `docs.python.org/3/tutorial/index.html` | ✓ | playwright+stealth | 2794ms | 2516 | — |

### ecommerce

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.npmjs.com/package/react` | ✓ | playwright+stealth | 1925ms | 1112 | — |

### hardened

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.crunchbase.com/organization/anthropic` | ✓ | zyte | 25030ms | 123 | — |
| `www.g2.com/products/lemlist/reviews` | ✗ | — | 131752ms | 0 | captcha |
| `www.capterra.com/p/275258/Lemlist/` | ✓ | zyte | 24466ms | 829 | — |
| `www.quora.com/What-is-cold-email` | ✓ | playwright+stealth | 2199ms | 61 | — |
| `www.glassdoor.com/Overview/Working-at-Anthropic-EI_IE6623627` | ✓ | playwright+stealth | 2031ms | 5024 | — |

### js_heavy

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.mailforge.ai/` | ✓ | playwright+stealth | 2712ms | 468 | — |
| `instantly.ai/` | ✓ | playwright+stealth | 2461ms | 59 | — |
| `www.trellus.ai/pricing` | ✓ | playwright+stealth | 4658ms | 836 | — |

### news

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `techcrunch.com/` | ✗ | — | 47132ms | 0 | captcha |
| `medium.com/` | ✓ | playwright+stealth | 4670ms | 83 | — |

### pdf

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.sec.gov/files/form10-k.pdf` | ✓ | pdf:pymupdf | 4652ms | 40649 | — |
| `arxiv.org/pdf/2310.06770` | ✓ | route:academic:arxiv | 372ms | 1635 | — |

### reddit

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.reddit.com/r/coldemail/top.json` | ✓ | cf_worker_reddit | 4671ms | 31071 | — |

### reviews

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `www.trustpilot.com/review/aerosend.io` | ✓ | playwright+stealth | 2008ms | 170 | — |

### rss

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `simonwillison.net/atom/everything/` | ✓ | route:rss | 1131ms | 119181 | — |

### smart_route

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `github.com/anthropics/claude-code` | ✓ | route:github:repo | 1093ms | 3335 | — |
| `github.com/anthropics/claude-code/issues/1` | ✓ | route:github:issue | 1132ms | 2287 | — |
| `arxiv.org/abs/2310.06770` | ✓ | route:academic:arxiv | 710ms | 1635 | — |
| `pubmed.ncbi.nlm.nih.gov/38528089/` | ✓ | route:academic:pubmed | 902ms | 1798 | — |
| `news.ycombinator.com/news` | ✓ | route:hackernews:frontpage | 855ms | 4173 | — |
| `news.ycombinator.com/item?id=44159044` | ✓ | playwright+stealth | 2047ms | 968 | — |
| `doi.org/10.1038/s41586-023-06924-6` | ✓ | route:academic:doi | 1008ms | 2001 | — |

### static_html

| URL | Status | Method | Latency | md_len | Block / Error |
|---|---|---|---|---|---|
| `example.com` | ✓ | playwright+stealth | 577ms | 167 | — |
| `www.aerosend.io/` | ✓ | playwright+stealth | 3065ms | 112 | — |
| `httpbin.org/html` | ✓ | playwright+stealth | 1691ms | 3566 | — |

### Summary

- **Working:** 27/31 sites
- **Blocked:** 4/31 sites

#### Blocked sites (for CLAUDE.md / skill updates)

- `techcrunch.com` — captcha
- `www.g2.com` — captcha
- `www.linkedin.com` — All scraping methods failed
- `www.instagram.com` — empty_content
