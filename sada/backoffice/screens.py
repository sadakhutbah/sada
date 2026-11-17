"""Texts of the live screens (slates): storage per mosque, view, preview and validation.

The defaults live in `sada.screens` — the module the engine renders with — so the interface shows
exactly what will be broadcast, and the preview is drawn by `sada.scenes` itself. Only what the
mosque changes is stored; an emptied field goes back to the default instead of leaving a blank line
on screen.
"""

from __future__ import annotations

import datetime as dt
import io
import re

from PIL import Image
from sqlalchemy import delete, select

from .. import events as events_mod
from .. import scenes
from .. import screens as base
from ..config import Config
from ..events import KHUTBA_TITLE
from ..i18n import _
from . import branding, db, events_store, live, mawaqit_store, orm, variables_ecran
from .models import ScreenText


class InvalidScreens(ValueError):
    """Payload refused: unknown screen or field, or a line too long."""


def saved(conn, mosque_id: int) -> dict[str, dict[str, str]]:
    """What the mosque changed: {écran: {champ: texte}}.

    The reserved screen `_commun` (common template texts: Arabic lines) is not one of the engine's
    six screens and stays out of this view.
    """
    out: dict[str, dict[str, str]] = {}
    for r in orm.rows(
        conn,
        select(ScreenText.screen, ScreenText.slot, ScreenText.text).where(
            ScreenText.mosque_id == mosque_id, ScreenText.screen != variables_ecran.SCREEN
        ),
    ):
        out.setdefault(r["screen"], {})[r["slot"]] = r["text"]
    return out


def view(conn, mosque: dict) -> dict[str, dict[str, dict[str, str]]]:
    """Screen → slot, with the default, what is saved (empty = untouched) and the text in force."""
    keep = saved(conn, mosque["id"])
    eff = base.effective(keep)
    return {
        s: {
            slot: {"default": base.DEFAULTS[s][slot], "value": keep.get(s, {}).get(slot, ""), "text": eff[s][slot]}
            for slot in base.DEFAULTS[s]
        }
        for s in base.SCREENS
    }


def save(conn, mosque: dict, payload, user_id=None, ip: str | None = None) -> dict:
    """Replaces the whole set at once (a form sends every field it shows)."""
    try:
        clean = base.check(payload or {})
    except ValueError as e:
        raise InvalidScreens(str(e)) from e
    # Only the six engine screens: the common texts (`_commun`) have their own save route.
    conn.execute(
        delete(ScreenText).where(ScreenText.mosque_id == mosque["id"], ScreenText.screen != variables_ecran.SCREEN)
    )
    orm.done(conn)
    for screen, slots in clean.items():
        for slot, text in slots.items():
            if text:  # empty = default, nothing stored
                conn.add(
                    ScreenText(mosque_id=mosque["id"], screen=screen, slot=slot, text=text, updated_at=db.now_iso())
                )
                conn.flush()
    db.audit(
        conn,
        mosque["id"],
        user_id,
        "ecrans.configure",
        "mosque",
        mosque["id"],
        ip=ip,
        champs=[f"{s}.{k}" for s, v in clean.items() for k, t in v.items() if t],
    )
    conn.commit()
    return view(conn, mosque)


def import_gabarit(conn, mosque: dict, filename: str, content: bytes, user_id=None, ip: str | None = None) -> dict:
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
        f.unlink(missing_ok=True)  # never keep a reference the texts were not applied from
        raise
    db.audit(
        conn,
        mosque["id"],
        user_id,
        "ecrans.gabarit",
        "mosque",
        mosque["id"],
        ip=ip,
        fichier=f"ecrans{gabarit.EXT}",
        repris=sum(len(v) for v in taken.values()),
        ignores=len(ignored),
        vides=len(empty),
    )
    conn.commit()
    return {
        "taken": taken,
        "ignored": ignored,
        "empty": empty,
        "screens": vue,
        "gabarit": gabarit.view(mosque),
        "message": gabarit.summary(taken, ignored, empty),
    }


# A dars of the programme is taken as in progress for this long when it gives no length.
DARS_MIN_DEFAUT = 90
HHMM = re.compile(r"(\d{1,2}):(\d{2})$")


def moment_reel(mosque: dict, conn=None) -> scenes.Moment:
    """The moment the engine would show now, from the mosque's real data — nothing made up.

    Planned session: the one the live reports, else the next jumu'a or programme entry of the day.
    Class in progress and next class: from the programme. What is not known stays empty, and the
    slots that need it disappear, exactly as on air.
    """
    # Naive local time, like the engine's clock and the programme's instances.
    now = dt.datetime.now(dt.UTC).astimezone(db.timezone(mosque)).replace(tzinfo=None)
    if conn is None:
        return scenes.Moment(now=now)
    try:
        times = mawaqit_store.times(conn, mosque)
    except Exception:  # site down and no cache: no jumu'a time known
        times = None
    programme = []
    for i, raw in enumerate(events_store.read(conn, mosque["id"])):
        try:
            programme.append(events_mod.parse(raw, i))
        except ValueError:
            continue
    today = events_mod.instances_on(programme, now.date(), times)
    en_cours = next(
        (e for e in today if e.starts <= now < e.starts + dt.timedelta(minutes=e.max_min or DARS_MIN_DEFAUT)),
        None,
    )
    nxt = events_mod.next_instances(programme, now, count=1, times_for=lambda _d: times)
    return scenes.Moment(
        now=now,
        planned=_prevue(mosque, now, times, today),
        event=en_cours.as_event() if en_cours else None,
        next_event=nxt[0] if nxt else None,
    )


