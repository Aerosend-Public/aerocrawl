"""Stress test for Aerocrawl — measures throughput, latency, and stability.

NOT a unit test — run manually against a live server. Excluded from pytest collection.

Usage:
    python tests/stress_test.py --base-url https://scraper.example.com/scraper --api-key ns-xxx
    python tests/stress_test.py --base-url http://localhost:8001 --api-key ns-xxx

Tests:
    1. Sequential scrape latency (10 requests)
    2. Concurrent scrape throughput (10 concurrent)
    3. Static vs Playwright routing (verify fallback chain)
    4. Screenshot under load (5 concurrent)
    5. Map endpoint speed
    6. Browser pool saturation (7 concurrent — exceeds pool of 5)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class TestResult:
    name: str
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0

    def summary(self) -> str:
        return (
            f"  {self.name}:\n"
            f"    Requests: {self.total_requests} ({self.successful} ok, {self.failed} failed)\n"
            f"    Latency: avg={self.avg_ms:.0f}ms, p50={self.p50_ms:.0f}ms, p95={self.p95_ms:.0f}ms, max={self.max_ms:.0f}ms"
        )


# Test URLs — mix of static and JS-heavy
STATIC_URLS = [
    "https://example.com",
    "https://httpbin.org/html",
    "https://www.python.org",
    "https://docs.python.org/3/",
    "https://en.wikipedia.org/wiki/Web_scraping",
]

JS_HEAVY_URLS = [
    "https://twitter.com",  # Should trigger Playwright (JS_HEAVY_DOMAINS)
]

MAP_URL = "https://www.python.org"


async def timed_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> tuple[float, httpx.Response | None, str]:
    """Make a request and return (latency_ms, response, error)."""
    start = time.time()
    try:
        if method == "GET":
            resp = await client.get(url, **kwargs)
        else:
            resp = await client.post(url, **kwargs)
        latency = (time.time() - start) * 1000
        return latency, resp, ""
    except Exception as e:
        latency = (time.time() - start) * 1000
        return latency, None, str(e)


async def test_health(client: httpx.AsyncClient, base_url: str) -> dict:
    """Check health endpoint — no auth needed."""
    _, resp, err = await timed_request(client, "GET", f"{base_url}/health")
    if resp and resp.status_code == 200:
        return resp.json()
    return {"error": err or f"status {resp.status_code if resp else 'no response'}"}


async def test_sequential_scrape(client: httpx.AsyncClient, base_url: str, headers: dict) -> TestResult:
    """Test 1: Sequential scrape — measures single-request latency."""
    result = TestResult(name="Sequential Scrape (10 requests)")
    for url in STATIC_URLS * 2:  # 10 requests
        latency, resp, err = await timed_request(
            client, "POST", f"{base_url}/scrape",
            json={"url": url, "formats": ["markdown", "metadata"]},
            headers=headers,
        )
        result.total_requests += 1
        result.latencies_ms.append(latency)
        if resp and resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                result.successful += 1
            else:
                result.failed += 1
                result.errors.append(f"{url}: {data.get('error', 'unknown')}")
        else:
            result.failed += 1
            result.errors.append(f"{url}: {err or resp.status_code if resp else 'no response'}")
    return result


async def test_concurrent_scrape(client: httpx.AsyncClient, base_url: str, headers: dict) -> TestResult:
    """Test 2: Concurrent scrape — 10 simultaneous requests."""
    result = TestResult(name="Concurrent Scrape (10 simultaneous)")

    async def scrape_one(url: str):
        latency, resp, err = await timed_request(
            client, "POST", f"{base_url}/scrape",
            json={"url": url, "formats": ["markdown", "metadata"]},
            headers=headers,
        )
        result.total_requests += 1
        result.latencies_ms.append(latency)
        if resp and resp.status_code == 200 and resp.json().get("success"):
            result.successful += 1
        else:
            result.failed += 1
            result.errors.append(f"{url}: {err or 'failed'}")

    tasks = [scrape_one(url) for url in STATIC_URLS * 2]
    await asyncio.gather(*tasks)
    return result


async def test_fallback_chain(client: httpx.AsyncClient, base_url: str, headers: dict) -> TestResult:
    """Test 3: Verify static vs Playwright routing."""
    result = TestResult(name="Fallback Chain Verification")

    # Static page — should use httpx
    latency, resp, err = await timed_request(
        client, "POST", f"{base_url}/scrape",
        json={"url": "https://example.com", "formats": ["markdown", "metadata"]},
        headers=headers,
    )
    result.total_requests += 1
    result.latencies_ms.append(latency)
    if resp and resp.status_code == 200:
        data = resp.json()
        method = data.get("data", {}).get("metadata", {}).get("scrape_method", "")
        if method == "httpx":
            result.successful += 1
            print(f"    example.com → {method} ({latency:.0f}ms) ✓")
        else:
            result.failed += 1
            print(f"    example.com → {method} ({latency:.0f}ms) — expected httpx")
    else:
        result.failed += 1

    # Force Playwright via screenshot
    latency, resp, err = await timed_request(
        client, "POST", f"{base_url}/scrape",
        json={"url": "https://example.com", "formats": ["screenshot", "metadata"]},
        headers=headers,
    )
    result.total_requests += 1
    result.latencies_ms.append(latency)
    if resp and resp.status_code == 200:
        data = resp.json()
        method = data.get("data", {}).get("metadata", {}).get("scrape_method", "")
        if method == "playwright":
            result.successful += 1
            print(f"    example.com+screenshot → {method} ({latency:.0f}ms) ✓")
        else:
            result.failed += 1
            print(f"    example.com+screenshot → {method} ({latency:.0f}ms) — expected playwright")
    else:
        result.failed += 1

    return result


async def test_screenshot_load(client: httpx.AsyncClient, base_url: str, headers: dict) -> TestResult:
    """Test 4: Screenshot under load — 5 concurrent."""
    result = TestResult(name="Concurrent Screenshots (5 simultaneous)")

    async def screenshot_one(url: str):
        latency, resp, err = await timed_request(
            client, "POST", f"{base_url}/screenshot",
            json={"url": url},
            headers=headers,
        )
        result.total_requests += 1
        result.latencies_ms.append(latency)
        if resp and resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            result.successful += 1
        else:
            result.failed += 1
            result.errors.append(f"{url}: {err or 'failed'}")

    tasks = [screenshot_one(url) for url in STATIC_URLS]
    await asyncio.gather(*tasks)
    return result


async def test_map_speed(client: httpx.AsyncClient, base_url: str, headers: dict) -> TestResult:
    """Test 5: Map endpoint speed."""
    result = TestResult(name="Map Endpoint")
    latency, resp, err = await timed_request(
        client, "POST", f"{base_url}/map",
        json={"url": MAP_URL, "max_urls": 100},
        headers=headers,
    )
    result.total_requests += 1
    result.latencies_ms.append(latency)
    if resp and resp.status_code == 200:
        data = resp.json()
        if data.get("success"):
            result.successful += 1
            print(f"    Found {data.get('total', 0)} URLs in {latency:.0f}ms")
        else:
            result.failed += 1
    else:
        result.failed += 1
        result.errors.append(err or "failed")
    return result


async def test_pool_saturation(client: httpx.AsyncClient, base_url: str, headers: dict) -> TestResult:
    """Test 6: Browser pool saturation — 7 concurrent (pool is 5)."""
    result = TestResult(name="Pool Saturation (7 concurrent, pool=5)")

    async def screenshot_one(url: str):
        latency, resp, err = await timed_request(
            client, "POST", f"{base_url}/screenshot",
            json={"url": url},
            headers=headers,
        )
        result.total_requests += 1
        result.latencies_ms.append(latency)
        if resp and resp.status_code == 200:
            result.successful += 1
        else:
            result.failed += 1
            result.errors.append(f"{url}: {err or 'failed'}")

    # 7 URLs — 5 run immediately, 2 wait for pool
    urls = STATIC_URLS + ["https://httpbin.org/html", "https://example.org"]
    tasks = [screenshot_one(url) for url in urls]
    await asyncio.gather(*tasks)
    return result


async def main():
    parser = argparse.ArgumentParser(description="Aerocrawl Stress Test")
    parser.add_argument("--base-url", required=True, help="Base URL (e.g., http://localhost:8001)")
    parser.add_argument("--api-key", required=True, help="API key (ns-xxx)")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.api_key}"}

    print(f"\n{'='*60}")
    print(f"  Aerocrawl Stress Test")
    print(f"  Target: {base_url}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient(timeout=120) as client:
        # Health check first
        print("Checking health...")
        health = await test_health(client, base_url)
        if "error" in health:
            print(f"  FAILED: {health['error']}")
            return
        print(f"  Status: {health.get('status')}")
        print(f"  Browser pool: {health.get('browser_pool', {})}")
        print(f"  Redis: {health.get('redis', {})}")
        print()

        results: list[TestResult] = []

        # Run tests sequentially
        tests = [
            ("Test 1: Sequential Scrape", test_sequential_scrape),
            ("Test 2: Concurrent Scrape", test_concurrent_scrape),
            ("Test 3: Fallback Chain", test_fallback_chain),
            ("Test 4: Screenshot Load", test_screenshot_load),
            ("Test 5: Map Speed", test_map_speed),
            ("Test 6: Pool Saturation", test_pool_saturation),
        ]

        for name, test_fn in tests:
            print(f"Running {name}...")
            start = time.time()
            result = await test_fn(client, base_url, headers)
            elapsed = time.time() - start
            print(result.summary())
            if result.errors:
                for err in result.errors[:3]:
                    print(f"    Error: {err}")
            print(f"    Total time: {elapsed:.1f}s\n")
            results.append(result)

        # Final health check
        print("Post-test health check...")
        health = await test_health(client, base_url)
        print(f"  Status: {health.get('status')}")
        print(f"  Browser pool: {health.get('browser_pool', {})}")

        # Summary
        total_requests = sum(r.total_requests for r in results)
        total_success = sum(r.successful for r in results)
        total_failed = sum(r.failed for r in results)
        all_latencies = [l for r in results for l in r.latencies_ms]

        print(f"\n{'='*60}")
        print(f"  SUMMARY")
        print(f"{'='*60}")
        print(f"  Total requests: {total_requests}")
        print(f"  Successful:     {total_success} ({total_success/total_requests*100:.0f}%)")
        print(f"  Failed:         {total_failed}")
        print(f"  Avg latency:    {statistics.mean(all_latencies):.0f}ms")
        print(f"  p50 latency:    {statistics.median(all_latencies):.0f}ms")
        p95_idx = int(len(sorted(all_latencies)) * 0.95)
        print(f"  p95 latency:    {sorted(all_latencies)[min(p95_idx, len(all_latencies)-1)]:.0f}ms")
        print(f"  Max latency:    {max(all_latencies):.0f}ms")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
