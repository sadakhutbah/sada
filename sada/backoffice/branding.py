"""Logo specific to the back-office: one file per mosque under data/backoffice/logos/; neutral emblem by default.

The logo of the video scenes (config.logo, monogram on the overlay) is a separate setting: here we only
handle the image of the interface (favicon + top bar).
"""
from __future__ import annotations

import io
from pathlib import Path

from .. import paths
from ..i18n import _

DIR = paths.DATA / "backoffice" / "logos"
MAX_BYTES = 2 * 1024 * 1024
EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}


def path_for(mosque: dict) -> Path:
    rel = (mosque.get("logo_path") or "").strip()
    return (paths.ROOT / rel) if rel else DIR / f"{mosque['slug']}.png"


def version(mosque: dict) -> str:
    return f"{mosque['id']}-{abs(hash((mosque.get('logo_path') or ''))) % 0xFFFFFF:x}"


def url(mosque: dict | None) -> str:
    """Address of the logo image, suffixed with a version: the browser does not keep the old favicon."""
    if not mosque or not (mosque.get("logo_path") or "").strip():
        return "/static/emblem.svg"
    return f"/logo/{mosque['slug']}?v={version(mosque)}"


def save(mosque: dict, filename: str, content: bytes) -> str:
    """Validates and saves the image; returns the path relative to the repository. Raises ValueError if refused."""
    from PIL import Image

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXT:
        raise ValueError(_("accepted format: png, jpg, webp or svg"))
    if len(content) > MAX_BYTES:
        raise ValueError(_("image too large (2 MB maximum)"))
    if ext == ".svg":
        head = content[:400].lower()
        if b"<svg" not in head:
            raise ValueError(_("unreadable SVG file"))
    else:
        try:
            im = Image.open(io.BytesIO(content))
            im.verify()
            if min(im.size) < 16:
                raise ValueError(_("image too small (16 px minimum)"))
        except ValueError:
            raise
        except Exception:
            raise ValueError(_("unreadable image file")) from None
    DIR.mkdir(parents=True, exist_ok=True)
    for old in DIR.glob(f"{mosque['slug']}.*"):
        old.unlink(missing_ok=True)
    dst = DIR / f"{mosque['slug']}{ext}"
    dst.write_bytes(content)
    return str(dst.relative_to(paths.ROOT))


def clear(mosque: dict) -> None:
    for old in DIR.glob(f"{mosque['slug']}.*"):
        old.unlink(missing_ok=True)
