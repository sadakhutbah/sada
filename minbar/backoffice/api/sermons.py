"""Khutbas: list, detail, speaker, transcript corrections and import."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from .. import auth, content, db, importer, traitement
from ..context import CtxDep, Invalid, khutba_or_404, imam_filter
from ...pipeline import verses as verses_mod
from ...pipeline.output import SessionOutput
from .common import _a_table, _ok

router = APIRouter()


@router.get("/sermons/mois")
def sermon_months(ctx: CtxDep):
    """The months containing khutbas, from the most recent to the oldest, with their count.

    The period selector is built on what the mosque actually has: no empty month,
    no calendar to browse, and no date to guess."""
    where, args = imam_filter(ctx)
    items = db.all_(ctx.conn, f"SELECT substr(k.date, 1, 7) mois, COUNT(*) n FROM khutbas k "
                              f"LEFT JOIN users u ON u.id = k.imam_id "
                              f"WHERE {where} AND k.date IS NOT NULL AND k.date != '' "
                              f"GROUP BY mois ORDER BY mois DESC", *args)
    return {"items": items}


@router.get("/sermons")
def sermons(ctx: CtxDep, q: str = "", mois: str = "", pending: int = 0,
            limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Khutbas, from the most recent to the oldest.

    A mosque accumulates years of khutbas: the list can be searched (`q` on the passage, the theme
    and the imam), restricted to a month (`mois` in YYYY-MM format, chosen from the list of existing
    months), filtered (`pending`: only those awaiting validation) and paginated.
    The filtered total is returned so that the front knows whether pages remain, without guessing."""
    where, args = imam_filter(ctx)
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
    perimetre, perimetre_args = imam_filter(ctx)
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
            "has_video": bool(k["video_path"]) and Path(k["video_path"]).exists()}


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
        raise Invalid(f"Import impossible : {e}") from e
    nk, nc = sum(r["khutbas_new"] for r in res), sum(r["clips_new"] for r in res)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "import.manuel", ip=ctx.ip, run=run or "tous", khutbas=nk, clips=nc)
    return _ok(f"Import : {len(res)} run(s) parcouru(s), {nk} khutba(s) et {nc} clip(s) ajoutés.",
               khutbas=nk, clips=nc)


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
        raise Invalid(f"Relance impossible : {e}") from e
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "traitement.relance", ip=ctx.ip, run=f.get("run"),
             session=f.get("session"))
    return _ok("Traitement remis en file : il démarre dans la minute.")


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
    return _ok("Khutba mise à jour.")


@router.post("/sermons/{kid}/revisions")
async def sermon_correction(kid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    n = content.save_correction(ctx.conn, k, int(f.get("seg", "-1")), f.get("field", ""), f.get("value", ""),
                                ctx.user, ctx.ip)
    return _ok("Correction enregistrée" + (f" ; {n} clip(s) à régénérer puis revalider." if n else "."),
               to_regenerate=n)
