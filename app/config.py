from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENV: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8001
    LOG_LEVEL: str = "info"
    TZ: str = "Asia/Kolkata"

    REDIS_URL: str = "redis://localhost:6379/1"

    PROXY_URL: str = ""
    PROXY_URL_IPROYAL: str = ""

    MAX_BROWSER_CONTEXTS: int = 5

    # Google Gemini API keys (comma-separated, round-robin for /extract)
    GEMINI_API_KEYS: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Tavily API keys (comma-separated, round-robin for fallback scraping)
    TAVILY_API_KEYS: str = ""

    # Cloudflare Worker proxies
    REDDIT_PROXY_URL: str = ""  # Reddit-specific CF Worker
    CF_PROXY_URL: str = ""  # General-purpose CF proxy Worker

    ADMIN_BOOTSTRAP_NAME: str = ""

    AEROCRAWL_DB_PATH: str = "data/ninjascraper.db"

    JS_HEAVY_DOMAINS: str = "twitter.com,x.com,instagram.com,facebook.com,linkedin.com,reddit.com,tiktok.com"

    # V3: Redis scrape-result cache (Step 0 of fallback chain)
    CACHE_ENABLED: bool = True
    CACHE_DEFAULT_TTL_SECONDS: int = 86400  # 24 hours
    CACHE_MAX_VALUE_BYTES: int = 2_097_152  # 2 MB post-compression

    # V3: Zyte API (web unlocker, replaces Tavily at Step 9)
    ZYTE_API_KEY: str = ""
    ZYTE_MONTHLY_BUDGET_USD: float = 30.0
    ZYTE_ALLOWLIST_DOMAINS: str = (
        "g2.com,capterra.com,crunchbase.com,quora.com,glassdoor.com,"
        "x.com,twitter.com,linkedin.com,instagram.com,facebook.com"
    )
    ZYTE_ENABLED: bool = True  # master kill switch
    # Zyte doesn't return per-call cost in response — this is the server-side
    # estimated cost per successful hard-tier call. Proxyway 2025 benchmark
    # shows Zyte hard-tier pricing around $1/CPM ($0.001/call). We overestimate
    # at $0.01/call as a safety margin — $30 cap = ~3,000 calls/mo safely.
    # Tune this after a month based on actual Zyte dashboard usage.
    ZYTE_ESTIMATED_COST_PER_CALL: float = 0.01

    # V3: Smart domain routing credentials
    GITHUB_PAT: str = ""
    NCBI_API_KEY: str = ""
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "NinjaScraper/3.0 by rithik"

    # V3: Slack alerts (shared Aerosend Slack, #content-pipeline)
    SLACK_BOT_TOKEN: str = ""
    SLACK_CHANNEL_PIPELINE: str = "C0REDACTED"
    ZYTE_ALERT_THRESHOLD_PCT: float = 0.80

    # V3.1: Per-key rate limits (Redis-backed sliding window)
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000
    RATE_LIMIT_ENABLED: bool = True

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def gemini_key_list(self) -> list[str]:
        return [k.strip() for k in self.GEMINI_API_KEYS.split(",") if k.strip()]

    @property
    def js_heavy_list(self) -> list[str]:
        return [d.strip() for d in self.JS_HEAVY_DOMAINS.split(",") if d.strip()]

    @property
    def tavily_key_list(self) -> list[str]:
        return [k.strip() for k in self.TAVILY_API_KEYS.split(",") if k.strip()]

    @property
    def zyte_allowlist(self) -> list[str]:
        return [d.strip().lower() for d in self.ZYTE_ALLOWLIST_DOMAINS.split(",") if d.strip()]

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }


settings = Settings()
