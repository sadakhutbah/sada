"""Khutbas: list, detail, speaker, transcript corrections and import."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Query
from sqlalchemy import and_, distinct, func, or_, outerjoin, select, update
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from ...i18n import _
from ...pipeline import verses as verses_mod
from ...pipeline.output import SessionOutput
from .. import (
    auth,
    content,
    db,
    habillage,
    import_video,
    importer,
    orm,
    states,
    traduction,
    traitement,
    transcript_export,
)
from ..context import CtxDep, Invalid, NotFound, imam_filter, khutba_or_404
from ..jobs import JobStatus
from ..models import Clip, Khutba, User
from .common import _ok, _segments

router = APIRouter()

#: Natures de séance qu'on peut lister séparément : la page Khutbas demande `khutba`, la page Dars `dars`.
GENRES = ("khutba", "dars", "conference", "tarawih")
#: Une séance importée avant la colonne `kind` est une khutba.
GENRE = func.coalesce(func.nullif(Khutba.kind, ""), "khutba")


def _filtre_genre(where: list, kind: str) -> list:
    """Restreint à une ou plusieurs natures de séance (`kind=dars` ou `kind=dars,conference`) ;
    vide = toutes sauf les tarawih (des replays, demandés seulement par leur nature), valeur inconnue
    refusée."""
    genres = [g for g in (x.strip().lower() for x in (kind or "").split(",")) if g]
    if not genres:
        return [*where, GENRE != "tarawih"]
    inconnus = [g for g in genres if g not in GENRES]
    if inconnus:
        raise Invalid(_("unknown kind: {kinds}", kinds=", ".join(inconnus)))
    return [*where, GENRE.in_(genres)]


def _clips_de(*where):
    """Nombre de clips de la séance courante (sous-requête corrélée)."""
    return select(func.count()).select_from(Clip).where(Clip.khutba_id == Khutba.id, *where).scalar_subquery()


@router.get("/sermons/mois")
def sermon_months(ctx: CtxDep, kind: str = ""):
    """The months containing khutbas, from the most recent to the oldest, with their count.

    The period selector is built on what the mosque actually has: no empty month,
    no calendar to browse, and no date to guess."""
    mois = func.substr(Khutba.date, 1, 7).label("mois")
    stmt = (
        select(mois, func.count().label("n"))
        .where(*_filtre_genre(imam_filter(ctx), kind), Khutba.date.is_not(None), Khutba.date != "")
        .group_by(mois)
        .order_by(mois.desc())
    )
    return {"items": orm.rows(ctx.conn, stmt)}


@router.get("/sermons")
def sermons(
    ctx: CtxDep,
    q: str = "",
    mois: str = "",
    pending: int = 0,
    kind: str = "",
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Khutbas, from the most recent to the oldest.

    A mosque accumulates years of khutbas: the list can be searched (`q` on the passage, the theme
    and the imam), restricted to a month (`mois` in YYYY-MM format, chosen from the list of existing
    months), filtered (`pending`: only those awaiting validation) and paginated.
    The filtered total is returned so that the front knows whether pages remain, without guessing.
    `kind` (khutba, dars, conference, tarawih) sépare les listes : un dars ne doit jamais apparaître parmi
    les khutbas, ni l'inverse ; le compteur « à valider » suit la même nature."""
    perimetre = _filtre_genre(imam_filter(ctx), kind)
    where = list(perimetre)
    if q.strip():
        motif = f"%{q.strip()}%"
        where.append(or_(Khutba.label.like(motif), Khutba.theme.like(motif), User.display_name.like(motif)))
    if mois and re.fullmatch(r"\d{4}-\d{2}", mois):
        where.append(func.substr(Khutba.date, 1, 7) == mois)
    if pending:
        where.append(select(Clip.id).where(Clip.khutba_id == Khutba.id, Clip.status == "a_valider").exists())
    imam = outerjoin(Khutba, User, User.id == Khutba.imam_id)
    total = ctx.conn.scalar(select(func.count()).select_from(imam).where(*where)) or 0
    # The tabs count on the user's scope, not on the displayed page.
    en_attente = (
        ctx.conn.scalar(
            select(func.count(distinct(Khutba.id)))
            .select_from(Khutba)
            .join(Clip, and_(Clip.khutba_id == Khutba.id, Clip.status == "a_valider"))
            .where(*perimetre)
        )
        or 0
    )
    stmt = (
        select(
            Khutba.__table__,
            User.display_name.label("imam"),
            _clips_de().label("clips"),
            _clips_de(Clip.status == "a_valider").label("pending"),
        )
        .select_from(imam)
        .where(*where)
        .order_by(Khutba.date.desc(), Khutba.run_id.desc(), Khutba.session_index)
        .limit(limit)
        .offset(offset)
    )
    rows = orm.rows(ctx.conn, stmt)
    return {"items": rows, "count": len(rows), "total": total, "offset": offset, "pending": en_attente}


