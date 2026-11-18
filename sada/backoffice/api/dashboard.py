"""Home screen counters and the live command entry point."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from ...i18n import _
from .. import db, live, orm
from ..context import CtxDep, NotFound, count_clips, imam_filter, live_status
from ..models import Audit, Khutba, User
from .common import _ok

router = APIRouter()


@router.get("/dashboard")
def dashboard(ctx: CtxDep):
    where = imam_filter(ctx)
    recent = orm.rows(
        ctx.conn,
        select(Khutba.__table__, User.display_name.label("imam"))
        .outerjoin(User, User.id == Khutba.imam_id)
        .where(*where)
        .order_by(Khutba.date.desc(), Khutba.run_id.desc(), Khutba.session_index)
        .limit(6),
    )
    # Chaque compteur renvoie vers sa page : les khutbas d'un côté, les dars enregistrés de l'autre.
    genre = func.coalesce(func.nullif(Khutba.kind, ""), "khutba")
    par_genre = dict(ctx.conn.execute(select(genre, func.count()).where(*where).group_by(genre)).all())
    total = par_genre.get("khutba", 0)
    cmds = orm.rows(
        ctx.conn,
        select(Audit.__table__, User.display_name.label("who"))
        .outerjoin(User, User.id == Audit.user_id)
        .where(Audit.mosque_id == ctx.mosque["id"], Audit.action.like("live.%"))
        .order_by(Audit.id.desc())
        .limit(5),
    )
    return {
        "counts": {
            "pending": count_clips(ctx, "a_valider"),
            "ready": count_clips(ctx, "valide"),
            "sermons": total,
            "dars": par_genre.get("dars", 0),
        },
        "recent": recent,
        "live": live_status(ctx),
        "commands": cmds,
        # Commands offered: keys derived from the engine, plus the values they accept and a
        # danger flag. Every label is built by the front end from i18n.
        "live_commands": [
            {"key": k, "destructive": k in live.DESTRUCTIVE, "choices": list(live.choices(k))} for k in live.COMMANDS
        ],
    }


@router.get("/live/indicator")
def live_indicator(ctx: CtxDep):
    """Is the mosque on the air? Read by the top bar on every page: snapshot only, a single flag."""
    if not ctx.mosque:
        return {"on": False}
    return {"on": live.on_air(live_status(ctx))}


@router.post("/live/{cmd}")
async def live_command(cmd: str, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("live")
    if cmd not in live.COMMANDS:
        raise NotFound()
    arg = (f.get("name") or "").strip() or None
    info = (f.get("info") or "").strip() or None  # janaza record: note under the name
    valeur = (f.get("valeur") or "").strip() or None  # minutes (extend) or scene (scene)
    try:
        r = await run_in_threadpool(live.command, ctx.mosque, cmd, arg, info, valeur)
        # The audit trail stores protocol keys (command, value), not labels: the log is translated
        # by the front end like the rest of the interface.
        details = {
            k: v
            for k, v in {
                "commande": cmd,
                "valeur": valeur,
                "defunt": arg,
                "mention": info,
                "reponse": r.get("message"),
                "etat": r.get("state"),
            }.items()
            if v
        }
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.commande", ip=ctx.ip, **details)
        # After a command, we want the real state: fresh read, no cache.
        return _ok(r.get("message", "ok"), state=r.get("state"), live=live_status(ctx, forcer=True))
    except live.OrchestratorError as e:
        details = {k: v for k, v in {"commande": cmd, "valeur": valeur, "erreur": str(e)}.items() if v}
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.echec", ip=ctx.ip, **details)
        return JSONResponse(
            {"error": "injoignable", "message": _("Live command failed — {error}", error=e)}, status_code=503
        )
