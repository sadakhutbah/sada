"""Video input, read from the back office at start-up.

Which picture the live broadcasts (surveillance camera, or a computer pushing its own) is chosen in the
interface. At start-up the orchestrator asks the internal endpoint; the local setting (`--camera-url`,
SADA_CAMERA_URL, JSON file) stays as the fallback for a demo or a machine without a back office.
Any failure leaves it in place: the live never depends on the back office being up.
"""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from urllib.parse import urlencode

from .i18n import _

Fetch = Callable[[str, str], dict]


def input_options(url: str) -> list[str]:
    """ffmpeg options for an input: the RTSP transport only exists for RTSP, and ffmpeg refuses it
    on any other protocol (an RTMP input would fail with "Option rtsp_transport not found")."""
    return ["-rtsp_transport", "tcp"] if (url or "").startswith("rtsp") else []


def _http_get(url: str, token: str) -> dict:      # pragma: no cover (network)
    from .backoffice.internal import HEADER

    req = urllib.request.Request(url, headers={HEADER: token})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read(65536))


def from_backoffice(backoffice_url: str, api_port: int, fetch: Fetch | None = None,
                    log: Callable[[str], None] = lambda _m: None) -> str:
    """Address saved in the interface, or "" when nothing is set or on failure."""
    if not backoffice_url:
        return ""
    from .backoffice.internal import token

    url = backoffice_url.rstrip("/") + "/api/v1/internal/live-input?" + urlencode({"port": api_port or 0})
    try:
        data = (fetch or _http_get)(url, token()) or {}
    except Exception as e:                        # noqa: BLE001 — any failure: keep the local setting
        log(_("Live: back-office video input unreachable ({error}) — local setting kept", error=e))
        return ""
    adresse = str(data.get("url") or "").strip()
    if adresse:
        log(_("Live: video input read from the back office ({protocol})", protocol=adresse.split("://")[0]))
    else:
        log(_("Live: no video input saved in the back office — local setting kept"))
    return adresse
