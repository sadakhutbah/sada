"""Texts of the live screens (slates): storage per mosque, view, preview and validation.

The defaults live in `minbar.screens` — the module the engine renders with — so the interface shows
exactly what will be broadcast, and the preview is drawn by `minbar.scenes` itself. Only what the
mosque changes is stored; an emptied field goes back to the default instead of leaving a blank line
on screen.
"""
from __future__ import annotations

import datetime as dt
import io
from types import SimpleNamespace
from typing import Any

from .. import scenes
from .. import screens as base
from ..config import Config
from . import branding, db, variables_ecran


class InvalidScreens(ValueError):
    """Payload refused: unknown screen or field, or a line too long."""


def saved(conn, mosque_id: int) -> dict[str, dict[str, str]]:
    """What the mosque changed: {écran: {champ: texte}}.

    The reserved screen `_commun` (common template texts: Arabic lines) is not one of the engine's
    six screens and stays out of this view.
    """
    out: dict[str, dict[str, str]] = {}
    for r in db.all_(conn, "SELECT screen, slot, text FROM screen_texts "
                           "WHERE mosque_id = ? AND screen <> ?", mosque_id, variables_ecran.SCREEN):
        out.setdefault(r["screen"], {})[r["slot"]] = r["text"]
    return out


def view(conn, mosque: dict) -> dict[str, dict[str, dict[str, str]]]:
    """Screen → slot, with the default, what is saved (empty = untouched) and the text in force."""
    keep = saved(conn, mosque["id"])
    eff = base.effective(keep)
    return {s: {slot: {"default": base.DEFAULTS[s][slot],
                       "value": keep.get(s, {}).get(slot, ""),
                       "text": eff[s][slot]}
                for slot in base.DEFAULTS[s]}
            for s in base.SCREENS}


def save(conn, mosque: dict, payload, user_id=None, ip: str | None = None) -> dict:
    """Replaces the whole set at once (a form sends every field it shows)."""
    try:
        clean = base.check(payload or {})
    except ValueError as e:
        raise InvalidScreens(str(e)) from e
    # Only the six engine screens: the common texts (`_commun`) have their own save route.
    conn.execute("DELETE FROM screen_texts WHERE mosque_id = ? AND screen <> ?",
                 (mosque["id"], variables_ecran.SCREEN))
    for screen, slots in clean.items():
        for slot, text in slots.items():
            if text:                                   # empty = default, nothing stored
                conn.execute("INSERT INTO screen_texts (mosque_id, screen, slot, text, updated_at) "
                             "VALUES (?,?,?,?,?)", (mosque["id"], screen, slot, text, db.now_iso()))
    db.audit(conn, mosque["id"], user_id, "ecrans.configure", "mosque", mosque["id"], ip=ip,
             champs=[f"{s}.{k}" for s, v in clean.items() for k, t in v.items() if t])
    conn.commit()
    return view(conn, mosque)


def import_gabarit(conn, mosque: dict, filename: str, content: bytes, user_id=None,
                   ip: str | None = None) -> dict:
    """Takes the texts of an `ecrans.html` template and keeps the file as the mosque's reference.

    Only non-empty texts the engine knows are applied; an empty value in the file never erases a
    text already saved (it means "no line in the mock-up", not "clear the setting"). Existing
    engine-only slots (title_cours, foot_cours...) that the template does not carry are kept.
    """
    from . import gabarit

    taken, ignored, empty = gabarit.parse(content, filename)
    merged = saved(conn, mosque["id"])
    for screen, slots in taken.items():
        merged.setdefault(screen, {}).update(slots)
    f = gabarit.store(mosque, content)
    try:
        vue = save(conn, mosque, merged, user_id, ip)
    except Exception:
        f.unlink(missing_ok=True)          # never keep a reference the texts were not applied from
        raise
    db.audit(conn, mosque["id"], user_id, "ecrans.gabarit", "mosque", mosque["id"], ip=ip,
             fichier=f"ecrans{gabarit.EXT}",
             repris=sum(len(v) for v in taken.values()), ignores=len(ignored), vides=len(empty))
    conn.commit()
    return {"taken": taken, "ignored": ignored, "empty": empty, "screens": vue,
            "gabarit": gabarit.view(mosque), "message": gabarit.summary(taken, ignored, empty)}


def valeurs_apercu(mosque: dict, screen: str) -> dict[str, str]:
    """Les valeurs des variables pour un aperçu : les mêmes que celles que le moteur emploiera.

    Le direct n'a pas de « prochaine classe » connue à la seconde près dans un aperçu ; on annonce
    donc un cours plausible, exactement comme le fait l'image du moteur, pour que les deux aperçus
    montrent le même texte.
    """
    now = dt.datetime.now(dt.timezone.utc).astimezone(db.timezone(mosque))
    nxt: Any = SimpleNamespace(starts=now + dt.timedelta(minutes=42), title="Tafsir de la sourate Al-Kahf",
                               kind="dars")
    moment = scenes.Moment(now=now, planned=now + dt.timedelta(minutes=17), next_event=nxt)
    return base.variables(moment, title=mosque.get("clip_title") or "",
                          mosque=mosque.get("name") or "",
                          heure=f"{moment.planned:%H} h {moment.planned:%M}",
                          cours=nxt.title, jour=f"{moment.planned:%A}".capitalize())


def preview(mosque: dict, payload, screen: str) -> bytes:
    """PNG (1280×720) of the screen as it will look, drawn by the engine's own renderer."""
    try:
        clean = base.check(payload or {})
    except ValueError as e:
        raise InvalidScreens(str(e)) from e
    if screen not in base.SCREENS or screen not in scenes.SCENES:
        raise InvalidScreens(f"écran inconnu : {screen}")
    logo = branding.path_for(mosque)
    cfg = Config(title=mosque.get("clip_title") or "", mosque_name=mosque.get("name") or "",
                 logo=str(logo) if logo and (mosque.get("logo_path") or "").strip() else "",
                 screen_texts=clean)
    now = dt.datetime.now(dt.timezone.utc).astimezone(db.timezone(mosque))
    # Stand-in for the next class: the preview announces a plausible one instead of a blank line.
    nxt: Any = SimpleNamespace(starts=now + dt.timedelta(minutes=42), title="Tafsir de la sourate Al-Kahf",
                              kind="dars")
    moment = scenes.Moment(now=now, planned=now + dt.timedelta(minutes=17), next_event=nxt)
    renderer = scenes.SceneRenderer(cfg)
    img = renderer.background().convert("RGBA")
    img.alpha_composite(renderer.content(screen, moment))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG")
    return out.getvalue()