@router.get("/tarawih/nights/{kid}")
def tarawih_night(ctx: CtxDep, kid: int):
    """A tarawih night as a replay: the player and the reciters (chapters when the control room
    switched them). Neither transcript, clips nor translation."""
    k = khutba_or_404(ctx, kid, tarawih=True)
    if k["kind"] != "tarawih":
        raise NotFound()
    try:
        chapters = json.loads(k["chapters"] or "{}")
    except ValueError:
        chapters = {}
    return {
        "night": {
            key: k[key] for key in ("id", "label", "date", "start_clock", "end_clock", "duration_s", "session_no")
        },
        # an audio-only night is replayed over its fixed slate, never with the camera
        "mode": k["series"] or "audio",
        "reciters": chapters.get("reciters") or [n for n in (k["speaker"] or "").split(", ") if n],
        "segments": chapters.get("segments") or [],
        "has_video": bool(k["video_path"]) and Path(k["video_path"]).exists(),
    }


@router.get("/sermons/{kid}/transcript")
def sermon_transcript(kid: int, ctx: CtxDep, format: str = "txt", timing: str | None = None):
    """Timestamped transcript (original text + translation) as a download: `format=txt` or `srt`.

    Read-only, same visibility as the khutba itself. `timing=video` (default when a branded video exists)
    aligns the times on that video, with the same shift as its subtitle tracks (opening card and cut);
    `timing=raw`, or no branded video, keeps the session recording's times."""
    mode = (timing or "").strip().lower()
    if mode and mode not in transcript_export.TIMINGS:
        raise Invalid(_("unknown timing: {timing}", timing=timing))
    fmt = format.strip().lower()
    if fmt not in transcript_export.FORMATS:
        raise Invalid(_("unknown format: {format}", format=format))
    k = khutba_or_404(ctx, kid)
    rows = content.transcript_rows(content.load(ctx.conn, k))
    aligned = mode != "raw" and habillage.version_habillee(ctx.conn, k) is not None
    shift = habillage.alignements(ctx.conn, k)["habille"] if aligned else 0.0
    media, ext = transcript_export.FORMATS[fmt]
    name = transcript_export.filename(k["label"], kid, ext)
    return Response(
        transcript_export.render(rows, fmt, shift),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-cache",
            "X-Sada-Timing": "video" if aligned else "raw",
        },
    )


@router.get("/sermons/{kid}")
def sermon(ctx: CtxDep, kid: int):
    k = khutba_or_404(ctx, kid)
    data = content.load(ctx.conn, k)
    segments = _segments(ctx.conn, kid)
    clips = orm.rows(ctx.conn, select(Clip).where(Clip.khutba_id == kid).order_by(Clip.idx))
    notes = SessionOutput(k["output_dir"]).notes
    imam = orm.first(ctx.conn, User, id=k["imam_id"]) if k["imam_id"] else None
    titres = habillage.langues(ctx.conn, k, data)
    return {
        "state": _sermon_state(ctx, k, clips),
        "sermon": k,
        "segments": segments,
        "clips": clips,
        "transcript": content.transcript_rows(data),
        "notes": notes.read_text(encoding="utf-8") if notes.exists() else "",
        # Detected verses: the certain/short ones and the ambiguous passages, flagged "to check".
        "verses": verses_mod.for_display(data["verses"]),
        "imam": imam,
        "imams": auth.imams(ctx.conn, ctx.mosque["id"]) if ctx.can("assign") else [],
        "has_video": bool(k["video_path"]) and Path(k["video_path"]).exists(),
        # Dressed version ready: it becomes the video shown, the raw one stays available as history.
        "dressed": habillage.version_habillee(ctx.conn, k),
        # Languages the player can show: what the transcript holds, dressed video or not. A language
        # still being translated is served with the passages it already has, labelled in the menu.
        "subtitles": list(titres["passages"]),
        "subtitles_state": titres["languages"],
        "translation": traduction.etat(k),
        # Clip chosen by hand: possible only if a source video can be cut (same circuit as regeneration).
        "manual_clip": _manual_clip(ctx, k, data),
    }


