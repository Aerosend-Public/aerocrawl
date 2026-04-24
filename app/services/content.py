from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from readability import Document


def extract_main_content(html: str) -> str:
    """Extract main content using readability-lxml. Fallback: strip nav/footer/header/aside."""
    try:
        doc = Document(html)
        content = doc.summary()
        if content and len(content.strip()) > 50:
            return content
    except Exception:
        pass

    # Fallback: manual extraction
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["nav", "footer", "header", "aside", "script", "style"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.find("body")
    if main:
        return str(main)
    return str(soup)


def html_to_markdown(html: str, only_main_content: bool = False) -> str:
    """Convert HTML to Markdown. Optionally extract main content first."""
    if only_main_content:
        html = extract_main_content(html)

    # Strip script and style tags before conversion
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    cleaned_html = str(soup)

    result = md(cleaned_html, heading_style="ATX", strip=["img"])
    # Clean up triple+ newlines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def extract_metadata(html: str, source_url: str, status_code: int) -> dict:
    """Extract page metadata from HTML."""
    soup = BeautifulSoup(html, "lxml")

    def _meta_content(attrs: dict) -> str:
        tag = soup.find("meta", attrs=attrs)
        return tag.get("content", "") if tag else ""

    title_tag = soup.find("title")
    html_tag = soup.find("html")

    return {
        "title": title_tag.get_text(strip=True) if title_tag else "",
        "description": _meta_content({"name": "description"}),
        "language": html_tag.get("lang", "") if html_tag else "",
        "og_title": _meta_content({"property": "og:title"}),
        "og_description": _meta_content({"property": "og:description"}),
        "og_image": _meta_content({"property": "og:image"}),
        "robots": _meta_content({"name": "robots"}),
        "status_code": status_code,
        "source_url": source_url,
    }


def extract_links(html: str, base_url: str, same_domain_only: bool = False) -> List[str]:
    """Extract all <a href> links, resolve relative, dedup, filter junk."""
    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc
    seen: set = set()
    links: list = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()

        # Filter junk
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # Resolve relative URLs
        absolute = urljoin(base_url, href)

        # Remove fragment
        absolute = absolute.split("#")[0]

        if not absolute:
            continue

        if same_domain_only:
            link_domain = urlparse(absolute).netloc
            if link_domain != base_domain:
                continue

        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)

    return links


@dataclass
class BlockResult:
    """Result of block-page detection."""
    blocked: bool
    block_type: str  # cloudflare_challenge, captcha, auth_wall, rate_limited, ip_blocked, empty_content
    detail: str


_CLOUDFLARE_SIGNATURES = [
    "Attention Required! | Cloudflare",
    "Just a moment...",
    "cf-browser-verification",
    "Enable JavaScript and cookies to continue",
    "Checking your browser before accessing",
    "cf-challenge-running",
]

_CAPTCHA_SIGNATURES = [
    "px-captcha",
    "Press & Hold to confirm you are",
    "Verify you are human",
    "datadome",
    "captcha-delivery.com",
    "geo.captcha-delivery.com",
    "recaptcha/api",
]

_AUTH_WALL_SIGNATURES = [
    ("Sign Up | LinkedIn", "title"),
    ("Log in | LinkedIn", "title"),
    ("/accounts/login/", "url"),
    ("/authwall", "url"),
    ("Log in to Instagram", "content"),
    ("Page isn't available", "content"),  # Instagram profile not found / auth wall
    ("Log In | Facebook", "title"),
    ("Create an account or log in to Facebook", "content"),
]

_IP_BLOCKED_SIGNATURES = [
    "blocked by network security",
    "whoa there, pardner",
    "Your request has been blocked due to a network policy",
    "Access to this page has been denied",
    "something went wrong, but don",  # X/Twitter error (handles curly apostrophes)
]


def detect_block(
    html: str = "",
    markdown: str = "",
    status_code: int = 0,
    final_url: str = "",
    title: str = "",
) -> Optional[BlockResult]:
    """Detect if scraped content is a block page, CAPTCHA, or auth wall.

    Returns BlockResult if blocked, None if content looks legitimate.

    On long content (>3000 chars markdown), skip keyword matching — it's
    probably a real article about blocking/captchas rather than an actual
    block page. Status-code checks still apply.
    """
    # Rate limited
    if status_code == 429:
        return BlockResult(blocked=True, block_type="rate_limited", detail="Server returned 429 Too Many Requests")

    # Long markdown → assume real content. Kills the Wikipedia false positive
    # where "Verify you are human" appears as example text in an article body.
    long_content = len(markdown or "") > 3000

    text = (html + " " + markdown + " " + title).lower()
    url_lower = final_url.lower()

    if not long_content:
        # IP blocked (check before Cloudflare since some CF blocks are IP-based)
        for sig in _IP_BLOCKED_SIGNATURES:
            if sig.lower() in text:
                return BlockResult(blocked=True, block_type="ip_blocked", detail=f"Matched: {sig}")

        # Cloudflare challenge
        for sig in _CLOUDFLARE_SIGNATURES:
            if sig.lower() in text:
                return BlockResult(blocked=True, block_type="cloudflare_challenge", detail=f"Matched: {sig}")

        # CAPTCHA
        for sig in _CAPTCHA_SIGNATURES:
            if sig.lower() in text:
                return BlockResult(blocked=True, block_type="captcha", detail=f"Matched: {sig}")

    # Auth wall (title/url match stays even on long content — valid signal)
    for sig, check_type in _AUTH_WALL_SIGNATURES:
        sig_lower = sig.lower()
        if check_type == "title" and sig_lower in title.lower():
            return BlockResult(blocked=True, block_type="auth_wall", detail=f"Auth wall: {sig}")
        if check_type == "url" and sig_lower in url_lower:
            return BlockResult(blocked=True, block_type="auth_wall", detail=f"Redirected to login: {sig}")
        if check_type == "content" and sig_lower in text and not long_content:
            return BlockResult(blocked=True, block_type="auth_wall", detail=f"Auth wall: {sig}")

    # LinkedIn-specific status code 999 (custom "blocked" response)
    if status_code == 999:
        return BlockResult(blocked=True, block_type="auth_wall", detail="LinkedIn returned status 999 (auth required)")

    # Empty content (after all other checks) — regardless of status code
    clean_text = re.sub(r"\s+", "", markdown)
    if len(clean_text) < 30:
        return BlockResult(blocked=True, block_type="empty_content", detail=f"Response had only {len(clean_text)} chars of useful content")

    return None


def looks_like_js_rendered(html: str) -> bool:
    """Detect if page likely requires JS rendering."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    body_text = body.get_text(strip=True) if body else ""
    script_count = len(soup.find_all("script"))

    if len(body_text) < 100 and script_count > 3:
        return True

    noscript = soup.find("noscript")
    if noscript and len(noscript.get_text(strip=True)) > 50:
        return True

    return False
