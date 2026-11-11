"""Home screen counters and the live command entry point."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .. import db, live
from ..context import CtxDep, NotFound, count_clips, imam_filter, live_status
from .common import _ok

router = APIRouter()


@router.get("/dashboard")
def dashboard(ctx: CtxDep):
    where, args = imam_filter(ctx)
    recent = db.all_(ctx.conn, f"SELECT k.*, u.display_name imam FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id "
                               f"WHERE {where} ORDER BY k.date DESC, k.run_id DESC, k.session_index LIMIT 6", *args)
    # Chaque compteur renvoie vers sa page : les khutbas d'un côté, les dars enregistrés de l'autre.
    par_genre = {r["g"]: r["n"] for r in db.all_(
        ctx.conn, f"SELECT COALESCE(NULLIF(k.kind, ''), 'khutba') g, COUNT(*) n FROM khutbas k WHERE {where} GROUP BY g", *args)}
    total = par_genre.get("khutba", 0)
    cmds = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                             "WHERE a.mosque_id = ? AND a.action LIKE 'live.%' ORDER BY a.id DESC LIMIT 5", ctx.mosque["id"])
    return {"counts": {"pending": count_clips(ctx, "a_valider"), "ready": count_clips(ctx, "valide"), "sermons": total,
                       "dars": par_genre.get("dars", 0)},
            "recent": recent, "live": live_status(ctx), "commands": cmds,
            # Commands offered: keys derived from the engine, plus the values they accept and a
            # danger flag. Every label is built by the front end from i18n.
            "live_commands": [{"key": k, "destructive": k in live.DESTRUCTIVE, "choices": list(live.choices(k))}
                              for k in live.COMMANDS]}


@router.post("/live/{cmd}")
async def live_command(cmd: str, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("live")
    if cmd not in live.COMMANDS:
        raise NotFound()
    arg = (f.get("name") or "").strip() or None
    info = (f.get("info") or "").strip() or None      # janaza record: note under the name
    valeur = (f.get("valeur") or "").strip() or None  # minutes (extend) or scene (scene)
    try:
        r = await run_in_threadpool(live.command, ctx.mosque, cmd, arg, info, valeur)
        # The audit trail stores protocol keys (command, value), not labels: the log is translated
        # by the front end like the rest of the interface.
        details = {k: v for k, v in {"commande": cmd, "valeur": valeur, "defunt": arg, "mention": info,
                                     "reponse": r.get("message"), "etat": r.get("state")}.items() if v}
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.commande", ip=ctx.ip, **details)
        # After a command, we want the real state: fresh read, no cache.
        return _ok(r.get("message", "ok"), state=r.get("state"), live=live_status(ctx, forcer=True))
    except live.OrchestratorError as e:
        details = {k: v for k, v in {"commande": cmd, "valeur": valeur, "erreur": str(e)}.items() if v}
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.echec", ip=ctx.ip, **details)
        return JSONResponse({"error": "injoignable", "message": f"échec — {e}"}, status_code=503)
