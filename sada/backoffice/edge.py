"""Settings of the edge in front of the back office: Cloudflare proxy, public host, ingest host.

Everything here is read from the environment on each call (tests and operators change it without a
restart of the module) and has a default that keeps a single-machine install working unchanged.

* ``SADA_INGEST_HOST`` (alias ``INGEST_HOST``): the one name OBS pushes RTMP *and* SRT to. It points at
  the relay, never behind the Cloudflare proxy (grey cloud: it carries no HTTP). Hosted platform default:
  ``ingest.sadakhutbah.org``. Elsewhere the host the browser reached is used, as before.
* ``SADA_PUBLIC_BASE_URL`` (alias ``PUBLIC_BASE_URL``): where the public pages and API live from the
  outside (``https://sadakhutbah.org``). Used for every absolute address printed on a public document
  (the QR code of the PDF). Unset: the platform address of the instance, then the request's.
* ``SADA_ALLOWED_HOSTS`` / ``SADA_PUBLIC_HOSTS``: host names served. Unset (default): any. When set, the
  first list is served everywhere, the second one only under ``/api/v1/public/``.
* ``SADA_CLOUDFLARE_PROXIES``: Cloudflare's published address ranges, the only hops whose
  ``CF-Connecting-IP`` is believed (see ``throttle.client_ip``).
"""

from __future__ import annotations

import os
from urllib.parse import quote

from . import instance

DEFAULT_INGEST_HOST = "ingest.sadakhutbah.org"
RTMP_PORT = 1935
SRT_PORT = 8890
PUBLIC_PREFIX = "/api/v1/public/"

#: https://www.cloudflare.com/ips-v4 and /ips-v6. SADA_CLOUDFLARE_PROXIES replaces the list ("none" = trust no edge).
CLOUDFLARE_RANGES = (
    "173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,141.101.64.0/18,108.162.192.0/18,"
    "190.93.240.0/20,188.114.96.0/20,197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,"
    "104.24.0.0/14,172.64.0.0/13,131.0.72.0/22,2400:cb00::/32,2606:4700::/32,2803:f800::/32,"
    "2405:b500::/32,2405:8100::/32,2a06:98c0::/29,2c0f:f248::/32"
)


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _hosted() -> bool:
    from .auth import hosted

    return hosted()


def ingest_host(request_host: str = "") -> str:
    """The name shown for RTMP and SRT: the setting, else the platform default when hosted, else the
    host the browser reached (a machine on the local network has no other name)."""
    return _env("SADA_INGEST_HOST", "INGEST_HOST") or (
        DEFAULT_INGEST_HOST if _hosted() else request_host or "127.0.0.1"
    )


def push_rtmp(host: str, slug: str) -> str:
    return f"rtmp://{host}:{RTMP_PORT}/live/{slug}"


def push_srt(host: str, slug: str, key: str = "") -> str:
    """SRT publish address (MediaMTX stream id). With the key it is the full address to paste in OBS;
    without, the address is safe to display."""
    stream = f"publish:live/{slug}" + (f":{slug}:{key}" if key else "")
    return f"srt://{host}:{SRT_PORT}?streamid={stream}"


def public_base(default: str = "") -> str:
    """Base of the absolute addresses of public pages: the setting, else the instance address, else `default`."""
    url = _env("SADA_PUBLIC_BASE_URL", "PUBLIC_BASE_URL").rstrip("/")
    return url if url.startswith(("http://", "https://")) else instance.base(default)


def public_page_url(slug: str, default_base: str = "") -> str:
    return f"{public_base(default_base)}{PUBLIC_PREFIX}mosques/{quote(slug)}/prayer-times/page"


def _hosts(*names: str) -> set[str]:
    return {h.strip().lower().rstrip(".") for h in _env(*names).split(",") if h.strip()}


def host_allowed(host: str, path: str) -> bool:
    """True when the Host header may be served. No SADA_ALLOWED_HOSTS: everything (single machine)."""
    allowed = _hosts("SADA_ALLOWED_HOSTS")
    if not allowed:
        return True
    name = (host or "").split(":")[0].strip().lower().rstrip(".")
    if name in allowed:
        return True
    return path.startswith(PUBLIC_PREFIX) and name in _hosts("SADA_PUBLIC_HOSTS")


#: Cache policy of the public read-only answers. `s-maxage` lets a shared cache (Cloudflare, a Worker)
#: keep them as long as a browser; the server-side cache is emptied on every write, so keep both short.
PUBLIC_MAX_AGE_S = 60


def public_cache_control() -> str:
    return f"public, max-age={PUBLIC_MAX_AGE_S}, s-maxage={PUBLIC_MAX_AGE_S}, stale-while-revalidate=30"
