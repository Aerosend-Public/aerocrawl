from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.mapper import discover_urls

ROBOTS_TXT = """User-agent: *
Disallow: /admin/
Sitemap: https://example.com/sitemap.xml
"""

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>https://example.com/blog/post1</loc></url>
  <url><loc>https://example.com/blog/post2</loc></url>
</urlset>
"""

HOMEPAGE_HTML = """<!DOCTYPE html>
<html>
<head><title>Example</title></head>
<body>
  <a href="/about">About</a>
  <a href="/blog/post1">Post 1</a>
  <a href="/contact">Contact</a>
</body>
</html>
"""


@pytest.mark.asyncio
@respx.mock
async def test_discover_urls_from_sitemap():
    """Mock robots.txt with Sitemap directive, sitemap.xml with 3 URLs, homepage with extra links."""
    respx.get("https://example.com/robots.txt").mock(
        return_value=Response(200, text=ROBOTS_TXT)
    )
    respx.get("https://example.com/sitemap.xml").mock(
        return_value=Response(200, text=SITEMAP_XML)
    )
    respx.get("https://example.com/").mock(
        return_value=Response(200, text=HOMEPAGE_HTML)
    )

    result = await discover_urls("https://example.com/")

    urls = result["urls"]
    assert result["total"] > 0

    # All 3 sitemap URLs should be found
    assert "https://example.com/page1" in urls
    assert "https://example.com/blog/post1" in urls
    assert "https://example.com/blog/post2" in urls

    # Page links should also be found (deduped)
    assert "https://example.com/about" in urls
    assert "https://example.com/contact" in urls

    # blog/post1 appears in both sitemap and page links — should be deduped
    assert urls.count("https://example.com/blog/post1") == 1

    # Source counts
    assert result["sources"]["sitemap"] == 3
    assert result["sources"]["page_links"] >= 2  # /about and /contact at minimum


@pytest.mark.asyncio
@respx.mock
async def test_discover_urls_with_include_filter():
    """Same setup, include_paths=["^/blog/"], verify only blog URLs returned."""
    respx.get("https://example.com/robots.txt").mock(
        return_value=Response(200, text=ROBOTS_TXT)
    )
    respx.get("https://example.com/sitemap.xml").mock(
        return_value=Response(200, text=SITEMAP_XML)
    )
    respx.get("https://example.com/").mock(
        return_value=Response(200, text=HOMEPAGE_HTML)
    )

    result = await discover_urls(
        "https://example.com/",
        include_paths=["^/blog/"],
    )

    urls = result["urls"]
    assert result["total"] > 0

    # Only blog URLs should be present
    for url in urls:
        assert "/blog/" in url, f"Non-blog URL found: {url}"

    assert "https://example.com/blog/post1" in urls
    assert "https://example.com/blog/post2" in urls

    # Non-blog URLs should be filtered out
    assert "https://example.com/page1" not in urls
    assert "https://example.com/about" not in urls
    assert "https://example.com/contact" not in urls
