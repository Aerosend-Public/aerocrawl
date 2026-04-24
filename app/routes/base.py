"""Route handler protocol + normalized result shape."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class RouteResult:
    """Result from a route handler — same shape the main scraper returns."""
    markdown: str = ""
    html: str = ""
    final_url: str = ""
    status_code: int = 200
    metadata: dict = field(default_factory=dict)
    # Provenance
    route_name: str = ""
    raw_data: Optional[dict] = None  # original API response if useful


@runtime_checkable
class RouteHandler(Protocol):
    """A site-specific route handler.

    Implementations must be safe to call concurrently and must not raise —
    on failure, return None from fetch() and let the caller fall back to
    the 9-step scrape chain.
    """

    name: str
    description: str

    def matches(self, url: str) -> bool:
        """Cheap check: does this handler claim this URL?"""

    async def fetch(self, url: str, only_main_content: bool = True) -> Optional[RouteResult]:
        """Fetch via API. Return None on any failure (auth, rate limit, 404, etc)."""
