"""Redis scrape-result cache.

Step 0 of the fallback chain: before any network work, check whether we've
scraped this URL+options combo recently. Hit rate >25% is the target for a
research tool where the same URLs get hit 3-10× per week.

Design doc: docs/2026-04-24-v3-upgrade-plan.md
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import msgpack
import structlog
import zstandard as zstd

from app.config import settings

logger = structlog.get_logger()


# The shared app Redis client has decode_responses=True (for arq + string state).
# Cache values are zstd-compressed msgpack bytes — they can't be UTF-8 decoded.
# Use a dedicated binary-mode client for all cache ops.
_binary_redis = None


async def get_redis():
    """Lazy binary-mode Redis client. Separate from app.redis_client which
    decodes responses as UTF-8."""
    global _binary_redis
    if _binary_redis is None:
        from redis.asyncio import Redis
        _binary_redis = Redis.from_url(settings.REDIS_URL, decode_responses=False)
    return _binary_redis

CACHE_VERSION = "v1"
_DEFAULT_TTL = 86400  # 24 hours
_NEGATIVE_TTL = 1800  # 30 min for stable block types
_MAX_VALUE_BYTES = 2_097_152  # 2 MB post-compression

# Per-domain TTL overrides (matched against URL host, substring match).
_TTL_OVERRIDES: list[tuple[str, int]] = [
    ("reddit.com", 6 * 3600),
    ("ycombinator.com", 6 * 3600),
    ("news.", 6 * 3600),
    ("/blog/", 6 * 3600),
    ("docs.", 7 * 86400),
    ("developer.", 7 * 86400),
    ("/api-reference", 7 * 86400),
]

# Domains to skip caching entirely (auth-gated, session-dependent).
_NO_CACHE_DOMAINS: set[str] = {
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "facebook.com",
}

# Block types safe to negatively cache — stable across retries.
_STABLE_BLOCK_TYPES: set[str] = {
    "auth_wall",
    "404",
    "410",
    "dns_fail",
}

# URL query params stripped during normalization (don't affect content).
_TRACKING_PARAMS: set[str] = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "ref_src", "_ga", "_gl", "yclid", "igshid",
}

_zstd_compressor = zstd.ZstdCompressor(level=3)
_zstd_decompressor = zstd.ZstdDecompressor()


def _normalize_url(url: str) -> str:
    """Canonicalize a URL so trivially-different URLs hit the same cache key."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()

    # Strip default ports
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]

    # Sort query params + drop tracking params
    params = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in params if k.lower() not in _TRACKING_PARAMS]
    filtered.sort()
    query = urlencode(filtered)

    # Drop fragment
    return urlunparse((scheme, netloc, parsed.path, parsed.params, query, ""))


def _options_fingerprint(opts: dict) -> str:
    """Stable hash of cache-affecting request options.

    Only fields that change the bytes we return go here. Proxy, timeout_ms,
    and internal method choices are NOT included — a cache hit should bypass
    the chain regardless of which proxy was originally used.
    """
    relevant = {
        "formats": sorted(opts.get("formats") or ["markdown"]),
        "selector": opts.get("selector") or "",
        "only_main_content": bool(opts.get("only_main_content", True)),
        "actions": opts.get("actions") or [],
        "wait_for": opts.get("wait_for") or "networkidle",
    }
    blob = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build_key(url: str, opts: dict) -> str:
    """Build the primary cache key for a scrape request."""
    normalized = _normalize_url(url)
    url_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    opts_hash = _options_fingerprint(opts)
    return f"scrape:{CACHE_VERSION}:{url_hash}:{opts_hash}"


def _url_index_key(url: str) -> str:
    """Hint key — set of all option-hashes seen for this URL.

    Enables `DELETE /cache?url=...` to purge all variants in one pass.
    """
    normalized = _normalize_url(url)
    url_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"scrape:{CACHE_VERSION}:url:{url_hash}"