def _prevue(mosque: dict, now: dt.datetime, times, today) -> dt.datetime | None:
    v = live.status_cached(mosque)
    session = v.get("session") if v.get("ok") is not False else None
    m = HHMM.search(str((session or {}).get("planned") or ""))
    if m:
        return now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    candidats = [e.starts for e in today if e.starts >= now]
    if now.weekday() == 4 and times:
        for hhmm in times.get("jumua_list") or []:
            m = HHMM.search(str(hhmm))
            if m:
                t = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
                if t >= now:
                    candidats.append(t)
    return min(candidats) if candidats else None


def valeurs_apercu(mosque: dict, screen: str, conn=None) -> dict[str, str]:
    """Les valeurs des variables pour un aperçu : celles que le moteur poserait maintenant.

    Rien n'est inventé : sans séance prévue, pas d'heure ; sans dars au programme, pas de
    « prochain cours » ; le bandeau du dars reprend la séance en cours, sinon « Dars » seul.
    """
    moment = moment_reel(mosque, conn)
    nxt = moment.next_event
    valeurs = base.variables(
        moment,
        title=KHUTBA_TITLE,
        mosque=mosque.get("name") or "",
        heure=f"{moment.planned:%H} h {moment.planned:%M}" if moment.planned else "",
        cours=getattr(nxt, "title", "") or "",
        jour=f"{moment.planned:%A}".capitalize() if moment.planned else "",
    )
    valeurs["logo"] = branding.url(mosque) if (mosque.get("logo_path") or "").strip() else ""
    arabes = variables_ecran.lire(conn, mosque["id"]) if conn is not None else {}
    slot = {"khutba": "arabe_khutba", "dars": "arabe_dars"}.get(screen, "")
    valeurs["arabe"] = arabes.get(slot, "") if slot else ""
    valeurs.update(ligne2="", orateur="", serie="", seance="")
    if screen == "khutba":
        valeurs["ligne2"] = valeurs["mosquee"]
    elif screen == "dars":
        ev = moment.event
        if ev is None:
            valeurs["titre"] = "Dars"
            valeurs["ligne2"] = valeurs["mosquee"]
        else:
            valeurs["titre"] = ev.get("title") or "Dars"
            bits = [valeurs["mosquee"]] if valeurs["mosquee"] else []
            if ev.get("speaker"):
                valeurs["orateur"] = ev["speaker"]
                bits.append(ev["speaker"])
            no = ev.get("session_no")
            if ev.get("series"):
                valeurs["serie"] = ev["series"]
                valeurs["seance"] = str(no) if no else ""
                bits.append(ev["series"] + (f" · séance {no}" if no else ""))
            valeurs["ligne2"] = "  ·  ".join(bits)
    return valeurs


def preview(mosque: dict, payload, screen: str, overlay: bool = False, conn=None) -> bytes:
    """PNG (1280×720) of the screen as it will look, drawn by the engine's own renderer.

    `overlay`: for the banner scenes (camera behind), the banner alone on a transparent background,
    so the control room can lay it over the live feed.
    """
    try:
        clean = base.check(payload or {})
    except ValueError as e:
        raise InvalidScreens(str(e)) from e
    if screen not in scenes.SCENES:
        raise InvalidScreens(_("unknown screen: {screen}", screen=screen))
    logo = branding.path_for(mosque)
    cfg = Config(
        mosque_name=mosque.get("name") or "",
        logo=str(logo) if logo and (mosque.get("logo_path") or "").strip() else "",
        screen_texts=clean,
    )
    moment = moment_reel(mosque, conn)
    renderer = scenes.SceneRenderer(cfg)
    spec = scenes.SCENES[screen]
    if spec.full:
        img = renderer.background().convert("RGBA")
    elif overlay:
        img = Image.new("RGBA", (renderer.W, renderer.H), (0, 0, 0, 0))
    else:
        # Bandeau du direct : la caméra est derrière le bandeau, l'aperçu pose donc ce dernier sur
        # un fond neutre — pas sur le fond vert des écrans pleins, qui laisserait croire le contraire.
        img = Image.new("RGBA", (renderer.W, renderer.H), (24, 26, 24, 255))
    img.alpha_composite(renderer.content(screen, moment))
    out = io.BytesIO()
    (img if overlay and not spec.full else img.convert("RGB")).save(out, "PNG")
    return out.getvalue()
