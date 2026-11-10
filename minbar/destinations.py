"""Live destinations, read from the back office at start-up.

The addresses and their keys live in the interface, not on the orchestrator's side and not in this
repository. At start-up the orchestrator asks the internal endpoint for the enabled destinations; the
local setting (`--rtmp-urls`, MINBAR_RTMP_URLS, JSON file) only remains as a fallback for a demo or a
machine without a back office. Any failure leaves the local setting in place: the live never depends on
the back office being up.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Callable
from urllib.parse import urlencode

Fetch = Callable[[str, str], dict]


def _http_get(url: str, token: str) -> dict:      # pragma: no cover (network)
    from .backoffice.internal import HEADER

    req = urllib.request.Request(url, headers={HEADER: token})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read(65536))


def from_backoffice(backoffice_url: str, api_port: int, fetch: Fetch | None = None,
                    log: Callable[[str], None] = lambda _m: None) -> list[str]:
    """Enabled destinations saved in the interface; empty list when there is nothing or on failure."""
    if not backoffice_url:
        return []
    from .backoffice.internal import token

    url = backoffice_url.rstrip("/") + "/api/v1/internal/live-targets?" + urlencode({"port": api_port or 0})
    try:
        data = (fetch or _http_get)(url, token()) or {}
    except Exception as e:                        # noqa: BLE001 — any failure: keep the local setting
        log(f"Direct : destinations du back-office injoignables ({e}) — configuration locale conservée")
        return []
    urls = [str(u).strip() for u in (data.get("urls") or []) if str(u).strip()]
    if urls:
        log(f"Direct : {len(urls)} destination(s) lue(s) dans le back-office")
    else:
        log("Direct : aucune destination enregistrée dans le back-office — configuration locale conservée")
    return urls
