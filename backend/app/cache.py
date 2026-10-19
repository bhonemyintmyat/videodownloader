import time
from collections import OrderedDict
from collections.abc import Callable
from threading import RLock
from typing import Generic, TypeVar


K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(
        self,
        max_entries: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_entries = max(1, max_entries)
        self._ttl_seconds = max(0.0, ttl_seconds)
        self._clock = clock
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: K) -> V | None:
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: K, value: V) -> None:
        now = self._clock()
        with self._lock:
            self._items[key] = (now + self._ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def delete(self, key: K) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
