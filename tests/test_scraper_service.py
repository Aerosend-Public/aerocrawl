from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper import ScrapeResult, scrape_url

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>Test</title><meta name="description" content="desc"></head>
<body><main><h1>Hello</h1><p>Content here</p></main></body>
</html>"""


@pytest.mark.asyncio
@patch("app.services.scraper.static_fetch")
@patch("app.services.scraper._scrape_with_playwright")
async def test_scrape_uses_static_first(mock_playwright, mock_static):
    """Static success means no browser_pool.acquire."""
    mock_static.return_value = {
        "html": SAMPLE_HTML,
        "status_code": 200,
        "final_url": "https://example.com",
    }

    result = await scrape_url("https://example.com", formats=["markdown"])
    assert result.success is True
    assert result.scrape_method == "static"
    assert "Hello" in result.markdown
    mock_playwright.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.scraper.static_fetch")
@patch("app.services.scraper._scrape_with_playwright")
async def test_scrape_falls_back_to_playwright(mock_playwright, mock_static):
    """Static returns None → playwright called."""
    mock_static.return_value = None
    mock_playwright.return_value = (SAMPLE_HTML, "https://example.com", 200, "", [])

    result = await scrape_url("https://example.com", formats=["markdown"])
    assert result.success is True
    assert result.scrape_method == "playwright"
    mock_playwright.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.scraper.static_fetch")
@patch("app.services.scraper._scrape_with_playwright")
async def test_scrape_forces_playwright_for_actions(mock_playwright, mock_static):
    """Actions present → static not called, straight to playwright."""
    mock_playwright.return_value = (SAMPLE_HTML, "https://example.com", 200, "", [{"action": "click", "success": True}])

    result = await scrape_url(
        "https://example.com",
        formats=["markdown"],
        actions=[{"type": "click", "selector": "button"}],
    )
    assert result.success is True
    assert result.scrape_method == "playwright"
    mock_static.assert_not_called()
    mock_playwright.assert_called_once()
