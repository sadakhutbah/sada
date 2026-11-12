"""Clips: validation queue, detail sheet, decision, regeneration and publishing."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ...i18n import _
from .. import content, db, publish
from ..context import Ctx, CtxDep, clip_or_404, imam_filter, khutba_or_404
from .common import _a_table, _ok

router = APIRouter()


def _clip_detail(ctx: Ctx, cid: int) -> dict:
    c, k = clip_or_404(ctx, cid)
    data = content.load(ctx.conn, k)
    rows = content.transcript_rows(data, c["start"], c["end"])
    regen_ok, regen_why = content.can_regenerate(c)
    hist = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                             "WHERE a.mosque_id = ? AND ((a.object_type = 'clip' AND a.object_id = ?) OR "
                             "(a.object_type = 'khutba' AND a.object_id = ? AND a.action LIKE 'correction.%')) "
                             "ORDER BY a.id DESC LIMIT 50", ctx.mosque["id"], c["id"], k["id"])
    pubs = db.all_(ctx.conn, "SELECT p.*, u.display_name who FROM publications p LEFT JOIN users u ON u.id = p.user_id "
                             "WHERE p.clip_id = ? ORDER BY p.id DESC", c["id"]) if _a_table(ctx.conn, "publications") else []
    refs = [r["verse"]["ref"] for r in rows if r["verse"] and not r["verse"]["ambiguous"]]
    # Context: an excerpt taken out of its khutba does not make sense on its own. We give the theme,
    # the verses carried by the clip and the two phrases preceding it (a reminder of where the excerpt starts).
    complet = content.transcript_rows(data)
    avant = [{"start": r["start"], "texte": ((r["verse"]["fr"] if r["verse"] else None) or r.get("fr") or r["text"] or "")}
             for r in complet if r["end"] <= c["start"] + 0.5 and (r["verse"] or r.get("fr"))][-2:]
    versets = [{"ref": r["verse"]["ref"], "surah": r["verse"].get("surah", ""), "fr": r["verse"]["fr"]}
               for r in complet if r["verse"] and r["end"] > c["start"] and r["start"] < c["end"]]
    return {
        "clip": c, "sermon": k, "segments": rows, "history": hist, "publications": pubs,
        "can_regenerate": regen_ok, "regenerate_reason": regen_why,
        "can_validate": content.can_validate(c)[0],
        # Bounds: moved on the khutba video (session time), not on the rendered clip.
        "can_adjust": c["status"] != "publie" and c["regen_state"] != "en_cours" and regen_ok,
        "sermon_video": bool(k["video_path"]) and Path(k["video_path"]).exists(),
        "clip_limits": {"min": content.MIN_CLIP, "max": content.MAX_CLIP,
                        "duration": k["duration_s"] or data.get("duration")},
        "can_publish": publish.can_publish(c)[0],
        "connectors": publish.connector_rows(ctx.conn, ctx.mosque),
        "caption": ((c["title"] + "\n") if c["title"] else "")
                   + publish.default_caption(k, ctx.mosque, list(dict.fromkeys(refs))),
        "contexte": {"theme": k["theme"] or "", "avant": avant, "versets": versets},
        "exists": bool(c["file_path"]) and Path(c["file_path"]).exists(),
    }


@router.get("/clips")
def clips(ctx: CtxDep, status: str = "", limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    where, args = imam_filter(ctx)
    extra, extra_args = ("AND c.status = ?", (status,)) if status else ("", ())
    rows = db.all_(ctx.conn, f"SELECT c.*, c.title clip_title, k.label sermon, k.date, k.title, k.session_no "
                             f"FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                             f"WHERE {where} {extra} ORDER BY c.id DESC LIMIT ? OFFSET ?", *args, *extra_args, limit, offset)
    # Counts per status for the filters ("To validate (8)"): counted on the whole mosque,
    # otherwise the first filter chosen would erase the other figures.
    par_statut = {r["status"]: r["n"] for r in db.all_(
        ctx.conn, f"SELECT c.status, COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                  f"WHERE {where} GROUP BY c.status", *args)}
    # The filtered total lets the front know how many rows remain, instead of guessing from the page size.
    total = (db.one(ctx.conn, f"SELECT COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                              f"WHERE {where} {extra}", *args, *extra_args) or {}).get("n", 0)
    return {"items": rows, "count": len(rows), "total": total, "offset": offset,
            "counts": par_statut, "tabs": content.CLIP_STATUSES}


@router.get("/clips/{cid}")
def clip(cid: int, ctx: CtxDep):
    return _clip_detail(ctx, cid)


@router.post("/clips/{cid}/decision")
async def clip_decision(cid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("validate")
    c, _k = clip_or_404(ctx, cid)
    if f.get("decision") == "rouvrir":
        content.reopen(ctx.conn, c, ctx.user, ctx.ip)
        return _ok(_("Clip put back in the validation queue."), status="a_valider")
    content.decide(ctx.conn, c, f.get("decision", ""), ctx.user, f.get("reason", ""), ctx.ip)
    return _ok(_("Clip validated.") if f.get("decision") == "valide" else _("Clip rejected."),
               status=f.get("decision"))


@router.post("/clips/{cid}/regenerate")
async def clip_regenerate(cid: int, ctx: CtxDep):
    await ctx.body()
    ctx.require("validate")
    c, _k = clip_or_404(ctx, cid)
    content.start_regeneration(ctx.conn, ctx.request.app.state.db_path, c, ctx.user, ctx.ip,
                               background=ctx.request.app.state.regen_background)
    return _ok(_("Regeneration started (about a minute): the sheet updates by itself."), regen_state="en_cours")


@router.post("/clips/{cid}/publish")
async def clip_publish(cid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("publish")
    c, _k = clip_or_404(ctx, cid)
    res = await run_in_threadpool(publish.publish, ctx.conn, c, f.get("connector", ""), ctx.user,
                                  f.get("caption", ""), ctx.ip)
    if not res.ok:
        return JSONResponse({"error": _("refused"), "message": _("Publication refused: {reason}", reason=res.message)},
                            status_code=422)
    return _ok(_("Published: {location}", location=res.location), location=res.location)


@router.post("/sermons/{kid}/clips")
async def clip_create(kid: int, ctx: CtxDep):
    """A clip chosen by hand on the khutba video: bounds in session time (seconds), type, title."""
    f = await ctx.body()
    ctx.require("validate")
    k = khutba_or_404(ctx, kid)
    cid = content.create_clip(ctx.conn, ctx.request.app.state.db_path, k, f.get("start", ""), f.get("end", ""),
                              f.get("kind", ""), f.get("title", ""), ctx.user, ctx.ip,
                              background=ctx.request.app.state.regen_background)
    return _ok(_("Clip created: rendering now (about a minute), then it waits for validation."), id=cid)


@router.post("/clips/{cid}/bounds")
async def clip_bounds(cid: int, ctx: CtxDep):
    """New bounds (and title) for a clip: back to "to validate", rendered again."""
    f = await ctx.body()
    ctx.require("validate")
    c, k = clip_or_404(ctx, cid)
    content.adjust_clip(ctx.conn, ctx.request.app.state.db_path, c, k, f.get("start", ""), f.get("end", ""),
                        f.get("title"), ctx.user, ctx.ip, background=ctx.request.app.state.regen_background)
    return _ok(_("Bounds saved: the clip is rendered again, then it goes back through validation."),
               regen_state="en_cours")
