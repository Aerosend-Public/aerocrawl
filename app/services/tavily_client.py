from __future__ import annotations

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()

TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


class TavilyClient:
    """Tavily Extract API client with round-robin key rotation."""

    def __init__(self, api_keys: list[str] | None = None) -> None:
        self._api_keys = api_keys or settings.tavily_key_list
        self._key_index = 0

    def _next_key(self) -> str | None:
        if not self._api_keys:
            return None
        key = self._api_keys[self._key_index % len(self._api_keys)]
        self._key_index += 1
        return key

    async def extract(self, url: str) -> dict | None:
        """Extract content from a URL via Tavily Extract API.

        Returns dict with keys: html, markdown, final_url, status_code
        Returns None if Tavily is not configured or extraction fails.
        """
        api_key = self._next_key()
        if not api_key:
            logger.debug("tavily: no API keys configured")
            return None

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    TAVILY_EXTRACT_URL,
                    json={
                        "api_key": api_key,
                        "urls": [url],
                    },
                )

                if resp.status_code != 200:
                    logger.warning("tavily: non-200 response", status=resp.status_code, url=url)
                    return None

                data = resp.json()
                results = data.get("results", [])
                failed = data.get("failed_results", [])

                if failed:
                    for f in failed:
                        if f.get("url") == url:
                            logger.debug("tavily: extraction failed", url=url, error=f.get("error"))
                            return None

                if not results:
                    logger.debug("tavily: no results", url=url)
                    return None

                result = results[0]
                raw_content = result.get("raw_content", "")

                if not raw_content or len(raw_content.strip()) < 30:
                    logger.debug("tavily: empty content", url=url)
                    return None

                return {
                    "html": "",  # Tavily returns markdown, not HTML
                    "markdown": raw_content,
                    "final_url": result.get("url", url),
                    "status_code": 200,
                }

        except Exception as exc:
            logger.debug("tavily: request error", url=url, error=str(exc))
            return None


# Module-level singleton
_tavily_client: TavilyClient | None = None


def get_tavily_client() -> TavilyClient:
    """Get or create the Tavily client singleton."""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient()
    return _tavily_client
