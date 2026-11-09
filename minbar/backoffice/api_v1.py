"""API JSON versionnée (`/api/v1`), destinée au front React.

Lecture seule pour l'instant : chaque écran React est branché ici avant d'ajouter les écritures.
Les règles d'accès sont celles du back-office (Ctx.require), pas une seconde matrice.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import auth, branding, content, db, live, publish
from .app import PRE_COOKIE, ConnDep, CtxDep, Ctx, _sec, imam_filter, count_clips


def _secure(request: Request) -> bool:
    return request.app.state.secure or _sec(request)

router = APIRouter(prefix="/api/v1", tags=["api"], include_in_schema=False)

PERMISSIONS = tuple(auth.PERMS)


def _user(ctx: Ctx) -> dict:
    u = ctx.user
    return {"id": u["id"], "name": u["display_name"] or u["username"], "role": u["role"],
            "role_label": auth.ROLE_LABELS.get(u["role"], u["role"]),
            "permissions": [p for p in PERMISSIONS if ctx.can(p)]}


def _mosque(ctx: Ctx) -> dict:
    m = ctx.mosque
    return {"id": m["id"], "slug": m["slug"], "name": m["name"], "timezone": m.get("timezone") or "",
            "logo": branding.url(m), "clip_title": m.get("clip_title") or ""}


@router.get("/login-challenge")
def challenge(request: Request):
    """Jeton anti-rejeu de la connexion : lié au navigateur par un cookie, comme le formulaire HTML."""
    pre = secrets.token_urlsafe(16)
    r = JSONResponse({"pre": pre})
    r.set_cookie(PRE_COOKIE, pre, httponly=True, samesite="lax", secure=_secure(request), max_age=600, path="/")
    return r


@router.post("/login")
async def login(request: Request, conn: ConnDep):
    f = await request.form()
    username = str(f.get("username", "")).strip()
    password = str(f.get("password", ""))
    pre = request.cookies.get(PRE_COOKIE)
    ip = request.client.host if request.client else "?"
    if not pre or not secrets.compare_digest(pre, str(f.get("pre", ""))):
        return JSONResponse({"error": "challenge"}, status_code=403)
    lim = request.app.state.limiter
    if lim.blocked(ip, username):
        return JSONResponse({"error": "rate"}, status_code=429)
    u = await run_in_threadpool(auth.authenticate, conn, username, password)
    if not u:
        lim.fail(ip, username)
        connu = db.one(conn, "SELECT mosque_id FROM users WHERE username = ?", username)
        db.audit(conn, connu["mosque_id"] if connu else None, None, "connexion.echec", ip=ip, identifiant=username[:64])
        return JSONResponse({"error": "identifiants"}, status_code=401)
    lim.success(ip, username)
    token = auth.open_session(conn, u["id"], ip, request.headers.get("user-agent"))
    db.audit(conn, u["mosque_id"], u["id"], "connexion", ip=ip)
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", u["mosque_id"])
    sess = auth.load_session(conn, token, request.headers.get("user-agent"), ip)[0]
    ctx = Ctx(request, conn, sess, u, m)
    r = JSONResponse({"user": _user(ctx), "mosque": _mosque(ctx), "csrf": sess["csrf"]})
    r.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=_secure(request),
                 max_age=auth.SESSION_TTL, path="/")
    r.delete_cookie(PRE_COOKIE)
    return r


@router.get("/session")
def session(ctx: CtxDep):
    """Qui est connecté, sur quelle mosquée, et ce qu'il a le droit de faire.

    Sert aussi de sonde : sans session, l'intercepteur d'authentification répond 401.
    """
    # Le nombre de clips à valider accompagne la session : la navigation l'affiche en pastille
    # sans une seconde requête au démarrage.
    where, args = imam_filter(ctx)
    a_valider = db.one(ctx.conn, f"SELECT COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                                 f"WHERE {where} AND c.status = 'a_valider'", *args)["n"]
    return {"user": _user(ctx), "mosque": _mosque(ctx), "csrf": (ctx.session or {}).get("csrf", ""),
            "counts": {"pending": a_valider}}


@router.get("/dashboard")
def dashboard(ctx: CtxDep):
    where, args = imam_filter(ctx)
    recent = db.all_(ctx.conn, f"SELECT k.*, u.display_name imam FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id "
                               f"WHERE {where} ORDER BY k.date DESC, k.run_id DESC, k.session_index LIMIT 6", *args)
    total = db.one(ctx.conn, f"SELECT COUNT(*) n FROM khutbas k WHERE {where}", *args)["n"]
    cmds = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                             "WHERE a.mosque_id = ? AND a.action LIKE 'live.%' ORDER BY a.id DESC LIMIT 5", ctx.mosque["id"])
    try:
        etat = {"ok": True, **live.summarize(live.status(ctx.mosque))}
    except live.OrchestratorError as e:
        etat = {"ok": False, "error": str(e)}
    return {"counts": {"pending": count_clips(ctx, "a_valider"), "ready": count_clips(ctx, "valide"), "sermons": total},
            "recent": recent, "live": etat, "commands": cmds}


@router.get("/sermons")
def sermons(ctx: CtxDep, limit: int = Query(100, ge=1, le=500)):
    where, args = imam_filter(ctx)
    rows = db.all_(ctx.conn, f"SELECT k.*, u.display_name imam, "
                             f"(SELECT COUNT(*) FROM clips c WHERE c.khutba_id = k.id) clips, "
                             f"(SELECT COUNT(*) FROM clips c WHERE c.khutba_id = k.id AND c.status = 'a_valider') pending "
                             f"FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id WHERE {where} "
                             f"ORDER BY k.date DESC, k.run_id DESC, k.session_index LIMIT ?", *args, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/sermons/{kid}")
def sermon(ctx: CtxDep, kid: int):
    where, args = imam_filter(ctx)
    row = db.one(ctx.conn, f"SELECT k.*, u.display_name imam FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id "
                           f"WHERE {where} AND k.id = ?", *args, kid)
    if not row:
        from .app import NotFound
        raise NotFound()
    segments = db.all_(ctx.conn, "SELECT * FROM segments WHERE khutba_id = ? ORDER BY idx", kid) \
        if _a_table(ctx.conn, "segments") else []
    clips = db.all_(ctx.conn, "SELECT * FROM clips WHERE khutba_id = ? ORDER BY idx", kid)
    return {"sermon": row, "segments": segments, "clips": clips, "status_labels": content.STATUS_LABELS}


@router.get("/clips")
def clips(ctx: CtxDep, status: str = "", limit: int = Query(200, ge=1, le=1000)):
    where, args = imam_filter(ctx)
    extra, extra_args = ("AND c.status = ?", (status,)) if status else ("", ())
    rows = db.all_(ctx.conn, f"SELECT c.*, k.label sermon, k.date, k.title, k.session_no "
                             f"FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                             f"WHERE {where} {extra} ORDER BY c.id DESC LIMIT ?", *args, *extra_args, limit)
    # Comptes par statut pour les filtres (« À valider (8) ») : comptés sur la mosquée entière,
    # sinon le premier filtre choisi effacerait les autres chiffres.
    par_statut = {r["status"]: r["n"] for r in db.all_(
        ctx.conn, f"SELECT c.status, COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                  f"WHERE {where} GROUP BY c.status", *args)}
    return {"items": rows, "count": len(rows), "counts": par_statut,
            "status_labels": content.STATUS_LABELS, "tabs": content.STATUS_TABS}


@router.get("/publications")
def publications(ctx: CtxDep):
    ctx.require("publish")
    etats = db.all_(ctx.conn, "SELECT * FROM connectors WHERE mosque_id = ?", ctx.mosque["id"]) \
        if _a_table(ctx.conn, "connectors") else []
    return {"connectors": [{"key": k, "label": v.label, "fields": list(v.fields)} for k, v in publish.CONNECTORS.items()],
            "configured": etats}


@router.get("/settings")
def settings(ctx: CtxDep):
    """Tout ce que montre l'écran Réglages : l'interface découpe elle-même en onglets."""
    ctx.require("admin")
    m = _mosque(ctx)
    m.update({k: ctx.mosque.get(k) or "" for k in db.MOSQUE_SETTINGS})
    # « par défaut : data/state » — même source que les gabarits, et jamais un chemin de la machine.
    from . import dirs as dirs_mod
    defauts = {k: dirs_mod.label(v) for k, v in db.mosque_dirs(ctx.mosque).items()}
    return {"mosque": m, "defaults": defauts,
            "sections": ("mosque", "logo", "live", "folders", "model", "connectors", "access")}


@router.get("/users")
def users(ctx: CtxDep):
    ctx.require("admin")
    rows = db.all_(ctx.conn, "SELECT id, username, display_name, role, active, created_at, last_login_at "
                             "FROM users WHERE mosque_id = ? ORDER BY id", ctx.mosque["id"])
    return {"items": rows, "roles": auth.ROLE_LABELS}


@router.get("/audit")
def audit(ctx: CtxDep, limit: int = Query(100, ge=1, le=500)):
    ctx.require("journal")
    rows = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                             "WHERE a.mosque_id = ? ORDER BY a.id DESC LIMIT ?", ctx.mosque["id"], limit)
    return {"items": rows, "count": len(rows)}


@router.get("/health")
def health():
    return JSONResponse({"ok": True})


def _a_table(conn, nom: str) -> bool:
    return bool(db.one(conn, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", nom))