def _sermon_state(ctx, k: dict, clips: list[dict]) -> dict:
    """Post-live processing of this khutba (if it came from a run) plus what its clips need."""
    processing = None
    if k.get("run_id") and k.get("session_index") is not None:
        run_dir = db.mosque_dirs(ctx.mosque)["state"] / k["run_id"]
        processing = traitement.lire(run_dir).get(str(k["session_index"]))
    branded = states.habillage_state(habillage.ligne(ctx.conn, k["id"]), habillage.fichier_video(k).exists())
    return states.sermon_state(processing, [states.clip_state(c) for c in clips], branded)


def _manual_clip(ctx, k: dict, data: dict) -> dict:
    if not ctx.can("validate"):
        return {"allowed": False, "reason": "", "kinds": [], "min": 0, "max": 0, "duration": None}
    ok, why = content.can_create_clip(ctx.conn, k)
    return {
        "allowed": ok,
        "reason": why,
        "kinds": list(content.CLIP_KINDS),
        "min": content.MIN_CLIP,
        "max": content.MAX_CLIP,
        "duration": k["duration_s"] or data.get("duration"),
    }


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
    db.audit(
        ctx.conn, ctx.mosque["id"], ctx.user["id"], "import.manuel", ip=ctx.ip, run=run or "tous", khutbas=nk, clips=nc
    )
    return _ok(
        _(
            "Import: {runs} run(s) scanned, {khutbas} khutba(s) and {clips} clip(s) added.",
            runs=len(res),
            khutbas=nk,
            clips=nc,
        ),
        khutbas=nk,
        clips=nc,
    )


@router.get("/traitements")
async def traitements(ctx: CtxDep):
    items = await run_in_threadpool(traitement.liste, ctx.mosque)
    return {"items": items, "actif": any(i["etat"] in (JobStatus.PENDING, JobStatus.RUNNING) for i in items)}


@router.post("/traitements/relancer")
async def traitement_relancer(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("assign")
    try:
        traitement.relancer(ctx.mosque, str(f.get("run") or ""), int(f.get("session")))
    except (FileNotFoundError, ValueError, TypeError) as e:
        raise Invalid(_("Retry failed: {error}", error=e)) from e
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "traitement.relance",
        ip=ctx.ip,
        run=f.get("run"),
        session=f.get("session"),
    )
    return _ok(_("Processing queued again: it starts within a minute."))


@router.post("/imports/video")
async def video_import(ctx: CtxDep):
    """Vidéo enregistrée hors direct, envoyée brute (pas de multipart : plusieurs Go en flux)."""
    ctx.need_csrf()
    ctx.require("assign")
    meta = dict(ctx.request.query_params)
    try:
        run = await import_video.recevoir(
            ctx.mosque, meta, ctx.request.stream(), log=lambda m: print("[import]", m, flush=True)
        )
    except import_video.ImportRefuse as e:
        raise Invalid(str(e)) from e
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "import.video",
        ip=ctx.ip,
        run=run,
        type=meta.get("kind") or "dars",
        titre=meta.get("title") or "",
    )
    return _ok(_("Video received: processing starts, follow it on the dashboard."), run=run)


def _envoi_erreur(e: Exception):
    if isinstance(e, import_video.EnvoiInconnu):
        raise NotFound() from e
    if isinstance(e, import_video.EnvoiDecale):
        return JSONResponse({"ok": False, "message": _("Out of sequence."), "received": e.recu}, status_code=409)
    raise Invalid(str(e)) from e


