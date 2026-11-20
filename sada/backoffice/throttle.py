"""Small in-process helpers for public, unauthenticated endpoints: a sliding-window rate limit per
client and a short-lived response cache. Both are per process (one back office), bounded in memory."""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import OrderedDict, deque

from .edge import CLOUDFLARE_RANGES

#: Where the proxy that fronts the back office (Traefik in the cluster) connects from: loopback, private
#: and carrier-grade NAT ranges (pod and node networks). SADA_TRUSTED_PROXIES replaces the list
#: (comma-separated addresses or CIDRs; "none" trusts no proxy, so forwarded headers are ignored).
DEFAULT_TRUSTED_PROXIES = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,fc00::/7"


def _networks(spec: str) -> tuple:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if part and part.lower() != "none":
            out.append(ipaddress.ip_network(part, strict=False))
    return tuple(out)


def _ip(text: str):
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def _trusted(env: str, default: str) -> tuple:
    return _networks(os.environ.get(env) or default)


def peer_trusted(request) -> bool:
    """The socket peer is a proxy of ours (Traefik in the cluster): its forwarded headers are believed."""
    peer_ip = _ip(request.client.host) if request.client else None
    return peer_ip is not None and any(
        peer_ip in net for net in _trusted("SADA_TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES)
    )


def client_ip(request) -> str:
    """The visitor's address. Behind the proxy the socket peer is the proxy itself, so its
    X-Forwarded-For (then X-Real-IP) is read, but only when the peer is a trusted proxy: from anyone
    else the headers are forged and ignored. X-Forwarded-For is walked from the right, skipping the
    trusted proxies, so a value the visitor prepends cannot pick the address that is counted.

    Behind Cloudflare the right-most hop that is not ours is a Cloudflare edge address. Only then is
    ``CF-Connecting-IP`` believed (anyone reaching the origin directly could send it); edge ranges are
    also skipped when walking, so a missing header still resolves to the visitor, not to the edge."""
    peer = request.client.host if request.client else "?"
    peer_ip = _ip(peer)
    if peer_ip is None:
        return peer
    trusted = _trusted("SADA_TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES)
    if not any(peer_ip in net for net in trusted):
        return peer
    edge = _trusted("SADA_CLOUDFLARE_PROXIES", CLOUDFLARE_RANGES)
    seen_edge = False
    for hop in reversed((request.headers.get("x-forwarded-for") or "").split(",")):
        ip = _ip(hop)
        if ip is None:
            break  # garbage: stop trusting what is to its left
        if any(ip in net for net in trusted):
            continue
        if any(ip in net for net in edge):
            if not seen_edge:
                seen_edge = True
                cf = _ip(request.headers.get("cf-connecting-ip") or "")
                if cf is not None:
                    return str(cf)
            continue
        return str(ip)
    real = _ip(request.headers.get("x-real-ip") or "")
    return str(real) if real is not None else peer


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


#: Answers of the public prayer-times endpoint, kept a minute. Every write of a mosque's settings or
#: timetable empties it, so a change shows at once instead of a minute later.
PUBLIC_CACHE = TtlCache(60.0)
