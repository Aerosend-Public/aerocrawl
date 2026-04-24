"""Synchronous schema-first extraction — Firecrawl /extract v2 equivalent.

Takes markdown + JSON schema + prompt, runs Gemini, returns validated dict.
Retries once on malformed JSON. On persistent failure returns None; caller
surfaces `extract_error` without losing the scrape result.
"""
from __future__ import annotations

import itertools
import json
import re
from typing import Any, Optional

import structlog

from app.config import settings

logger = structlog.get_logger()

_key_cycle = None
_MAX_INPUT_CHARS = 250_000  # ~60k tokens — keeps Gemini 2.5 Flash fast


def _next_key() -> str:
    global _key_cycle
    keys = settings.gemini_key_list
    if not keys:
        raise RuntimeError("No Gemini API keys configured (set GEMINI_API_KEYS)")
    if _key_cycle is None:
        _key_cycle = itertools.cycle(keys)
    return next(_key_cycle)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t.strip()


def _validates_schema(value: Any, schema: dict) -> bool:
    """Cheap shape-level check: top-level type + required keys present.

    Deliberately NOT full JSON-Schema validation — Gemini usually gets
    structural right if we give it a clear schema; deeper validation is
    the caller's responsibility.
    """
    if not schema:
        return True
    t = schema.get("type")
    if t == "object" and not isinstance(value, dict):
        return False
    if t == "array" and not isinstance(value, list):
        return False
    if t == "object" and schema.get("required"):
        return all(k in value for k in schema["required"])
    return True


async def extract_structured(
    markdown: str,
    schema: dict,
    prompt: str = "",
    source_url: str = "",
    max_retries: int = 1,
) -> Optional[dict | list]:
    """Run Gemini to extract structured data. Returns parsed JSON or None."""
    if not markdown:
        return None

    # Truncate oversized input — Gemini 2.5 Flash handles 1M context but
    # larger inputs hurt latency and rarely add signal for research extraction.
    if len(markdown) > _MAX_INPUT_CHARS:
        markdown = markdown[:_MAX_INPUT_CHARS] + "\n\n[... truncated ...]"

    try:
        from google import genai
    except ImportError:
        logger.warning("extract_sync: google-genai not installed")
        return None

    schema_blob = json.dumps(schema, indent=2) if schema else "{}"
    src_line = f"Source URL: {source_url}\n\n" if source_url else ""

    system = (
        "You extract structured data from web content. Respond with ONLY a "
        "single JSON object matching the schema. No preamble, no explanation, "
        "no code fences, no closing remark."
    )
    user = (
        f"{src_line}"
        f"Instructions: {prompt or 'Extract the requested fields from the content.'}\n\n"
        f"JSON Schema:\n{schema_blob}\n\n"
        f"Content:\n{markdown}\n\n"
        f"Return JSON only."
    )

    last_error = ""
    for attempt in range(max_retries + 1):
        api_key = _next_key()
        try:
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=f"{system}\n\n{user}",
            )
            text = _strip_fences(resp.text or "")
            parsed = json.loads(text)
            if not _validates_schema(parsed, schema):
                last_error = "response did not match schema shape"
                logger.debug("extract_sync: schema mismatch", attempt=attempt)
                continue
            return parsed
        except json.JSONDecodeError as exc:
            last_error = f"json decode: {exc}"
            logger.debug("extract_sync: invalid json", attempt=attempt)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("extract_sync: gemini call failed", attempt=attempt, error=last_error)

    logger.info("extract_sync: giving up", last_error=last_error)
    return None
