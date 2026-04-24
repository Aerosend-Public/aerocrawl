"""PDF extraction — pymupdf primary, Gemini 2.5 Flash fallback for scans.

Detection is three-layer (URL → Content-Type → magic bytes). Magic bytes
are authoritative because (a) servers lie about Content-Type and (b) arXiv
and EDGAR routinely 302 to PDFs from HTML-looking URLs.

pymupdf is AGPL-3.0 — fine for internal VPS use. If Aerocrawl ever gets
distributed or sold as SaaS, swap primary to pdfplumber + pypdf.
"""
from __future__ import annotations

import base64
import io
from typing import Optional, Tuple

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()

_PDF_MAGIC = b"%PDF-"
_DEFAULT_TIMEOUT = 60.0
_MIN_CHARS_PER_PAGE = 100  # below this average → probably a scan, use Gemini
_MAX_PAGES_FOR_GEMINI = 100  # beyond this, refuse — too expensive


def looks_like_pdf_url(url: str) -> bool:
    """Cheap URL-based heuristic, not authoritative."""
    return url.lower().split("?")[0].endswith(".pdf")


def is_pdf_bytes(data: bytes) -> bool:
    """Definitive check — does this byte stream start with %PDF-?"""
    return data[:5] == _PDF_MAGIC


async def fetch_pdf_bytes(url: str, timeout_s: float = _DEFAULT_TIMEOUT) -> Optional[Tuple[bytes, str]]:
    """Fetch a URL's bytes if it's a PDF. Returns (bytes, final_url) or None."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Aerocrawl/3.0"})
    except Exception as exc:
        logger.debug("pdf: fetch failed", url=url, error=str(exc))
        return None
    if resp.status_code != 200:
        return None
    content = resp.content
    ct = (resp.headers.get("content-type") or "").lower()
    if "application/pdf" not in ct and not is_pdf_bytes(content):
        return None
    return content, str(resp.url)


def _extract_with_pymupdf(pdf_bytes: bytes) -> Tuple[str, int, list]:
    """Extract text + tables with pymupdf. Returns (text, page_count, tables)."""
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.warning("pdf: pymupdf not installed")
        return "", 0, []

    tables: list = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.warning("pdf: pymupdf open failed", error=str(exc))
        return "", 0, []
    try:
        page_count = doc.page_count
        text_parts: list[str] = []
        for page in doc:
            text_parts.append(page.get_text())
        text = "\n\n".join(text_parts)
    finally:
        doc.close()
    return text, page_count, tables


def _extract_tables_with_pdfplumber(pdf_bytes: bytes) -> list:
    """Use pdfplumber specifically for tables — pymupdf's table extraction is weaker."""
    try:
        import pdfplumber
    except ImportError:
        return []
    tables: list = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                for t in page.extract_tables() or []:
                    # Filter noise — need ≥2 rows and ≥2 columns
                    if len(t) >= 2 and max((len(r) for r in t), default=0) >= 2:
                        tables.append({"page": page_num, "rows": t})
    except Exception as exc:
        logger.debug("pdf: pdfplumber failed", error=str(exc))
    return tables


async def _extract_with_gemini(pdf_bytes: bytes) -> Optional[str]:
    """Send PDF bytes directly to Gemini 2.5 Flash. Handles scans + complex layouts."""
    keys = settings.gemini_key_list
    if not keys:
        logger.debug("pdf: no gemini keys configured, cannot fall back")
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("pdf: google-genai not installed")
        return None

    try:
        client = genai.Client(api_key=keys[0])
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                "Extract all text from this PDF. Preserve headings as # markdown, "
                "lists as bullets, and tables as markdown tables. Output markdown only — "
                "no preamble, no explanations, no closing remarks.",
            ],
        )
        return (response.text or "").strip()
    except Exception as exc:
        logger.warning("pdf: gemini extraction failed", error=str(exc))
        return None


async def extract_pdf(url: str, pdf_bytes: Optional[bytes] = None) -> Optional[dict]:
    """Full PDF extraction pipeline.

    1. Fetch bytes (if not provided)
    2. pymupdf extraction
    3. If text is too sparse (scan PDF) → Gemini fallback
    4. pdfplumber for tables

    Returns dict with {markdown, html, final_url, status_code, pages, tables}
    or None on failure.
    """
    final_url = url
    if pdf_bytes is None:
        fetched = await fetch_pdf_bytes(url)
        if not fetched:
            return None
        pdf_bytes, final_url = fetched

    if not is_pdf_bytes(pdf_bytes):
        return None

    text, page_count, _ = _extract_with_pymupdf(pdf_bytes)

    # If avg chars/page is very low, this is a scan — go to Gemini
    avg_chars = (len(text) / page_count) if page_count else 0
    used_gemini = False
    if avg_chars < _MIN_CHARS_PER_PAGE and page_count <= _MAX_PAGES_FOR_GEMINI:
        logger.info("pdf: sparse text, falling back to Gemini", avg_chars=avg_chars, pages=page_count)
        gemini_text = await _extract_with_gemini(pdf_bytes)
        if gemini_text and len(gemini_text) > len(text):
            text = gemini_text
            used_gemini = True

    tables = _extract_tables_with_pdfplumber(pdf_bytes)

    if not text.strip() and not tables:
        return None

    # Append tables as markdown
    if tables:
        text += "\n\n## Tables\n\n"
        for tbl in tables:
            text += f"### Page {tbl['page']}\n\n"
            rows = tbl["rows"]
            if rows:
                header = rows[0]
                text += "| " + " | ".join((c or "").replace("\n", " ") for c in header) + " |\n"
                text += "|" + "|".join(["---"] * len(header)) + "|\n"
                for row in rows[1:]:
                    text += "| " + " | ".join((c or "").replace("\n", " ") for c in row) + " |\n"
                text += "\n"

    return {
        "markdown": text,
        "html": "",
        "final_url": final_url,
        "status_code": 200,
        "pages": page_count,
        "table_count": len(tables),
        "extractor": "gemini" if used_gemini else "pymupdf",
    }
