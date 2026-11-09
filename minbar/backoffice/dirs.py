"""Parcours des dossiers de la machine qui héberge le back-office (choix par bouton, pas au clavier).

Le serveur ne rend que des dossiers existants : on part des racines accessibles (accueil, /mnt, /media…)
et on ne descend que dans des entrées réelles. Les noms cachés et les pseudo-systèmes (/proc, /sys) sont
masqués ; aucun contenu de fichier n'est exposé.
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import paths

# sous-dossiers de /mnt qui ne sont pas des disques utilisateur (interne à Windows/WSL)
SKIP_MT = {"wsl", "wslg", "host", "cgroup", "host_mnt"}


SKIP = {"proc", "sys", "dev", "run", "boot", "lost+found"}


def roots() -> list[dict]:
    """Points de départ proposés : dossier du programme, accueil, disques montés."""
    out: list[dict] = []

    def add(p: Path, label: str | None = None):
        try:
            if p.is_dir() and p.resolve() not in {Path(r["path"]) for r in out}:
                out.append({"path": str(p.resolve()), "label": label or str(p)})
        except OSError:
            pass

    add(paths.ROOT, "Dossier du programme")
    add(Path.home(), "Dossier personnel")
    known = {"/mnt": "Disques Windows", "/media": "Périphériques", "/srv": "Services", "/data": "Données"}
    for base in known:
        b = Path(base)
        if not b.is_dir():
            continue
        add(b, known[base])
        try:
            for child in sorted(b.iterdir())[:24]:
                if child.name in SKIP or (base == "/mnt" and child.name in SKIP_MT) or not child.is_dir():
                    continue
                # « /mnt/c » -> « Disque C: », lisible par un utilisateur de Windows
                label = f"Disque {child.name.upper()}:" if base == "/mnt" and len(child.name) == 1 else child.name
                add(child, label)
        except OSError:
            pass
    add(Path("/"), "Racine du disque")
    return out


def listing(path: str | None) -> dict:
    """-> {path, parent, exists, writable, entries:[{name,path,readable}]} ; ValueError si hors limites."""
    p = Path(path).expanduser() if path else Path.home()
    try:
        p = p.resolve()
    except OSError:
        raise ValueError("chemin illisible") from None
    if not p.is_dir():
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else None,
                "exists": False, "writable": False, "entries": []}
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            name = child.name
            if name.startswith(".") or (p == Path("/") and name in SKIP):
                continue
            try:
                if not child.is_dir() or child.is_symlink():
                    continue
            except OSError:
                continue
            entries.append({"name": name, "path": str(child)})
            if len(entries) >= 400:
                break
    except PermissionError:
        pass
    return {
        "path": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "exists": True,
        "writable": os.access(p, os.W_OK),
        "entries": entries,
    }


def label(path) -> str:
    """Chemin lisible sans révéler l'emplacement de la machine : relatif au programme si possible."""
    if not path:
        return ""
    p = Path(path).expanduser()
    try:
        rel = p.resolve().relative_to(paths.ROOT)
        return str(rel)
    except (ValueError, OSError):
        return p.name or str(p)


def check(path: str, create_if_missing: bool = False) -> tuple[bool, str]:
    """Vérifie qu'un dossier choisi convient ; le crée s'il manque et que c'est demandé."""
    p = Path(path).expanduser()
    if p.exists() and not p.is_dir():
        return False, "un fichier porte déjà ce nom"
    if not p.exists():
        if not create_if_missing:
            return False, "ce dossier n'existe pas encore"
        try:
            p.mkdir(parents=True)
        except OSError as e:
            return False, f"impossible de le créer ({e.strerror or e})"
    if not os.access(p, os.W_OK):
        return False, "dossier en lecture seule pour le programme"
    return True, ""