@router.post("/imports/video/uploads")
async def video_upload_open(ctx: CtxDep):
    """Envoi par morceaux (≤ 90 Mo : limite du proxy Cloudflare) : ouvre l'envoi."""
    ctx.need_csrf()
    ctx.require("assign")
    f = await ctx.body()
    try:
        size = int(f.get("size") or 0)
        return _ok("", **import_video.envoi_ouvrir(ctx.mosque, ctx.user["id"], f, size))
    except (import_video.ImportRefuse, ValueError) as e:
        raise Invalid(str(e)) from e


@router.get("/imports/video/uploads/{uid}")
async def video_upload_state(uid: str, ctx: CtxDep):
    ctx.require("assign")
    try:
        return _ok("", **import_video.envoi_etat(ctx.mosque, ctx.user["id"], uid))
    except import_video.EnvoiInconnu as e:
        raise NotFound() from e


@router.put("/imports/video/uploads/{uid}")
async def video_upload_chunk(uid: str, ctx: CtxDep, offset: int = Query(..., ge=0)):
    ctx.need_csrf()
    ctx.require("assign")
    try:
        recu = await import_video.envoi_morceau(ctx.mosque, ctx.user["id"], uid, offset, ctx.request.stream())
    except (import_video.EnvoiInconnu, import_video.EnvoiDecale, import_video.ImportRefuse) as e:
        return _envoi_erreur(e)
    return _ok("", received=recu)


@router.delete("/imports/video/uploads/{uid}")
async def video_upload_cancel(uid: str, ctx: CtxDep):
    ctx.need_csrf()
    ctx.require("assign")
    try:
        import_video.envoi_annuler(ctx.mosque, ctx.user["id"], uid)
    except import_video.EnvoiInconnu as e:
        raise NotFound() from e
    return _ok("")


@router.post("/imports/video/uploads/{uid}/complete")
async def video_upload_complete(uid: str, ctx: CtxDep):
    ctx.need_csrf()
    ctx.require("assign")
    try:
        meta = import_video._envoi_meta(db.mosque_dirs(ctx.mosque), uid, ctx.user["id"])[1]["meta"]
        run = await run_in_threadpool(
            import_video.envoi_finir, ctx.mosque, ctx.user["id"], uid, lambda m: print("[import]", m, flush=True)
        )
    except (import_video.EnvoiInconnu, import_video.EnvoiDecale, import_video.ImportRefuse) as e:
        return _envoi_erreur(e)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "import.video",
        ip=ctx.ip,
        run=run,
        type=meta.get("kind") or "dars",
        titre=meta.get("title") or "",
    )
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
    ctx.conn.execute(update(Khutba).where(Khutba.id == k["id"]).values(imam_id=iid, speaker=speaker))
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "khutba.imam",
        "khutba",
        k["id"],
        ip=ctx.ip,
        imam=iid,
        intervenant=speaker or None,
    )
    return _ok(_("Khutba updated."))


@router.post("/sermons/{kid}/revisions")
async def sermon_correction(kid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    n = content.save_correction(
        ctx.conn, k, int(f.get("seg", "-1")), f.get("field", ""), f.get("value", ""), ctx.user, ctx.ip
    )
    return _ok(
        _("Correction saved; {n} clip(s) to regenerate then validate again.", n=n) if n else _("Correction saved."),
        to_regenerate=n,
    )


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
    n = content.add_subtitle(
        ctx.conn,
        k,
        _nombre(f.get("start", ""), _("start")),
        _nombre(f.get("end", ""), _("end")),
        f.get("text", ""),
        ctx.user,
        ctx.ip,
    )
    return _ok(
        _("Subtitle added; {n} clip(s) to regenerate then validate again.", n=n) if n else _("Subtitle added."),
        to_regenerate=n,
    )


@router.post("/sermons/{kid}/subtitles/{sid}/delete")
async def sermon_subtitle_remove(kid: int, sid: int, ctx: CtxDep):
    await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    n = content.remove_subtitle(ctx.conn, k, sid, ctx.user, ctx.ip)
    return _ok(
        _("Subtitle removed; {n} clip(s) to regenerate then validate again.", n=n) if n else _("Subtitle removed."),
        to_regenerate=n,
    )
