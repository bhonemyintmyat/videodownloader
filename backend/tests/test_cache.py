from app.cache import TTLCache


def test_cache_expires_entries():
    now = [0.0]
    cache = TTLCache(max_entries=2, ttl_seconds=10, clock=lambda: now[0])

    cache.set("video", "asset")
    assert cache.get("video") == "asset"

    now[0] = 10.1
    assert cache.get("video") is None
    assert len(cache) == 0


def test_cache_evicts_least_recently_used_entry():
    cache = TTLCache(max_entries=2, ttl_seconds=60, clock=lambda: 0.0)

    cache.set("first", 1)
    cache.set("second", 2)
    assert cache.get("first") == 1
    cache.set("third", 3)

    assert cache.get("first") == 1
    assert cache.get("second") is None
    assert cache.get("third") == 3


def test_cache_delete_and_clear():
    cache = TTLCache(max_entries=4, ttl_seconds=60, clock=lambda: 0.0)
    cache.set("a", 1)
    cache.set("b", 2)

    cache.delete("a")
    assert cache.get("a") is None
    assert cache.get("b") == 2

    cache.clear()
    assert len(cache) == 0
