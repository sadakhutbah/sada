"""Logo propre au back-office : fichier par mosquée sous data/backoffice/logos/ ; emblème neutre à défaut.

Le logo des scènes vidéo (config.logo, monogramme sur l'habillage) est un réglage séparé : ici on ne
manipule que l'image de l'interface (favicon + barre du haut).
"""
from __future__ import annotations

import io
from pathlib import Path

from .. import paths

DIR = paths.DATA / "backoffice" / "logos"
MAX_BYTES = 2 * 1024 * 1024
EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}


def path_for(mosque: dict) -> Path:
    rel = (mosque.get("logo_path") or "").strip()
    return (paths.ROOT / rel) if rel else DIR / f"{mosque['slug']}.png"


def version(mosque: dict) -> str:
    return f"{mosque['id']}-{abs(hash((mosque.get('logo_path') or ''))) % 0xFFFFFF:x}"


def url(mosque: dict | None) -> str:
    """Adresse de l'image du logo, suffixée par une version : le navigateur ne garde pas l'ancien favicon."""
    if not mosque or not (mosque.get("logo_path") or "").strip():
        return "/static/emblem.svg"
    return f"/logo/{mosque['slug']}?v={version(mosque)}"


def save(mosque: dict, filename: str, content: bytes) -> str:
    """Valide et enregistre l'image ; renvoie le chemin relatif au dépôt. Lève ValueError si refusée."""
    from PIL import Image

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXT:
        raise ValueError("format accepté : png, jpg, webp ou svg")
    if len(content) > MAX_BYTES:
        raise ValueError("image trop lourde (2 Mo maximum)")
    if ext == ".svg":
        head = content[:400].lower()
        if b"<svg" not in head:
            raise ValueError("fichier SVG illisible")
    else:
        try:
            im = Image.open(io.BytesIO(content))
            im.verify()
            if min(im.size) < 16:
                raise ValueError("image trop petite (16 px minimum)")
        except ValueError:
            raise
        except Exception:
            raise ValueError("fichier image illisible") from None
    DIR.mkdir(parents=True, exist_ok=True)
    for old in DIR.glob(f"{mosque['slug']}.*"):
        old.unlink(missing_ok=True)
    dst = DIR / f"{mosque['slug']}{ext}"
    dst.write_bytes(content)
    return str(dst.relative_to(paths.ROOT))


def clear(mosque: dict) -> None:
    for old in DIR.glob(f"{mosque['slug']}.*"):
        old.unlink(missing_ok=True)