def _should_skip_host(url: str) -> bool:
    """True if this URL's domain is on the no-cache list."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(d in host for d in _NO_CACHE_DOMAINS)


def _ttl_for_url(url: str) -> int:
    """Determine TTL by matching URL host/path against override table."""
    target = url.lower()
    for pattern, ttl in _TTL_OVERRIDES:
        if pattern in target:
            return ttl
    return settings.CACHE_DEFAULT_TTL_SECONDS or _DEFAULT_TTL


def _looks_like_block_page(markdown: str) -> bool:
    """Sanity-check before caching 'successful' content — is this actually a block page?

    Length threshold is 50 chars (matches detect_block's "empty_content" signal).
    `example.com` renders to 167 chars of Markdown — legitimate small pages
    must NOT be rejected by the cache.
    """
    if not markdown or len(markdown.strip()) < 50:
        return True
    pattern = re.compile(
        r"just a moment|cf-challenge|attention required|enable javascript and cookies|"
        r"verify you are human|press & hold to confirm|blocked by network security",
        re.IGNORECASE,
    )
    return bool(pattern.search(markdown))


def _serialize(value: dict) -> Optional[bytes]:
    """msgpack + zstd. Returns None if over size cap."""
    try:
        packed = msgpack.packb(value, use_bin_type=True)
        compressed = _zstd_compressor.compress(packed)
        if len(compressed) > _MAX_VALUE_BYTES:
            logger.debug(
                "cache: refusing oversized value",
                raw=len(packed),
                compressed=len(compressed),
            )
            return None
        return compressed
    except Exception as exc:
        logger.warning("cache: serialize failed", error=str(exc))
        return None


def _deserialize(raw: bytes) -> Optional[dict]:
    try:
        return msgpack.unpackb(_zstd_decompressor.decompress(raw), raw=False)
    except Exception as exc:
        logger.warning("cache: deserialize failed", error=str(exc))
        return None


def _result_to_dict(result: Any) -> dict:
    """Convert a ScrapeResult dataclass or dict into a cacheable dict."""
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return dict(result)
    raise TypeError(f"Cannot cache {type(result).__name__}")


async def get(url: str, opts: dict) -> Optional[dict]:
    """Fetch a cached scrape result. Returns None on miss or if cache disabled."""
    if not settings.CACHE_ENABLED:
        return None
    if _should_skip_host(url):
        return None
    try:
        redis = await get_redis()
    except Exception as exc:
        logger.debug("cache: redis unavailable", error=str(exc))
        return None
    key = build_key(url, opts)
    try:
        raw = await redis.get(key)
    except Exception as exc:
        logger.debug("cache: get failed", key=key, error=str(exc))
        return None
    if raw is None:
        return None
    # Binary client returns bytes; defensive coerce if anything else sneaks in
    if isinstance(raw, str):
        raw = raw.encode("latin-1")
    value = _deserialize(raw)
    if value is None:
        return None
    stored_at = value.pop("_cached_at", time.time())
    value["cached"] = True
    value["cache_age_seconds"] = max(0, int(time.time() - stored_at))
    return value


async def set(url: str, opts: dict, result: Any) -> bool:
    """Store a scrape result. Returns True if stored, False if skipped."""
    if not settings.CACHE_ENABLED:
        return False
    if _should_skip_host(url):
        return False

    payload = _result_to_dict(result)

    # Only cache stable failures — skip transient CF challenges, timeouts, etc.
    if not payload.get("success"):
        block_type = payload.get("block_type", "")
        if block_type not in _STABLE_BLOCK_TYPES:
            return False
        ttl = _NEGATIVE_TTL
    else:
        if _looks_like_block_page(payload.get("markdown", "")):
            logger.warning("cache: refusing to store block page as success", url=url)
            return False
        ttl = _ttl_for_url(url)

    # Large screenshots bloat the hot path — drop them before storing
    if payload.get("screenshot") and len(payload["screenshot"]) > 100_000:
        payload["screenshot"] = ""
        payload["_screenshot_dropped"] = True

    payload["_cached_at"] = time.time()

    blob = _serialize(payload)
    if blob is None:
        return False

    try:
        redis = await get_redis()
        key = build_key(url, opts)
        pipe = redis.pipeline()
        pipe.set(key, blob, ex=ttl)
        pipe.sadd(_url_index_key(url), _options_fingerprint(opts))
        pipe.expire(_url_index_key(url), ttl)
        await pipe.execute()
        return True
    except Exception as exc:
        logger.debug("cache: set failed", error=str(exc))
        return False


async def invalidate(url: str) -> int:
    """Delete all cached variants for a single URL. Returns count deleted."""
    try:
        redis = await get_redis()
    except Exception:
        return 0
    index_key = _url_index_key(url)
    try:
        opt_hashes = await redis.smembers(index_key)
    except Exception:
        return 0
    if not opt_hashes:
        return 0
    normalized = _normalize_url(url)
    url_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    # Binary client returns bytes for set members — decode
    keys = [
        f"scrape:{CACHE_VERSION}:{url_hash}:{h.decode() if isinstance(h, bytes) else h}"
        for h in opt_hashes
    ]
    keys.append(index_key)
    try:
        return await redis.delete(*keys)
    except Exception:
        return 0


async def purge_all() -> int:
    """Delete every cache entry in the current namespace. Slow (SCAN).

    Use when schema changes or cached data is poisoned across the board.
    For one-URL invalidation use `invalidate(url)` instead — it's O(1).
    """
    try:
        redis = await get_redis()
    except Exception:
        return 0
    count = 0
    patterns = [f"scrape:{CACHE_VERSION}:*"]
    for pattern in patterns:
        try:
            async for key in redis.scan_iter(match=pattern, count=500):
                await redis.delete(key)
                count += 1
        except Exception as exc:
            logger.warning("cache: purge_all failed", pattern=pattern, error=str(exc))
    logger.info("cache: purge_all complete", keys_deleted=count)
    return count


async def invalidate_domain(host: str) -> int:
    """Deprecated — we don't store URL text in keys, so domain filtering
    isn't possible without a reverse index. Forwards to purge_all() and
    kept for API backward compatibility. Use purge_all() directly."""
    return await purge_all()


async def stats() -> dict:
    """O(1) cache stats — just Redis INFO counters, no keyspace scan.

    Previous implementation did a SCAN over the cache namespace on every call
    which became a bottleneck at 20K+ keys and blocked Caddy health probes.
    We trade the `namespace_keys` accuracy for constant-time response; callers
    who want exact key counts can hit `DBSIZE` directly.
    """
    try:
        redis = await get_redis()
        info = await redis.info("stats")
        hits = int(info.get("keyspace_hits", 0))
        misses = int(info.get("keyspace_misses", 0))
        return {
            "redis_global_hits": hits,
            "redis_global_misses": misses,
            "redis_global_hit_rate": round(hits / max(1, hits + misses), 4),
        }
    except Exception:
        return {"redis_global_hits": 0, "redis_global_misses": 0, "redis_global_hit_rate": 0.0}
