"""Accounts: list, creation, update and password change."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import delete, select, update
from starlette.concurrency import run_in_threadpool

from ...i18n import _
from .. import auth, db, orm
from ..context import CtxDep, Invalid, NotFound
from ..models import AuthSession, User
from .common import _ok

router = APIRouter()


@router.get("/users")
def users(ctx: CtxDep):
    ctx.require("admin")
    rows = orm.rows(
        ctx.conn,
        select(
            User.id,
            User.username,
            User.display_name,
            User.role,
            User.active,
            User.created_at,
            User.last_login_at,
            User.permissions,
        )
        .where(User.mosque_id == ctx.mosque["id"])
        .order_by(User.id),
    )
    for r in rows:
        r["custom"] = auth.custom_permissions(r) is not None
        r["permissions"] = auth.permissions(r)
    return {
        "items": rows,
        "roles": auth.role_labels(),
        "perms": auth.perm_labels(),
        "role_perms": {r: auth.role_permissions(r) for r in auth.ROLES},
        "me": ctx.user["id"],
        "min_password": auth.MIN_PASSWORD,
    }


@router.post("/account/password")
async def account_password(ctx: CtxDep):
    f = await ctx.body()
    if not await run_in_threadpool(auth.verify_password, ctx.user["password_hash"], f.get("current", "")):
        raise Invalid(_("Current password is incorrect."))
    if f.get("new") != f.get("confirm"):
        raise Invalid(_("The two new passwords differ."))
    if f.get("new") == f.get("current"):
        raise Invalid(_("Choose a password different from the current one."))
    await run_in_threadpool(auth.set_password, ctx.conn, ctx.user["id"], f.get("new", ""), ctx.session["id"])
    ctx.conn.execute(update(User).where(User.id == ctx.user["id"]).values(must_change_password=0))
    orm.done(ctx.conn)
    db.audit(
        ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", ctx.user["id"], ip=ctx.ip
    )
    return _ok(_("Password changed."))


@router.post("/users")
async def user_create(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    uid = await run_in_threadpool(
        auth.create_user,
        ctx.conn,
        ctx.mosque["id"],
        f.get("username", ""),
        f.get("password", ""),
        f.get("role", ""),
        f.get("display_name", ""),
        f.get("permissions"),
    )
    # The administrator chose the password: the person replaces it at first sign-in.
    ctx.conn.execute(update(User).where(User.id == uid).values(must_change_password=1))
    orm.done(ctx.conn)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "utilisateur.cree",
        "utilisateur",
        uid,
        ip=ctx.ip,
        identifiant=f.get("username"),
        role=f.get("role"),
    )
    return _ok(_("Account “{username}” created.", username=f.get("username")), id=uid)


@router.post("/users/{uid}/password")
async def user_password(uid: int, ctx: CtxDep):
    """An administrator sets a new password for an account; its open sessions are closed."""
    f = await ctx.body()
    ctx.require("admin")
    u = orm.first(ctx.conn, User, id=uid, mosque_id=ctx.mosque["id"])
    if not u:
        raise NotFound(_("Unknown account."))
    if uid == ctx.user["id"]:
        raise Invalid(_("Change your own password from your account."))
    pw = f.get("password", "")
    if len(pw) < auth.MIN_PASSWORD:
        raise Invalid(_("Password too short (at least {n} characters).", n=auth.MIN_PASSWORD))
    await run_in_threadpool(auth.set_password, ctx.conn, uid, pw)  # also signs the account out everywhere
    ctx.conn.execute(update(User).where(User.id == uid).values(must_change_password=1))
    orm.done(ctx.conn)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", uid, ip=ctx.ip)
    return _ok(_("Password of “{username}” changed.", username=u["username"]))


@router.patch("/users/{uid}")
async def user_update(uid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    u = orm.first(ctx.conn, User, id=uid, mosque_id=ctx.mosque["id"])
    if not u:
        raise NotFound()
    if f.get("password"):
        await run_in_threadpool(auth.set_password, ctx.conn, uid, f["password"])
        ctx.conn.execute(update(User).where(User.id == uid).values(must_change_password=int(uid != ctx.user["id"])))
        orm.done(ctx.conn)
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", uid, ip=ctx.ip)
    role = f.get("role") or u["role"]
    actif = f.get("active")
    active = (1 if u["active"] else 0) if actif in ("", None) else (1 if str(actif) in ("1", "true", "on") else 0)
    if role not in auth.ROLES:
        raise Invalid(_("Unknown role."))
    try:
        perms = auth.normalize_permissions(f["permissions"]) if "permissions" in f else u["permissions"]
    except ValueError as e:
        raise Invalid(str(e)) from e
    effectives = auth.permissions({"role": role, "permissions": perms})
    if uid == ctx.user["id"] and ("admin" not in effectives or not active):
        raise Invalid(_("You cannot remove your own administrator rights."))
    ctx.conn.execute(
        update(User)
        .where(User.id == uid)
        .values(
            role=role,
            active=active,
            display_name=(f.get("display_name") or u["display_name"]).strip(),
            permissions=perms,
        )
    )
    if not active:
        ctx.conn.execute(delete(AuthSession).where(AuthSession.user_id == uid))
    orm.done(ctx.conn)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "utilisateur.modifie",
        "utilisateur",
        uid,
        ip=ctx.ip,
        role=role,
        actif=bool(active),
        permissions=",".join(effectives),
    )
    return _ok(_("Account “{username}” updated.", username=u["username"]), role=role, active=bool(active))
