"""Video input of the live: what the encoder reads, set up from the back office.

Two ways to feed the live:

* ``camera`` — a surveillance camera publishing RTSP: the address is typed as is.
* ``push``   — a computer (OBS) *pushes* its picture, which an RTSP/RTMP relay receives and the encoder
  then reads. The screen shows the address to paste in OBS; the encoder keeps reading RTSP.

A stored address is not a secret: it comes back in clear whenever the user is allowed to see the settings.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from urllib.parse import urlsplit

from ..i18n import _
from . import db

MODES = ("camera", "push")
CAMERA_SCHEMES = ("rtsp://", "rtsps://")
DEFAULT_URL = "rtsp://127.0.0.1:8554/cam"
# The relay listens on 1935 (RTMP, what OBS pushes) and re-serves the same stream on 8554 (RTSP).
RELAY_RTMP_PORT = 1935
RELAY_RTSP_PORT = 8554
PROBE_TIMEOUT_S = 6


class InvalidInput(ValueError):
    """The video input the screen sent cannot be used as is."""


def slug(mosque) -> str:
    """Stream name on the relay: one per mosque, readable in the logs."""
    base = re.sub(r"[^a-z0-9]+", "-", str(mosque.get("slug") or mosque.get("id") or "live").lower()).strip("-")
    return base or "live"


def push_rtmp(host: str, mosque) -> str:
    """Address to paste in OBS (its "Stream" server field)."""
    return f"rtmp://{host}:{RELAY_RTMP_PORT}/live/{slug(mosque)}"


def push_rtsp(mosque) -> str:
    """What the encoder reads once the push is up: the relay's RTSP copy."""
    return f"rtsp://127.0.0.1:{RELAY_RTSP_PORT}/live/{slug(mosque)}"


def check(mode: str, url: str | None, mosque) -> tuple[str, str, str]:
    """(mode, address, error message) — normalised for storage."""
    mode = (mode or "").strip()
    if mode not in MODES:
        return mode, "", _("Choose the mosque's camera or the push from a computer.")
    if mode == "push":
        return mode, push_rtsp(mosque), ""
    url = (url or "").strip() or DEFAULT_URL
    if not url.startswith(CAMERA_SCHEMES):
        return mode, url, _("The camera address must start with rtsp:// or rtsps://.")
    if re.search(r"\s", url):
        return mode, url, _("The address must not contain a space.")
    if not urlsplit(url).hostname:
        return mode, url, _("The address does not name a server.")
    return mode, url, ""


def current(conn, mosque) -> dict:
    """Stored input, with the mode "camera" as the default before anything is set."""
    row = db.one(conn, "SELECT mode, url FROM live_input WHERE mosque_id = ?", mosque["id"])
    mode = (row or {}).get("mode") or "camera"
    url = (row or {}).get("url") or (push_rtsp(mosque) if mode == "push" else DEFAULT_URL)
    return {"mode": mode, "url": url}


def effective(conn, mosque_id: int) -> str:
    """Address in clear for the encoder, or "" when nothing was ever configured."""
    row = db.one(conn, "SELECT mode, url FROM live_input WHERE mosque_id = ?", mosque_id)
    if not row:
        return ""
    return row["url"] or ""


def view(conn, mosque, host: str) -> dict:
    """What the screen shows: the stored mode, its address, and the address to give OBS."""
    cur = current(conn, mosque)
    return {
        **cur,
        "configured": bool(db.one(conn, "SELECT 1 AS x FROM live_input WHERE mosque_id = ?", mosque["id"])),
        "push_rtmp": push_rtmp(host, mosque),
        "push_rtsp": push_rtsp(mosque),
    }


def save(conn, mosque, mode, url, user) -> dict:
    mode, url, err = check(mode, url, mosque)
    if err:
        raise InvalidInput(err)
    conn.execute(
        "INSERT INTO live_input (mosque_id, mode, url, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(mosque_id) DO UPDATE SET mode = excluded.mode, url = excluded.url, "
        "updated_at = excluded.updated_at",
        (mosque["id"], mode, url, db.now_iso()),
    )
    db.audit(
        conn,
        mosque["id"],
        user["id"] if user else None,
        "direct.entree",
        "mosque",
        mosque["id"],
        mode=mode,
        adresse=url,
    )
    return current(conn, mosque)


def probe(url: str, timeout: float = PROBE_TIMEOUT_S) -> tuple[bool, str]:
    """Tries to read the stream: (reachable, readable detail). Never raises."""
    url = (url or "").strip()
    if not url or not shutil.which("ffprobe"):
        return False, _("Stream cannot be checked from this server.")
    if url.startswith("rtmp"):
        return False, _("A push is checked once OBS is running: the encoder reads the relay, not this address.")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate",
        "-of",
        "csv=p=0",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, _("No image received within the time limit.")
    except OSError as e:
        return False, _("Inspection impossible ({error}).", error=e.__class__.__name__)
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        return False, _("Stream unreachable: {detail}", detail=detail[-1][:160] if detail else _("no answer"))
    lignes, video = [ln for ln in r.stdout.splitlines() if ln.strip()], ""
    for ln in lignes:
        parts = ln.split(",")
        if parts and parts[0] == "video":
            w = parts[1] if len(parts) > 1 else ""
            h = parts[2] if len(parts) > 2 else ""
            fps = parts[3] if len(parts) > 3 else ""
            num, _sep, den = fps.partition("/")
            try:
                taux = round(float(num) / float(den or 1))
            except (ValueError, ZeroDivisionError):
                taux = 0
            video = f"{w}×{h}" + (" · " + _("{rate} fps", rate=taux) if taux else "")
            break
    if not video:
        return False, _("The stream answers but carries no image.")
    son = any(ln.startswith("audio") for ln in lignes)
    return True, video + (" · " + _("sound present") if son else " · " + _("no sound"))
