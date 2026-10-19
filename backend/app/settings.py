import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    return max(minimum, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_url(name: str) -> str | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute HTTP or HTTPS URL.")
    return raw.rstrip("/")


def _env_first(*names: str) -> str | None:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            return raw
    return None


@dataclass(frozen=True)
class Settings:
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 20
    rate_limit_max_keys: int = 10000
    trust_proxy_headers: bool = False
    cache_max_entries: int = 2048
    cache_ttl_seconds: float = 900.0
    queue_max_jobs: int = 5000
    job_max_urls: int = 25
    job_ttl_seconds: float = 3600.0
    cdn_base_url: str | None = None
    redis_rest_url: str | None = None
    redis_rest_token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        redis_url = _env_first("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL")
        redis_token = _env_first("UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_TOKEN")

        if redis_url:
            parsed = urlsplit(redis_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("The Redis REST URL must be an absolute HTTP or HTTPS URL.")
            redis_url = redis_url.rstrip("/")
        if bool(redis_url) != bool(redis_token):
            raise ValueError("Both the Redis REST URL and token must be configured together.")

        return cls(
            rate_limit_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 60),
            rate_limit_burst=_env_int("RATE_LIMIT_BURST", 20),
            rate_limit_max_keys=_env_int("RATE_LIMIT_MAX_KEYS", 10000),
            trust_proxy_headers=_env_bool("TRUST_PROXY_HEADERS", False),
            cache_max_entries=_env_int("CACHE_MAX_ENTRIES", 2048),
            cache_ttl_seconds=float(_env_int("CACHE_TTL_SECONDS", 900)),
            queue_max_jobs=_env_int("QUEUE_MAX_JOBS", 5000),
            job_max_urls=_env_int("JOB_MAX_URLS", 25),
            job_ttl_seconds=float(_env_int("JOB_TTL_SECONDS", 3600)),
            cdn_base_url=_env_url("CDN_BASE_URL"),
            redis_rest_url=redis_url,
            redis_rest_token=redis_token,
        )
