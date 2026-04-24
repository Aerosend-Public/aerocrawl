"""Shared httpx AsyncClient — persistent connection pool.

Saves ~20-50ms of TLS handshake per request by keeping connections open to
frequently-hit hosts. HTTP/2 multiplexing stacks concurrent requests to the
same host over one TCP connection — large win for crawls, search result
follow-ups, and multi-URL extract.

Limits:
  - max_connections=100 (VPS is fine; adjust if we scale out)
  - max_keepalive_connections=50 per host
  - keepalive_expiry=30s (drop idle sockets after 30s)
  - http2=True (faster for hosts that support it; falls back to 1.1)
"""
from __future__ import annotations

from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None


async def get_shared_client() -> httpx.AsyncClient:
    """Lazy-create and return the process-wide shared httpx AsyncClient."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            http2=True,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=50,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
    return _client


async def close_shared_client() -> None:
    """Close the shared client on shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
