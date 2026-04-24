from __future__ import annotations

from app.services.search_scraper import _parse_brave_results


BRAVE_HTML_FIXTURE = """
<html><body>
<div id="results">
  <div class="snippet" data-type="web">
    <div class="result-wrapper">
      <div class="result-content">
        <a class="l1" href="https://www.aerosend.io/">
          <div class="title">Email Deliverability Platform | Aerosend</div>
        </a>
        <div class="generic-snippet">
          <div class="content">Aerosend helps you send high-deliverability emails.</div>
        </div>
      </div>
    </div>
  </div>
  <div class="snippet" data-type="web">
    <div class="result-wrapper">
      <div class="result-content">
        <a class="l1" href="https://www.aerosend.io/blog">
          <div class="title">Aerosend Blog | Growth Tips</div>
        </a>
        <div class="generic-snippet">
          <div class="content">Read the latest growth strategies and tips.</div>
        </div>
      </div>
    </div>
  </div>
  <div class="snippet" data-type="web">
    <div class="result-wrapper">
      <div class="result-content">
        <a class="l1" href="https://example.com/third">
          <div class="title">Third Result</div>
        </a>
        <div class="generic-snippet">
          <div class="content">Description of third result.</div>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>
"""


def test_parse_brave_results_extracts_all_fields():
    results = _parse_brave_results(BRAVE_HTML_FIXTURE, count=10)
    assert len(results) == 3
    assert results[0]["title"] == "Email Deliverability Platform | Aerosend"
    assert results[0]["url"] == "https://www.aerosend.io/"
    assert "high-deliverability" in results[0]["description"]


def test_parse_brave_results_respects_count():
    results = _parse_brave_results(BRAVE_HTML_FIXTURE, count=1)
    assert len(results) == 1


def test_parse_brave_results_empty_html():
    results = _parse_brave_results("<html><body></body></html>", count=10)
    assert results == []


from app.services.search_scraper import _parse_ddg_results


DDG_HTML_FIXTURE = """
<html><body>
<div id="links">
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.aerosend.io%2F&amp;rut=abc123">
        Email Deliverability Platform | Aerosend
      </a>
      <a class="result__snippet">Aerosend helps you send high-deliverability emails.</a>
    </div>
  </div>
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.g2.com%2Fproducts%2Faerosend%2Freviews&amp;rut=def456">
        Aerosend Reviews | G2
      </a>
      <a class="result__snippet">Read real user reviews of Aerosend on G2.</a>
    </div>
  </div>
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fthird&amp;rut=ghi789">
        Third DDG Result
      </a>
      <a class="result__snippet">Description of third result.</a>
    </div>
  </div>
</div>
</body></html>
"""


def test_parse_ddg_results_extracts_all_fields():
    results = _parse_ddg_results(DDG_HTML_FIXTURE, count=10)
    assert len(results) == 3
    assert results[0]["title"] == "Email Deliverability Platform | Aerosend"
    assert results[0]["url"] == "https://www.aerosend.io/"
    assert "high-deliverability" in results[0]["description"]


def test_parse_ddg_results_decodes_redirect_urls():
    results = _parse_ddg_results(DDG_HTML_FIXTURE, count=10)
    assert results[1]["url"] == "https://www.g2.com/products/aerosend/reviews"


def test_parse_ddg_results_respects_count():
    results = _parse_ddg_results(DDG_HTML_FIXTURE, count=1)
    assert len(results) == 1


def test_parse_ddg_results_empty_html():
    results = _parse_ddg_results("<html><body></body></html>", count=10)
    assert results == []


import pytest
from unittest.mock import AsyncMock, patch

from app.services.search_scraper import search


@pytest.mark.asyncio
async def test_search_returns_brave_results_on_success():
    """When CF Worker returns valid Brave HTML, return brave_html results."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": 200,
        "body": BRAVE_HTML_FIXTURE,
        "final_url": "https://search.brave.com/search?q=test",
    }

    with patch("app.services.search_scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await search("test query", count=10, cf_proxy_url="https://proxy.example.com")

    assert result["success"] is True
    assert result["search_engine"] == "brave_html"
    assert result["result_count"] == 3
    assert len(result["results"]) == 3


@pytest.mark.asyncio
async def test_search_falls_back_to_ddg_when_brave_fails():
    """When Brave returns CAPTCHA/empty, fall back to DDG."""
    captcha_html = "<html><body>Please verify you are human captcha</body></html>"
    mock_brave_response = AsyncMock()
    mock_brave_response.status_code = 200
    mock_brave_response.json.return_value = {
        "status": 200,
        "body": captcha_html,
        "final_url": "https://search.brave.com/search?q=test",
    }

    mock_ddg_response = AsyncMock()
    mock_ddg_response.status_code = 200
    mock_ddg_response.json.return_value = {
        "status": 200,
        "body": DDG_HTML_FIXTURE,
        "final_url": "https://html.duckduckgo.com/html/?q=test",
    }

    with patch("app.services.search_scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_brave_response, mock_ddg_response]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await search("test query", count=10, cf_proxy_url="https://proxy.example.com")

    assert result["success"] is True
    assert result["search_engine"] == "duckduckgo_html"
    assert result["result_count"] == 3


@pytest.mark.asyncio
async def test_search_returns_failure_when_both_engines_fail():
    """When both Brave and DDG fail, return success=false."""
    empty_html = "<html><body></body></html>"
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": 200,
        "body": empty_html,
        "final_url": "https://search.brave.com/search?q=test",
    }

    with patch("app.services.search_scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await search("test query", count=10, cf_proxy_url="https://proxy.example.com")

    assert result["success"] is False
    assert result["error"] == "All search engines failed"
    assert result["results"] == []
