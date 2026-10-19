import math
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import httpx

from .cache import TTLCache
from .rate_limit import RateLimitDecision, TokenBucketRateLimiter
from .settings import Settings


MEDIA_KEY_PREFIX = "videosaver:media:"
JOB_KEY_PREFIX = "videosaver:job:"
RATE_KEY_PREFIX = "videosaver:rate:"

_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
  tokens = burst
  ts = now
end
local elapsed = now - ts
if elapsed < 0 then
  elapsed = 0
end
tokens = math.min(burst, tokens + elapsed * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  allowed = 1
  tokens = tokens - 1
else
  retry = math.ceil((1 - tokens) / rate)
end
redis.call('HSET', key, 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', key, ttl)
return {allowed, tostring(tokens), retry}
"""


class StateBackendError(RuntimeError):
    pass


@runtime_checkable
class StateBackend(Protocol):
    async def rate_limit(self, key: str) -> RateLimitDecision: ...

    async def cache_get(self, key: str) -> str | None: ...

    async def cache_set(self, key: str, value: str, ttl_seconds: float) -> None: ...

    async def job_get(self, job_id: str) -> str | None: ...

    async def job_save(self, job_id: str, value: str, ttl_seconds: float) -> None: ...

    async def close(self) -> None: ...


class MemoryState:
    def __init__(self, settings: Settings):
        self._cache: TTLCache[str, str] = TTLCache(
            max_entries=settings.cache_max_entries,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._jobs: TTLCache[str, str] = TTLCache(
            max_entries=settings.queue_max_jobs,
            ttl_seconds=settings.job_ttl_seconds,
        )
        self._limiter = TokenBucketRateLimiter(
            per_minute=settings.rate_limit_per_minute,
            burst=settings.rate_limit_burst,
            max_keys=settings.rate_limit_max_keys,
        )

    async def rate_limit(self, key: str) -> RateLimitDecision:
        return self._limiter.check(key)

    async def cache_get(self, key: str) -> str | None:
        return self._cache.get(key)

    async def cache_set(self, key: str, value: str, ttl_seconds: float) -> None:
        self._cache.set(key, value, ttl_seconds=ttl_seconds)

    async def job_get(self, job_id: str) -> str | None:
        return self._jobs.get(job_id)

    async def job_save(self, job_id: str, value: str, ttl_seconds: float) -> None:
        self._jobs.set(job_id, value, ttl_seconds=ttl_seconds)

    async def close(self) -> None:
        return None


class RedisState:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not settings.redis_rest_url or not settings.redis_rest_token:
            raise StateBackendError("Redis REST credentials are not configured.")

        self._url = settings.redis_rest_url.rstrip("/")
        self._token = settings.redis_rest_token
        self._rate_per_second = max(1, settings.rate_limit_per_minute) / 60.0
        self._burst = max(1, settings.rate_limit_burst)
        self._rate_ttl = 900
        self._clock = clock
        self._http = http_client
        self._owns_client = http_client is None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=5.0)
        return self._http

    async def _execute(self, *command: str) -> object:
        try:
            response = await self._client().post(
                self._url,
                json=list(command),
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise StateBackendError("The state backend is unavailable.") from exc

        if response.status_code >= 400:
            raise StateBackendError("The state backend rejected the request.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise StateBackendError("The state backend returned an invalid response.") from exc

        if not isinstance(payload, dict) or payload.get("error"):
            raise StateBackendError("The state backend rejected the command.")

        return payload.get("result")

    async def rate_limit(self, key: str) -> RateLimitDecision:
        try:
            result = await self._execute(
                "EVAL",
                _RATE_LIMIT_SCRIPT,
                "1",
                f"{RATE_KEY_PREFIX}{key}",
                f"{self._rate_per_second:.10f}",
                str(self._burst),
                f"{self._clock():.6f}",
                str(self._rate_ttl),
            )
        except StateBackendError:
            return RateLimitDecision(
                allowed=True,
                limit=self._burst,
                remaining=self._burst,
                retry_after=0,
            )

        if not isinstance(result, list) or len(result) < 3:
            return RateLimitDecision(
                allowed=True,
                limit=self._burst,
                remaining=self._burst,
                retry_after=0,
            )

        allowed = bool(int(result[0]))
        tokens = float(result[1])
        retry_after = int(result[2])
        return RateLimitDecision(
            allowed=allowed,
            limit=self._burst,
            remaining=max(0, int(tokens)),
            retry_after=max(0, retry_after),
        )

    async def cache_get(self, key: str) -> str | None:
        try:
            result = await self._execute("GET", f"{MEDIA_KEY_PREFIX}{key}")
        except StateBackendError:
            return None
        return None if result is None else str(result)

    async def cache_set(self, key: str, value: str, ttl_seconds: float) -> None:
        try:
            await self._execute(
                "SETEX",
                f"{MEDIA_KEY_PREFIX}{key}",
                str(max(1, math.ceil(ttl_seconds))),
                value,
            )
        except StateBackendError:
            return None

    async def job_get(self, job_id: str) -> str | None:
        result = await self._execute("GET", f"{JOB_KEY_PREFIX}{job_id}")
        return None if result is None else str(result)

    async def job_save(self, job_id: str, value: str, ttl_seconds: float) -> None:
        await self._execute(
            "SETEX",
            f"{JOB_KEY_PREFIX}{job_id}",
            str(max(1, math.ceil(ttl_seconds))),
            value,
        )

    async def close(self) -> None:
        if self._http is not None and self._owns_client:
            await self._http.aclose()
        self._http = None


def create_state(settings: Settings) -> StateBackend:
    if settings.redis_rest_url and settings.redis_rest_token:
        return RedisState(settings)
    return MemoryState(settings)
