"""Khutbas: list, detail, speaker, transcript corrections and import."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from .. import auth, content, db, import_video, importer, traitement
from ..context import CtxDep, Invalid, NotFound, khutba_or_404, imam_filter
from ...i18n import _
from ...pipeline import verses as verses_mod
from ...pipeline.output import SessionOutput
from .common import _a_table, _ok

router = APIRouter()

#: Natures de séance qu'on peut lister séparément : la page Khutbas demande `khutba`, la page Dars `dars`.
GENRES = ("khutba", "dars", "conference")
#: Une séance importée avant la colonne `kind` est une khutba.
GENRE_SQL = "COALESCE(NULLIF(k.kind, ''), 'khutba')"


def _filtre_genre(where: str, args: tuple, kind: str) -> tuple[str, tuple]:
    """Restreint à une ou plusieurs natures de séance (`kind=dars` ou `kind=dars,conference`) ;
    vide = toutes, valeur inconnue refusée."""
    genres = [g for g in (x.strip().lower() for x in (kind or "").split(",")) if g]
    if not genres:
        return where, args
    inconnus = [g for g in genres if g not in GENRES]
    if inconnus:
        raise Invalid(_("unknown kind: {kinds}", kinds=", ".join(inconnus)))
    return f"{where} AND {GENRE_SQL} IN ({', '.join('?' * len(genres))})", (*args, *genres)


@router.get("/sermons/mois")
def sermon_months(ctx: CtxDep, kind: str = ""):
    """The months containing khutbas, from the most recent to the oldest, with their count.

    The period selector is built on what the mosque actually has: no empty month,
    no calendar to browse, and no date to guess."""
    where, args = _filtre_genre(*imam_filter(ctx), kind)
    items = db.all_(ctx.conn, f"SELECT substr(k.date, 1, 7) mois, COUNT(*) n FROM khutbas k "
                              f"LEFT JOIN users u ON u.id = k.imam_id "
                              f"WHERE {where} AND k.date IS NOT NULL AND k.date != '' "
                              f"GROUP BY mois ORDER BY mois DESC", *args)
    return {"items": items}


@router.get("/sermons")
def sermons(ctx: CtxDep, q: str = "", mois: str = "", pending: int = 0, kind: str = "",
            limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Khutbas, from the most recent to the oldest.

    A mosque accumulates years of khutbas: the list can be searched (`q` on the passage, the theme
    and the imam), restricted to a month (`mois` in YYYY-MM format, chosen from the list of existing
    months), filtered (`pending`: only those awaiting validation) and paginated.
    The filtered total is returned so that the front knows whether pages remain, without guessing.
    `kind` (khutba, dars, conference) sépare les listes : un dars ne doit jamais apparaître parmi
    les khutbas, ni l'inverse ; le compteur « à valider » suit la même nature."""
    where, args = _filtre_genre(*imam_filter(ctx), kind)
    if q.strip():
        motif = f"%{q.strip()}%"
        where += " AND (k.label LIKE ? OR k.theme LIKE ? OR u.display_name LIKE ?)"
        args = (*args, motif, motif, motif)
    if mois and re.fullmatch(r"\d{4}-\d{2}", mois):
        where += " AND substr(k.date, 1, 7) = ?"
        args = (*args, mois)
    if pending:
        where += " AND EXISTS (SELECT 1 FROM clips c WHERE c.khutba_id = k.id AND c.status = 'a_valider')"
    total = (db.one(ctx.conn, f"SELECT COUNT(*) n FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id "
                              f"WHERE {where}", *args) or {"n": 0})["n"]
    # The tabs count on the user's scope, not on the displayed page.
    perimetre, perimetre_args = _filtre_genre(*imam_filter(ctx), kind)
    en_attente = (db.one(ctx.conn, f"SELECT COUNT(DISTINCT k.id) n FROM khutbas k "
                                   f"JOIN clips c ON c.khutba_id = k.id AND c.status = 'a_valider' "
                                   f"WHERE {perimetre}", *perimetre_args) or {"n": 0})["n"]
    rows = db.all_(ctx.conn, f"SELECT k.*, u.display_name imam, "
                             f"(SELECT COUNT(*) FROM clips c WHERE c.khutba_id = k.id) clips, "
                             f"(SELECT COUNT(*) FROM clips c WHERE c.khutba_id = k.id AND c.status = 'a_valider') pending "
                             f"FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id WHERE {where} "
                             f"ORDER BY k.date DESC, k.run_id DESC, k.session_index LIMIT ? OFFSET ?",
                    *args, limit, offset)
    return {"items": rows, "count": len(rows), "total": total, "offset": offset, "pending": en_attente}


@router.get("/sermons/{kid}")
def sermon(ctx: CtxDep, kid: int):
    k = khutba_or_404(ctx, kid)
    data = content.load(ctx.conn, k)
    segments = db.all_(ctx.conn, "SELECT * FROM segments WHERE khutba_id = ? ORDER BY idx", kid) \
        if _a_table(ctx.conn, "segments") else []
    clips = db.all_(ctx.conn, "SELECT * FROM clips WHERE khutba_id = ? ORDER BY idx", kid)
    notes = SessionOutput(k["output_dir"]).notes
    imam = db.one(ctx.conn, "SELECT display_name FROM users WHERE id = ?", k["imam_id"]) if k["imam_id"] else None
    return {"sermon": k, "segments": segments, "clips": clips,
            "transcript": content.transcript_rows(data),
            "notes": notes.read_text(encoding="utf-8") if notes.exists() else "",
            # Detected verses: the certain/short ones and the ambiguous passages, flagged "to check".
            "verses": verses_mod.for_display(data["verses"]), "imam": imam,
            "imams": auth.imams(ctx.conn, ctx.mosque["id"]) if ctx.can("assign") else [],
            "has_video": bool(k["video_path"]) and Path(k["video_path"]).exists(),
            # Clip chosen by hand: possible only if a source video can be cut (same circuit as regeneration).
            "manual_clip": _manual_clip(ctx, k, data)}


