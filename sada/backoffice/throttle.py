"""Small in-process helpers for public, unauthenticated endpoints: a sliding-window rate limit per
client and a short-lived response cache. Both are per process (one back office), bounded in memory."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque


class SlidingWindow:
    """At most `limit` hits per `window` seconds for each key; returns the seconds to wait when refused."""

    def __init__(self, limit: int, window: float = 60.0, max_keys: int = 4096):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float:
        """0 when allowed (the hit is counted), else the seconds until a slot frees."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                q = self._hits[key] = deque()
                while len(self._hits) > self.max_keys:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(0.1, self.window - (now - q[0]))
            q.append(now)
            return 0.0


class TtlCache:
    """Values kept for `ttl` seconds, the oldest dropped past `max_items`."""

    def __init__(self, ttl: float, max_items: int = 2048):
        self.ttl, self.max_items = ttl, max_items
        self._items: OrderedDict[object, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key, now: float | None = None):
        now = time.monotonic() if now is None else now
        with self._lock:
            hit = self._items.get(key)
            if hit and hit[0] > now:
                return hit[1]
            self._items.pop(key, None)
            return None

    def put(self, key, value, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._items[key] = (now + self.ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
