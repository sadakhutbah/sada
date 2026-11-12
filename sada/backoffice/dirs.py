"""Browsing of the folders of the machine hosting the back-office (chosen with buttons, not typed).

The server only renders existing folders: we start from the accessible roots (home, /mnt, /media...)
and only descend into real entries. Hidden names and pseudo-filesystems (/proc, /sys) are
masked; no file content is exposed.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import paths
from ..i18n import _

# sub-folders of /mnt that are not user disks (internal to Windows/WSL)
SKIP_MT = {"wsl", "wslg", "host", "cgroup", "host_mnt"}


SKIP = {"proc", "sys", "dev", "run", "boot", "lost+found"}


def roots() -> list[dict]:
    """Starting points offered: program folder, home, mounted disks."""
    out: list[dict] = []

    def add(p: Path, label: str | None = None):
        try:
            if p.is_dir() and p.resolve() not in {Path(r["path"]) for r in out}:
                out.append({"path": str(p.resolve()), "label": label or str(p)})
        except OSError:
            pass

    add(paths.ROOT, _("Program folder"))
    add(Path.home(), _("Home folder"))
    known = {"/mnt": _("Windows drives"), "/media": _("Devices"), "/srv": _("Services"), "/data": _("Data")}
    for base in known:
        b = Path(base)
        if not b.is_dir():
            continue
        add(b, known[base])
        try:
            for child in sorted(b.iterdir())[:24]:
                if child.name in SKIP or (base == "/mnt" and child.name in SKIP_MT) or not child.is_dir():
                    continue
                # "/mnt/c" -> "Disque C:", readable by a Windows user
                label = (
                    _("Drive {letter}:", letter=child.name.upper())
                    if base == "/mnt" and len(child.name) == 1
                    else child.name
                )
                add(child, label)
        except OSError:
            pass
    add(Path("/"), _("Disk root"))
    return out


def listing(path: str | None) -> dict:
    """-> {path, parent, exists, writable, entries:[{name,path,readable}]} ; ValueError if out of bounds."""
    p = Path(path).expanduser() if path else Path.home()
    try:
        p = p.resolve()
    except OSError:
        raise ValueError(_("unreadable path")) from None
    if not p.is_dir():
        return {
            "path": str(p),
            "parent": str(p.parent) if p.parent != p else None,
            "exists": False,
            "writable": False,
            "entries": [],
        }
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
    """Readable path without revealing the machine's location: relative to the program when possible."""
    if not path:
        return ""
    p = Path(path).expanduser()
    try:
        rel = p.resolve().relative_to(paths.ROOT)
        return str(rel)
    except (ValueError, OSError):
        return p.name or str(p)


def check(path: str, create_if_missing: bool = False) -> tuple[bool, str]:
    """Checks that a chosen folder is suitable; creates it if missing and requested."""
    p = Path(path).expanduser()
    if p.exists() and not p.is_dir():
        return False, _("a file already has this name")
    if not p.exists():
        if not create_if_missing:
            return False, _("this folder does not exist yet")
        try:
            p.mkdir(parents=True)
        except OSError as e:
            return False, _("cannot create it ({error})", error=e.strerror or e)
    if not os.access(p, os.W_OK):
        return False, _("folder is read-only for the program")
    return True, ""
