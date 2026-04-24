from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from fastapi import HTTPException


def validate_url(url: str) -> str:
    """Validate URL is http(s) and not targeting private/internal networks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, f"Invalid URL scheme: {parsed.scheme}. Only http/https allowed.")
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(400, "Invalid URL: no hostname")
    # Block private IPs
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise HTTPException(400, "URLs targeting private/internal networks are not allowed")
    except ValueError:
        # hostname is a domain name, not an IP — check for localhost
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            raise HTTPException(400, "URLs targeting localhost are not allowed")
    return url
