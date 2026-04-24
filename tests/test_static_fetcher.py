from __future__ import annotations

import pytest
import respx
import httpx

from app.services.static_fetcher import static_fetch

REAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body>
    <main>
        <h1>Hello World</h1>
        <p>This is a real HTML page with enough content to not look JS-rendered.
        It has paragraphs and headings and all sorts of text to make it realistic.</p>
    </main>
</body>
</html>"""

JS_HEAVY_HTML = """<!DOCTYPE html>
<html><head></head>
<body></body>
<script src="a.js"></script>
<script src="b.js"></script>
<script src="c.js"></script>
<script src="d.js"></script>
</html>"""


@pytest.mark.asyncio
@respx.mock
async def test_static_fetch_success():
    """200 with real HTML returns dict."""
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            text=REAL_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    result = await static_fetch("https://example.com/page")
    assert result is not None
    assert result["status_code"] == 200
    assert "Hello World" in result["html"]
    assert result["final_url"] == "https://example.com/page"


@pytest.mark.asyncio
@respx.mock
async def test_static_fetch_js_page_returns_none():
    """Empty body + 4 script tags returns None."""
    respx.get("https://spa-app.com/").mock(
        return_value=httpx.Response(
            200,
            text=JS_HEAVY_HTML,
            headers={"content-type": "text/html"},
        )
    )
    result = await static_fetch("https://spa-app.com/")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_static_fetch_404():
    """Non-200 returns None."""
    respx.get("https://example.com/missing").mock(
        return_value=httpx.Response(404, text="Not Found", headers={"content-type": "text/html"})
    )
    result = await static_fetch("https://example.com/missing")
    assert result is None
