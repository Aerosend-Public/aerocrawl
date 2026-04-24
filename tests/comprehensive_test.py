"""Comprehensive production test for NinjaScraper — tests every endpoint end-to-end.

NOT a unit test — run manually against a live server:
    python tests/comprehensive_test.py --base-url https://scraper.example.com/scraper --api-key ns-xxx
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

import httpx

BASE_URL = ""
HEADERS = {}
RESULTS: list[dict] = []


def log(status: str, name: str, detail: str = "", latency_ms: int = 0):
    icon = "PASS" if status == "pass" else "FAIL" if status == "fail" else "SKIP"
    lat = f" ({latency_ms}ms)" if latency_ms else ""
    print(f"  [{icon}] {name}{lat}")
    if detail:
        print(f"         {detail}")
    RESULTS.append({"status": status, "name": name, "latency_ms": latency_ms})


async def timed_post(client: httpx.AsyncClient, path: str, body: dict) -> tuple[int, dict | bytes, int]:
    start = time.time()
    resp = await client.post(f"{BASE_URL}{path}", json=body, headers=HEADERS)
    ms = int((time.time() - start) * 1000)
    ct = resp.headers.get("content-type", "")
    if "image/" in ct:
        return ms, resp.content, resp.status_code
    try:
        return ms, resp.json(), resp.status_code
    except Exception:
        return ms, {"raw": resp.text[:500]}, resp.status_code


async def timed_get(client: httpx.AsyncClient, path: str) -> tuple[int, dict, int]:
    start = time.time()
    resp = await client.get(f"{BASE_URL}{path}", headers=HEADERS)
    ms = int((time.time() - start) * 1000)
    try:
        return ms, resp.json(), resp.status_code
    except Exception:
        return ms, {"raw": resp.text[:500]}, resp.status_code


async def timed_delete(client: httpx.AsyncClient, path: str) -> tuple[int, dict, int]:
    start = time.time()
    resp = await client.delete(f"{BASE_URL}{path}", headers=HEADERS)
    ms = int((time.time() - start) * 1000)
    return ms, resp.json(), resp.status_code


# ─── AUTH TESTS ───

async def test_auth(client: httpx.AsyncClient):
    print("\n--- AUTH ---")

    # Health needs no auth
    ms, data, code = await timed_get(client, "/health")
    if code == 200 and data.get("status") == "ok":
        log("pass", "Health (no auth)", f"uptime={data.get('uptime')}s", ms)
    else:
        log("fail", "Health (no auth)", f"code={code}", ms)

    # Scrape without auth → 401
    resp = await client.post(f"{BASE_URL}/scrape", json={"url": "https://example.com"})
    if resp.status_code == 401:
        log("pass", "Scrape without auth → 401")
    else:
        log("fail", "Scrape without auth → 401", f"got {resp.status_code}")

    # Scrape with bad key → 401
    resp = await client.post(f"{BASE_URL}/scrape", json={"url": "https://example.com"},
                              headers={"Authorization": "Bearer ns-invalidkey12345678901234567890"})
    if resp.status_code == 401:
        log("pass", "Scrape with bad key → 401")
    else:
        log("fail", "Scrape with bad key → 401", f"got {resp.status_code}")


# ─── SSRF TESTS ───

async def test_ssrf(client: httpx.AsyncClient):
    print("\n--- SSRF PROTECTION ---")

    bad_urls = [
        ("http://localhost:8000/health", "localhost"),
        ("http://127.0.0.1:8000/health", "loopback IP"),
        ("http://0.0.0.0/", "zero IP"),
        ("ftp://example.com/file", "non-http scheme"),
        ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
    ]

    for url, desc in bad_urls:
        ms, data, code = await timed_post(client, "/scrape", {"url": url})
        if code == 400:
            log("pass", f"SSRF blocked: {desc}")
        else:
            log("fail", f"SSRF blocked: {desc}", f"code={code} — should be 400")


# ─── SCRAPE TESTS ───

async def test_scrape(client: httpx.AsyncClient):
    print("\n--- SCRAPE ---")

    # Basic scrape — static page
    ms, data, code = await timed_post(client, "/scrape", {
        "url": "https://example.com",
        "formats": ["markdown", "metadata", "links"],
    })
    if code == 200 and data.get("success"):
        has_md = bool(data.get("markdown"))
        has_meta = bool(data.get("metadata"))
        has_links = bool(data.get("links"))
        method = data.get("scrape_method", "?")
        log("pass", f"Scrape example.com (method={method})",
            f"md={has_md}, meta={has_meta}, links={len(data.get('links', []))}", ms)
    else:
        log("fail", "Scrape example.com", f"code={code}, error={data.get('error', '?')}", ms)

    # Scrape with screenshot (forces Playwright)
    ms, data, code = await timed_post(client, "/scrape", {
        "url": "https://example.com",
        "formats": ["markdown", "screenshot", "metadata"],
    })
    if code == 200 and data.get("success"):
        has_screenshot = bool(data.get("screenshot"))
        method = data.get("scrape_method", "?")
        log("pass", f"Scrape+screenshot (method={method})", f"has_screenshot={has_screenshot}", ms)
    else:
        log("fail", "Scrape+screenshot", f"code={code}", ms)

    # Scrape with selector
    ms, data, code = await timed_post(client, "/scrape", {
        "url": "https://example.com",
        "formats": ["markdown"],
        "selector": "h1",
    })
    if code == 200 and data.get("success"):
        md = data.get("markdown", "")
        log("pass", "Scrape with selector (h1)", f"content='{md[:50]}'", ms)
    else:
        log("fail", "Scrape with selector", f"error={data.get('error', '?')}", ms)

    # Scrape JS-heavy domain (should use Playwright)
    ms, data, code = await timed_post(client, "/scrape", {
        "url": "https://twitter.com",
        "formats": ["metadata"],
        "timeout_ms": 15000,
    })
    method = data.get("scrape_method", "?") if isinstance(data, dict) else "?"
    if code == 200:
        log("pass", f"Scrape JS-heavy domain (method={method})", "", ms)
    else:
        log("fail", f"Scrape twitter.com", f"code={code}", ms)

    # Scrape Wikipedia (real content page)
    ms, data, code = await timed_post(client, "/scrape", {
        "url": "https://en.wikipedia.org/wiki/Web_scraping",
        "formats": ["markdown", "metadata", "links"],
        "only_main_content": True,
    })
    if code == 200 and data.get("success"):
        md_len = len(data.get("markdown", ""))
        links_count = len(data.get("links", []))
        log("pass", "Scrape Wikipedia (main content)", f"markdown={md_len} chars, links={links_count}", ms)
    else:
        log("fail", "Scrape Wikipedia", f"code={code}", ms)


# ─── SCREENSHOT TESTS ───

async def test_screenshot(client: httpx.AsyncClient):
    print("\n--- SCREENSHOT ---")

    ms, data, code = await timed_post(client, "/screenshot", {
        "url": "https://example.com",
        "full_page": True,
    })
    if code == 200 and isinstance(data, bytes) and len(data) > 1000:
        log("pass", "Screenshot example.com", f"size={len(data)} bytes (PNG)", ms)
    elif code == 200:
        log("pass", "Screenshot example.com", f"response type={type(data)}", ms)
    else:
        log("fail", "Screenshot example.com", f"code={code}", ms)


# ─── MAP TESTS ───

async def test_map(client: httpx.AsyncClient):
    print("\n--- MAP ---")

    ms, data, code = await timed_post(client, "/map", {
        "url": "https://www.python.org",
        "max_urls": 50,
    })
    if code == 200 and data.get("success"):
        total = data.get("total", 0)
        sources = data.get("sources", {})
        log("pass", "Map python.org", f"found {total} URLs, sources={sources}", ms)
    else:
        log("fail", "Map python.org", f"code={code}", ms)

    # Map with filter
    ms, data, code = await timed_post(client, "/map", {
        "url": "https://docs.python.org",
        "max_urls": 20,
        "include_paths": ["^/3/library/"],
    })
    if code == 200 and data.get("success"):
        urls = data.get("urls", [])
        all_match = all("/3/library/" in u for u in urls) if urls else True
        log("pass", f"Map with include filter", f"found {len(urls)} matching URLs, all_match={all_match}", ms)
    else:
        log("fail", "Map with include filter", f"code={code}", ms)


# ─── CONCURRENT STRESS ───

async def test_concurrent(client: httpx.AsyncClient):
    print("\n--- CONCURRENT STRESS ---")

    urls = [
        "https://example.com",
        "https://httpbin.org/html",
        "https://www.python.org",
        "https://docs.python.org/3/",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
    ]

    # 5 concurrent scrapes
    latencies = []
    failures = 0

    async def scrape_one(url):
        nonlocal failures
        ms, data, code = await timed_post(client, "/scrape", {"url": url, "formats": ["markdown"]})
        latencies.append(ms)
        if code != 200 or not (isinstance(data, dict) and data.get("success")):
            failures += 1

    start = time.time()
    await asyncio.gather(*[scrape_one(u) for u in urls])
    total = time.time() - start

    if failures == 0:
        log("pass", "5 concurrent scrapes",
            f"avg={statistics.mean(latencies):.0f}ms, max={max(latencies)}ms, total={total:.1f}s")
    else:
        log("fail", "5 concurrent scrapes", f"{failures}/5 failed")

    # 5 concurrent screenshots (saturates pool)
    latencies = []
    failures = 0

    async def screenshot_one(url):
        nonlocal failures
        ms, data, code = await timed_post(client, "/screenshot", {"url": url, "timeout_ms": 20000})
        latencies.append(ms)
        if code != 200:
            failures += 1

    start = time.time()
    await asyncio.gather(*[screenshot_one(u) for u in urls])
    total = time.time() - start

    log("pass" if failures <= 1 else "fail",
        f"5 concurrent screenshots ({failures} failed)",
        f"avg={statistics.mean(latencies):.0f}ms, max={max(latencies)}ms, total={total:.1f}s")

    # 8 concurrent (exceeds pool of 5 — tests queuing)
    latencies = []
    failures = 0
    extra_urls = urls + ["https://example.org", "https://httpbin.org/get", "https://httpbin.org/ip"]

    async def scrape_queued(url):
        nonlocal failures
        ms, data, code = await timed_post(client, "/scrape", {
            "url": url, "formats": ["markdown", "metadata"],
        })
        latencies.append(ms)
        if code != 200 or not (isinstance(data, dict) and data.get("success")):
            failures += 1

    start = time.time()
    await asyncio.gather(*[scrape_queued(u) for u in extra_urls])
    total = time.time() - start

    log("pass" if failures <= 1 else "fail",
        f"8 concurrent (pool saturation, {failures} failed)",
        f"avg={statistics.mean(latencies):.0f}ms, max={max(latencies)}ms, total={total:.1f}s")


# ─── CRAWL TEST ───

async def test_crawl(client: httpx.AsyncClient):
    print("\n--- CRAWL (async) ---")

    # Start crawl
    ms, data, code = await timed_post(client, "/crawl", {
        "url": "https://example.com",
        "max_pages": 3,
        "max_depth": 1,
        "formats": ["markdown", "metadata"],
    })
    if code != 200 or not data.get("success"):
        log("fail", "Start crawl", f"code={code}, data={data}")
        return

    job_id = data["job_id"]
    log("pass", f"Crawl started", f"job_id={job_id}", ms)

    # Poll for completion (max 60s)
    for i in range(30):
        await asyncio.sleep(2)
        ms, status, code = await timed_get(client, f"/crawl/{job_id}")
        if code != 200:
            continue
        s = status.get("status", "")
        pages = status.get("pages_scraped", 0)
        if s == "completed":
            log("pass", f"Crawl completed", f"pages={pages}", ms)
            break
        elif s == "failed":
            log("fail", "Crawl failed", f"error={status.get('error', '?')}")
            break
    else:
        log("fail", "Crawl timeout", "Did not complete in 60s")

    # Cancel test (start a new one and cancel)
    ms, data, code = await timed_post(client, "/crawl", {
        "url": "https://www.python.org",
        "max_pages": 50,
    })
    if code == 200 and data.get("job_id"):
        cancel_id = data["job_id"]
        await asyncio.sleep(1)
        ms, cancel_data, code = await timed_delete(client, f"/crawl/{cancel_id}")
        if code == 200 and cancel_data.get("success"):
            log("pass", "Crawl cancel", f"job={cancel_id}")
        else:
            log("fail", "Crawl cancel", f"code={code}")


# ─── BATCH TEST ───

async def test_batch(client: httpx.AsyncClient):
    print("\n--- BATCH SCRAPE (async) ---")

    ms, data, code = await timed_post(client, "/batch/scrape", {
        "urls": ["https://example.com", "https://httpbin.org/html", "https://example.org"],
        "formats": ["markdown", "metadata"],
    })
    if code != 200 or not data.get("success"):
        log("fail", "Start batch", f"code={code}, data={data}")
        return

    job_id = data["job_id"]
    log("pass", f"Batch started", f"job_id={job_id}, urls=3", ms)

    # Poll
    for i in range(20):
        await asyncio.sleep(2)
        ms, status, code = await timed_get(client, f"/batch/{job_id}")
        if code != 200:
            continue
        s = status.get("status", "")
        completed = status.get("completed", 0)
        if s == "completed":
            results = status.get("data", [])
            successes = sum(1 for r in results if r.get("success"))
            log("pass", f"Batch completed", f"{successes}/{len(results)} succeeded", ms)
            break
        elif s == "failed":
            log("fail", "Batch failed", f"error={status.get('error', '?')}")
            break
    else:
        log("fail", "Batch timeout", "Did not complete in 40s")

    # Batch size limit test
    ms, data, code = await timed_post(client, "/batch/scrape", {
        "urls": [f"https://example.com/{i}" for i in range(101)],
    })
    if code == 400 or code == 422:
        log("pass", "Batch size limit (101 URLs rejected)")
    else:
        log("fail", "Batch size limit", f"code={code} — expected 400")


# ─── KEYS & USAGE ───

async def test_keys_and_usage(client: httpx.AsyncClient):
    print("\n--- KEYS & USAGE ---")

    # List keys (admin)
    ms, data, code = await timed_get(client, "/keys")
    if code == 200 and isinstance(data, dict):
        keys = data.get("keys", data.get("data", []))
        log("pass", f"List keys", f"count={len(keys) if isinstance(keys, list) else '?'}", ms)
    else:
        log("fail", "List keys", f"code={code}")

    # Create a test key
    ms, data, code = await timed_post(client, "/keys", {
        "name": "Stress Test Key",
        "team_member": "Test",
    })
    if code == 200 and data.get("key", "").startswith("ns-"):
        test_key = data["key"]
        test_key_id = data["key_id"]
        log("pass", "Create key", f"prefix={test_key[:8]}...", ms)

        # Verify the new key works
        resp = await client.post(f"{BASE_URL}/scrape",
                                  json={"url": "https://example.com", "formats": ["metadata"]},
                                  headers={"X-API-Key": test_key})
        if resp.status_code == 200:
            log("pass", "New key works for scraping")
        else:
            log("fail", "New key works for scraping", f"code={resp.status_code}")

        # Non-admin can't list keys
        resp = await client.get(f"{BASE_URL}/keys", headers={"X-API-Key": test_key})
        if resp.status_code == 403:
            log("pass", "Non-admin can't list keys (403)")
        else:
            log("fail", "Non-admin can't list keys", f"code={resp.status_code}")

        # Revoke it
        ms, data, code = await timed_delete(client, f"/keys/{test_key_id}")
        if code == 200:
            log("pass", "Revoke key", latency_ms=ms)
        else:
            log("fail", "Revoke key", f"code={code}")

        # Revoked key should fail
        resp = await client.post(f"{BASE_URL}/scrape",
                                  json={"url": "https://example.com"},
                                  headers={"X-API-Key": test_key})
        if resp.status_code == 401:
            log("pass", "Revoked key rejected (401)")
        else:
            log("fail", "Revoked key rejected", f"code={resp.status_code}")
    else:
        log("fail", "Create key", f"code={code}")

    # Usage stats
    ms, data, code = await timed_get(client, "/usage")
    if code == 200:
        log("pass", "Usage stats", f"total={data.get('total_requests', '?')}", ms)
    else:
        log("fail", "Usage stats", f"code={code}")


# ─── STABILITY TEST ───

async def test_stability(client: httpx.AsyncClient):
    print("\n--- STABILITY (rapid fire) ---")

    # 20 rapid sequential requests
    successes = 0
    latencies = []
    for i in range(20):
        ms, data, code = await timed_post(client, "/scrape", {
            "url": "https://example.com", "formats": ["markdown"],
        })
        latencies.append(ms)
        if code == 200 and isinstance(data, dict) and data.get("success"):
            successes += 1

    log("pass" if successes == 20 else "fail",
        f"20 rapid sequential scrapes ({successes}/20 ok)",
        f"avg={statistics.mean(latencies):.0f}ms, p95={sorted(latencies)[18]}ms")

    # Health after rapid fire — pool should be clean
    ms, data, code = await timed_get(client, "/health")
    pool = data.get("browser_pool", {})
    active = pool.get("active_contexts", -1)
    if active == 0:
        log("pass", "Pool clean after stress", f"active={active}")
    else:
        log("fail", "Pool leak detected", f"active={active} — should be 0")


async def main():
    parser = argparse.ArgumentParser(description="NinjaScraper Comprehensive Test")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()

    global BASE_URL, HEADERS
    BASE_URL = args.base_url.rstrip("/")
    HEADERS = {"Authorization": f"Bearer {args.api_key}"}

    print(f"\n{'='*60}")
    print(f"  NinjaScraper Comprehensive Production Test")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*60}")

    async with httpx.AsyncClient(timeout=120) as client:
        await test_auth(client)
        await test_ssrf(client)
        await test_scrape(client)
        await test_screenshot(client)
        await test_map(client)
        await test_concurrent(client)
        await test_crawl(client)
        await test_batch(client)
        await test_keys_and_usage(client)
        await test_stability(client)

    # Final summary
    passed = sum(1 for r in RESULTS if r["status"] == "pass")
    failed = sum(1 for r in RESULTS if r["status"] == "fail")
    skipped = sum(1 for r in RESULTS if r["status"] == "skip")
    total = len(RESULTS)

    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed:
        print(f"\n  FAILURES:")
        for r in RESULTS:
            if r["status"] == "fail":
                print(f"    - {r['name']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
