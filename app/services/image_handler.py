"""Image extraction and optional Gemini Vision captioning.

Default behaviour: extract `{src, alt, caption}` triples from the page without
describing images. Vision describe is opt-in and rate-capped.

Heuristics classify images as "informational" (chart/diagram/pricing) vs
"decorative" (logo/icon/spacer). Only informational images with weak or
missing alt text are candidates for Gemini Vision description.
"""
from __future__ import annotations

import base64
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

from app.config import settings

logger = structlog.get_logger()

_DECORATIVE_PATH_HINTS = re.compile(
    r"(icon|logo|avatar|sprite|spacer|pixel|1x1|badge|emoji|favicon)",
    re.IGNORECASE,
)
_VISION_KEYWORDS = re.compile(
    r"(chart|pricing|comparison|infographic|diagram|benchmark|graph|plot|table)",
    re.IGNORECASE,
)
_MIN_INFORMATIONAL_WIDTH = 300
_MIN_INFORMATIONAL_HEIGHT = 200
_MAX_VISION_IMAGES_PER_PAGE = 5


def _extract_int(val) -> int:
    if val is None:
        return 0
    try:
        return int(str(val).strip().split("px")[0].split("%")[0])
    except (ValueError, AttributeError):
        return 0


def extract_image_triples(html: str, base_url: str) -> list[dict]:
    """Return {src, alt, caption, width, height, informational} for each <img>."""
    soup = BeautifulSoup(html, "lxml")
    triples: list[dict] = []
    seen_srcs: set[str] = set()

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        # Resolve relative URLs
        absolute = urljoin(base_url, src)
        if absolute in seen_srcs:
            continue
        seen_srcs.add(absolute)

        alt = (img.get("alt") or "").strip()
        width = _extract_int(img.get("width"))
        height = _extract_int(img.get("height"))

        # Look for figcaption in parent <figure>
        caption = ""
        parent = img.find_parent("figure")
        if parent:
            cap_el = parent.find("figcaption")
            if cap_el:
                caption = cap_el.get_text(strip=True)

        # Classify
        is_informational = _is_informational(absolute, alt, width, height, caption)

        triples.append({
            "src": absolute,
            "alt": alt,
            "caption": caption,
            "width": width,
            "height": height,
            "informational": is_informational,
        })
    return triples


def _is_informational(src: str, alt: str, width: int, height: int, caption: str) -> bool:
    """Heuristic: is this an informational image worth keeping?"""
    # Exclude by path hints
    if _DECORATIVE_PATH_HINTS.search(src):
        return False
    # Figure with caption → informational
    if caption:
        return True
    # Size signal
    if width >= _MIN_INFORMATIONAL_WIDTH and height >= _MIN_INFORMATIONAL_HEIGHT:
        return True
    # Long alt text → informational
    if len(alt) >= 20:
        return True
    return False


def _should_describe(triple: dict, surrounding_text: str = "") -> bool:
    """Per design Q8: informational AND (empty/short alt) AND (keyword match)."""
    if not triple["informational"]:
        return False
    if len(triple["alt"]) > 5:
        return False
    target = f"{triple['src']} {surrounding_text}"
    return bool(_VISION_KEYWORDS.search(target))


async def describe_image_with_gemini(image_url: str) -> Optional[str]:
    """Fetch an image and send to Gemini 2.5 Flash for structured description.

    Returns a short description string or None on failure.
    """
    keys = settings.gemini_key_list
    if not keys:
        return None

    # Download the image
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(image_url, headers={"User-Agent": "Aerocrawl/3.0"})
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    img_bytes = resp.content
    if len(img_bytes) > 10 * 1024 * 1024:  # cap at 10 MB
        return None

    ct = resp.headers.get("content-type") or "image/jpeg"

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None

    try:
        client = genai.Client(api_key=keys[0])
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type=ct),
                "Describe this image in one paragraph. If it contains data "
                "(chart, table, pricing, comparison), extract the data values "
                "as a markdown table. Otherwise describe what's shown. "
                "No preamble, no closing remark.",
            ],
        )
        return (response.text or "").strip()
    except Exception as exc:
        logger.debug("image: gemini describe failed", url=image_url, error=str(exc))
        return None


async def describe_candidates(
    triples: list[dict],
    max_images: int = _MAX_VISION_IMAGES_PER_PAGE,
) -> list[dict]:
    """Invoke Gemini Vision on the top N candidates. Mutates triples in place.

    Returns the same list with `description` field added to images that got described.
    """
    candidates = [t for t in triples if _should_describe(t)][:max_images]
    for t in candidates:
        desc = await describe_image_with_gemini(t["src"])
        if desc:
            t["description"] = desc
    return triples


async def extract_page_via_screenshot(
    screenshot_b64: str,
    url: str,
    structured_prompt: Optional[str] = None,
) -> Optional[dict]:
    """Visual mode: send a full-page screenshot to Gemini for structured extraction.

    More reliable than HTML parsing for JS-heavy SPAs, canvas/SVG pricing tables,
    and image-rendered comparison tables.
    """
    keys = settings.gemini_key_list
    if not keys or not screenshot_b64:
        return None
    try:
        img_bytes = base64.b64decode(screenshot_b64)
    except Exception:
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None

    prompt = structured_prompt or (
        "Extract the full content of this page into clean markdown. "
        "Preserve headings, lists, tables, and any pricing or comparison data. "
        "Include all visible text. No preamble."
    )

    try:
        client = genai.Client(api_key=keys[0])
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                prompt,
            ],
        )
        return {
            "markdown": (response.text or "").strip(),
            "final_url": url,
            "status_code": 200,
            "extractor": "gemini-vision",
        }
    except Exception as exc:
        logger.debug("image: visual mode failed", url=url, error=str(exc))
        return None
