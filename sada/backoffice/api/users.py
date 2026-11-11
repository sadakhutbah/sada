"""Accounts: list, creation, update and password change."""
from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from .. import auth, db
from ..context import CtxDep, Invalid, NotFound
from .common import _ok

router = APIRouter()


@router.get("/users")
def users(ctx: CtxDep):
    ctx.require("admin")
    rows = db.all_(ctx.conn, "SELECT id, username, display_name, role, active, created_at, last_login_at "
                             "FROM users WHERE mosque_id = ? ORDER BY id", ctx.mosque["id"])
    return {"items": rows, "roles": auth.ROLE_LABELS}


@router.post("/account/password")
async def account_password(ctx: CtxDep):
    f = await ctx.body()
    if not await run_in_threadpool(auth.verify_password, ctx.user["password_hash"], f.get("current", "")):
        raise Invalid("Mot de passe actuel incorrect.")
    if f.get("new") != f.get("confirm"):
        raise Invalid("Les deux nouveaux mots de passe diffèrent.")
    await run_in_threadpool(auth.set_password, ctx.conn, ctx.user["id"], f.get("new", ""))
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", ctx.user["id"], ip=ctx.ip)
    return _ok("Mot de passe changé.")


@router.post("/users")
async def user_create(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    uid = await run_in_threadpool(auth.create_user, ctx.conn, ctx.mosque["id"], f.get("username", ""),
                                  f.get("password", ""), f.get("role", ""), f.get("display_name", ""))
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.cree", "utilisateur", uid, ip=ctx.ip,
             identifiant=f.get("username"), role=f.get("role"))
    return _ok(f"Compte « {f.get('username')} » créé.", id=uid)


@router.patch("/users/{uid}")
async def user_update(uid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    u = db.one(ctx.conn, "SELECT * FROM users WHERE id = ? AND mosque_id = ?", uid, ctx.mosque["id"])
    if not u:
        raise NotFound()
    if f.get("password"):
        await run_in_threadpool(auth.set_password, ctx.conn, uid, f["password"])
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", uid, ip=ctx.ip)
    role = f.get("role") or u["role"]
    actif = f.get("active")
    active = (1 if u["active"] else 0) if actif in ("", None) else (1 if str(actif) in ("1", "true", "on") else 0)
    if role not in auth.ROLES:
        raise Invalid("Rôle inconnu.")
    if uid == ctx.user["id"] and (role != "admin" or not active):
        raise Invalid("Vous ne pouvez pas retirer vos propres droits d'administrateur.")
    ctx.conn.execute("UPDATE users SET role = ?, active = ?, display_name = ? WHERE id = ?",
                     (role, active, (f.get("display_name") or u["display_name"]).strip(), uid))
    if not active:
        ctx.conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.modifie", "utilisateur", uid, ip=ctx.ip,
             role=role, actif=bool(active))
    return _ok(f"Compte « {u['username']} » mis à jour.", role=role, active=bool(active))