def _manual_clip(ctx, k: dict, data: dict) -> dict:
    if not ctx.can("validate"):
        return {"allowed": False, "reason": "", "kinds": [], "min": 0, "max": 0, "duration": None}
    ok, why = content.can_create_clip(ctx.conn, k)
    return {"allowed": ok, "reason": why, "kinds": list(content.CLIP_KINDS), "min": content.MIN_CLIP,
            "max": content.MAX_CLIP, "duration": k["duration_s"] or data.get("duration")}


@router.post("/import")
async def do_import(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("assign")
    run = (f.get("run") or "").strip()
    try:
        if run:
            res = [await run_in_threadpool(importer.import_run, ctx.conn, ctx.mosque, run)]
        else:
            res = await run_in_threadpool(importer.scan, ctx.conn, ctx.mosque)
    except (FileNotFoundError, ValueError) as e:
        raise Invalid(_("Import failed: {error}", error=e)) from e
    nk, nc = sum(r["khutbas_new"] for r in res), sum(r["clips_new"] for r in res)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "import.manuel", ip=ctx.ip, run=run or "tous", khutbas=nk, clips=nc)
    return _ok(_("Import: {runs} run(s) scanned, {khutbas} khutba(s) and {clips} clip(s) added.",
                 runs=len(res), khutbas=nk, clips=nc), khutbas=nk, clips=nc)


@router.get("/traitements")
async def traitements(ctx: CtxDep):
    items = await run_in_threadpool(traitement.liste, ctx.mosque)
    return {"items": items, "actif": any(i["etat"] in ("attente", "en_cours") for i in items)}


@router.post("/traitements/relancer")
async def traitement_relancer(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("assign")
    try:
        traitement.relancer(ctx.mosque, str(f.get("run") or ""), int(f.get("session")))
    except (FileNotFoundError, ValueError, TypeError) as e:
        raise Invalid(_("Retry failed: {error}", error=e)) from e
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "traitement.relance", ip=ctx.ip, run=f.get("run"),
             session=f.get("session"))
    return _ok(_("Processing queued again: it starts within a minute."))


@router.post("/imports/video")
async def video_import(ctx: CtxDep):
    """Vidéo enregistrée hors direct, envoyée brute (pas de multipart : plusieurs Go en flux)."""
    ctx.need_csrf()
    ctx.require("assign")
    meta = dict(ctx.request.query_params)
    try:
        run = await import_video.recevoir(ctx.mosque, meta, ctx.request.stream(),
                                          log=lambda m: print("[import]", m, flush=True))
    except import_video.ImportRefuse as e:
        raise Invalid(str(e)) from e
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "import.video", ip=ctx.ip, run=run,
             type=meta.get("kind") or "dars", titre=meta.get("title") or "")
    return _ok(_("Video received: processing starts, follow it on the dashboard."), run=run)


@router.post("/sermons/{kid}/speaker")
async def sermon_speaker(kid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("assign")
    k = khutba_or_404(ctx, kid)
    iid = auth.imam_id(ctx.conn, ctx.mosque["id"], f.get("imam_id"))
    if f.get("imam_id") and iid is None:
        raise NotFound()
    speaker = (f.get("speaker") or "").strip()[:120]
    ctx.conn.execute("UPDATE khutbas SET imam_id = ?, speaker = ? WHERE id = ?", (iid, speaker, k["id"]))
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "khutba.imam", "khutba", k["id"], ip=ctx.ip,
             imam=iid, intervenant=speaker or None)
    return _ok(_("Khutba updated."))


@router.post("/sermons/{kid}/revisions")
async def sermon_correction(kid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    n = content.save_correction(ctx.conn, k, int(f.get("seg", "-1")), f.get("field", ""), f.get("value", ""),
                                ctx.user, ctx.ip)
    return _ok(_("Correction saved; {n} clip(s) to regenerate then validate again.", n=n) if n
               else _("Correction saved."), to_regenerate=n)


def _nombre(v: str, nom: str) -> float:
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        raise ValueError(_("Invalid {field} (a number of seconds is expected).", field=nom)) from None


@router.post("/sermons/{kid}/subtitles")
async def sermon_subtitle_add(kid: int, ctx: CtxDep):
    """Adds a subtitle at a precise instant (session time, in seconds)."""
    f = await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    n = content.add_subtitle(ctx.conn, k, _nombre(f.get("start", ""), _("start")), _nombre(f.get("end", ""), _("end")),
                             f.get("text", ""), ctx.user, ctx.ip)
    return _ok(_("Subtitle added; {n} clip(s) to regenerate then validate again.", n=n) if n
               else _("Subtitle added."), to_regenerate=n)


@router.post("/sermons/{kid}/subtitles/{sid}/delete")
async def sermon_subtitle_remove(kid: int, sid: int, ctx: CtxDep):
    await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    n = content.remove_subtitle(ctx.conn, k, sid, ctx.user, ctx.ip)
    return _ok(_("Subtitle removed; {n} clip(s) to regenerate then validate again.", n=n) if n
               else _("Subtitle removed."), to_regenerate=n)
