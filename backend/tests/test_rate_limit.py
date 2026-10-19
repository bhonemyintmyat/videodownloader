from app.rate_limit import TokenBucketRateLimiter


def test_bucket_allows_burst_then_refills():
    now = [0.0]
    limiter = TokenBucketRateLimiter(per_minute=60, burst=2, clock=lambda: now[0])

    assert limiter.check("client").allowed is True
    assert limiter.check("client").allowed is True

    denied = limiter.check("client")
    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after == 1

    now[0] = 1.0
    assert limiter.check("client").allowed is True


def test_buckets_are_isolated_by_client():
    now = [0.0]
    limiter = TokenBucketRateLimiter(per_minute=60, burst=1, clock=lambda: now[0])

    assert limiter.check("first").allowed is True
    assert limiter.check("first").allowed is False
    assert limiter.check("second").allowed is True


def test_idle_buckets_are_pruned():
    now = [0.0]
    limiter = TokenBucketRateLimiter(
        per_minute=60,
        burst=1,
        idle_ttl_seconds=10,
        clock=lambda: now[0],
    )

    limiter.check("old")
    now[0] = 20.0
    limiter.check("new")

    assert "old" not in limiter._buckets
    assert "new" in limiter._buckets
