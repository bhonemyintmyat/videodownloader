import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Callable


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


@dataclass
class _Bucket:
    tokens: float
    last_seen: float


class TokenBucketRateLimiter:
    def __init__(
        self,
        per_minute: int,
        burst: int,
        max_keys: int = 10000,
        idle_ttl_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._rate_per_second = max(1, per_minute) / 60.0
        self._burst = max(1, burst)
        self._max_keys = max(1, max_keys)
        self._idle_ttl_seconds = max(0.0, idle_ttl_seconds)
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = RLock()

    def check(self, key: str) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            self._prune(now)
            bucket = self._buckets.get(key)

            if bucket is None:
                tokens = float(self._burst)
            else:
                elapsed = max(0.0, now - bucket.last_seen)
                tokens = min(float(self._burst), bucket.tokens + elapsed * self._rate_per_second)

            if tokens >= 1.0:
                tokens -= 1.0
                allowed = True
                retry_after = 0
            else:
                allowed = False
                retry_after = max(1, math.ceil((1.0 - tokens) / self._rate_per_second))

            self._buckets[key] = _Bucket(tokens=tokens, last_seen=now)
            self._buckets.move_to_end(key)

            return RateLimitDecision(
                allowed=allowed,
                limit=self._burst,
                remaining=int(tokens),
                retry_after=retry_after,
            )

    def _prune(self, now: float) -> None:
        while self._buckets:
            key, bucket = next(iter(self._buckets.items()))
            if now - bucket.last_seen > self._idle_ttl_seconds:
                self._buckets.popitem(last=False)
                continue
            break

        while len(self._buckets) >= self._max_keys:
            self._buckets.popitem(last=False)
