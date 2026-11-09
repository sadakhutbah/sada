"""API JSON versionnée (`/api/v1`) : tout ce que fait le front React.

Lecture et écriture passent par ici ; les règles d'accès sont celles du back-office (Ctx.require),
pas une seconde matrice. Les écritures exigent le jeton de session en en-tête `X-CSRF`.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..pipeline import llm
from ..pipeline.output import SessionOutput
from . import auth, branding, content, db, dirs as dirs_mod, importer, live, publish
from .app import (PRE_COOKIE, ConnDep, Ctx, CtxDep, Invalid, NotFound, _sec, clip_or_404, count_clips,
                  imam_filter, khutba_or_404, live_status)

router = APIRouter(prefix="/api/v1", tags=["api"], include_in_schema=False)

PERMISSIONS = tuple(auth.PERMS)
SECTIONS = ("mosque", "logo", "live", "folders", "model", "connectors", "access")


def _secure(request: Request) -> bool:
    return request.app.state.secure or _sec(request)


# ------------------------------------------------------------------ formes de réponse

def _user(ctx: Ctx) -> dict:
    u = ctx.user
    return {"id": u["id"], "name": u["display_name"] or u["username"], "role": u["role"],
            "role_label": auth.ROLE_LABELS.get(u["role"], u["role"]),
            "permissions": [p for p in PERMISSIONS if ctx.can(p)]}


def _mosque(ctx: Ctx) -> dict:
    m = ctx.mosque
    return {"id": m["id"], "slug": m["slug"], "name": m["name"], "timezone": m.get("timezone") or "",
            "logo": branding.url(m), "clip_title": m.get("clip_title") or ""}


def _ok(message: str, **extra) -> dict:
    return {"ok": True, "message": message, **extra}


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
    return {
        "clip": c, "sermon": k, "segments": rows, "history": hist, "publications": pubs,
        "can_regenerate": regen_ok, "regenerate_reason": regen_why,
        "can_validate": content.can_validate(c)[0],
        "can_publish": publish.can_publish(c)[0],
        "connectors": publish.connector_rows(ctx.conn, ctx.mosque),
        "caption": publish.default_caption(k, ctx.mosque, list(dict.fromkeys(refs))),
        "exists": bool(c["file_path"]) and Path(c["file_path"]).exists(),
        "status_labels": content.STATUS_LABELS,
    }


# ------------------------------------------------------------------ session

@router.get("/login-challenge")
def challenge(request: Request):
    """Jeton anti-rejeu de la connexion : lié au navigateur par un cookie."""
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


@router.post("/logout")
def logout(ctx: CtxDep):
    ctx.need_csrf()
    auth.close_session(ctx.conn, ctx.request.cookies.get(auth.COOKIE))
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "deconnexion", ip=ctx.ip)
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
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


# ------------------------------------------------------------------ lecture

@router.get("/dashboard")
def dashboard(ctx: CtxDep):
    where, args = imam_filter(ctx)
    recent = db.all_(ctx.conn, f"SELECT k.*, u.display_name imam FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id "
                               f"WHERE {where} ORDER BY k.date DESC, k.run_id DESC, k.session_index LIMIT 6", *args)
    total = db.one(ctx.conn, f"SELECT COUNT(*) n FROM khutbas k WHERE {where}", *args)["n"]
    cmds = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                             "WHERE a.mosque_id = ? AND a.action LIKE 'live.%' ORDER BY a.id DESC LIMIT 5", ctx.mosque["id"])
    return {"counts": {"pending": count_clips(ctx, "a_valider"), "ready": count_clips(ctx, "valide"), "sermons": total},
            "recent": recent, "live": live_status(ctx), "commands": cmds,
            # Les commandes proposées viennent du serveur : le front n'invente pas la liste.
            "live_commands": [{"key": k, "label": c.label, "style": c.style, "confirm": c.confirm}
                              for k, c in live.COMMANDS.items()]}


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
    k = khutba_or_404(ctx, kid)
    data = content.load(ctx.conn, k)
    segments = db.all_(ctx.conn, "SELECT * FROM segments WHERE khutba_id = ? ORDER BY idx", kid) \
        if _a_table(ctx.conn, "segments") else []
    clips = db.all_(ctx.conn, "SELECT * FROM clips WHERE khutba_id = ? ORDER BY idx", kid)
    notes = SessionOutput(k["output_dir"]).notes
    imam = db.one(ctx.conn, "SELECT display_name FROM users WHERE id = ?", k["imam_id"]) if k["imam_id"] else None
    return {"sermon": k, "segments": segments, "clips": clips, "status_labels": content.STATUS_LABELS,
            "transcript": content.transcript_rows(data),
            "notes": notes.read_text(encoding="utf-8") if notes.exists() else "",
            "verses": data["verses"], "imam": imam,
            "imams": auth.imams(ctx.conn, ctx.mosque["id"]) if ctx.can("assign") else [],
            "has_video": bool(k["video_path"]) and Path(k["video_path"]).exists()}


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


@router.get("/clips/{cid}")
def clip(cid: int, ctx: CtxDep):
    return _clip_detail(ctx, cid)


@router.get("/publications")
def publications(ctx: CtxDep):
    ctx.require("publish")
    etats = db.all_(ctx.conn, "SELECT * FROM connectors WHERE mosque_id = ?", ctx.mosque["id"]) \
        if _a_table(ctx.conn, "connectors") else []
    ready = db.all_(ctx.conn, "SELECT c.*, k.label sermon, k.date FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                              "WHERE c.mosque_id = ? AND c.status = 'valide' ORDER BY c.decided_at DESC", ctx.mosque["id"])
    pubs = db.all_(ctx.conn, "SELECT p.*, u.display_name who, c.idx, c.kind, k.label sermon, k.date FROM publications p "
                             "JOIN clips c ON c.id = p.clip_id JOIN khutbas k ON k.id = c.khutba_id "
                             "LEFT JOIN users u ON u.id = p.user_id WHERE p.mosque_id = ? ORDER BY p.id DESC LIMIT 100",
                   ctx.mosque["id"]) if _a_table(ctx.conn, "publications") else []
    return {"connectors": [{"key": k, "label": v.label, "fields": list(v.fields)} for k, v in publish.CONNECTORS.items()],
            "configured": etats, "ready": ready, "publications": pubs}


@router.get("/settings")
def settings(ctx: CtxDep):
    """Tout ce que montre l'écran Réglages : l'interface découpe elle-même en onglets."""
    ctx.require("admin")
    m = _mosque(ctx)
    m.update({k: ctx.mosque.get(k) or "" for k in db.MOSQUE_SETTINGS})
    # « par défaut : data/state » — jamais un chemin de la machine.
    defauts = {k: dirs_mod.label(v) for k, v in db.mosque_dirs(ctx.mosque).items()}
    return {"mosque": m, "defaults": defauts, "sections": SECTIONS,
            "imams": auth.imams(ctx.conn, ctx.mosque["id"]),
            "connectors": publish.connector_rows(ctx.conn, ctx.mosque),
            "model": {**llm.config(), "label": llm.settings().label(), "enabled": llm.enabled()}}


@router.get("/users")
def users(ctx: CtxDep):
    ctx.require("admin")
    rows = db.all_(ctx.conn, "SELECT id, username, display_name, role, active, created_at, last_login_at "
                             "FROM users WHERE mosque_id = ? ORDER BY id", ctx.mosque["id"])
    return {"items": rows, "roles": auth.ROLE_LABELS}


@router.get("/audit")
def audit(ctx: CtxDep, action: str = "", limit: int = Query(100, ge=1, le=500)):
    ctx.require("journal")
    rows = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                             "WHERE a.mosque_id = ? AND (? = '' OR a.action LIKE ? || '%') ORDER BY a.id DESC LIMIT ?",
                   ctx.mosque["id"], action, action, limit)
    return {"items": rows, "count": len(rows), "labels": AUDIT_LABELS}


@router.get("/folders")
def folders(ctx: CtxDep, path: str = ""):
    """Arborescence de la machine qui héberge l'application, pour le bouton « Parcourir »."""
    ctx.require("admin")
    try:
        return {"roots": dirs_mod.roots(), **dirs_mod.listing(path or None)}
    except ValueError as e:
        raise Invalid(str(e)) from e


@router.get("/health")
def health():
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ écritures

@router.post("/live/{cmd}")
async def live_command(cmd: str, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("live")
    if cmd not in live.COMMANDS:
        raise NotFound()
    arg = (f.get("name") or "").strip() or None
    info = (f.get("info") or "").strip() or None      # fiche janaza : mention sous le nom
    label = live.COMMANDS[cmd].label
    try:
        r = await run_in_threadpool(live.command, ctx.mosque, cmd, arg, info)
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.commande", ip=ctx.ip, commande=label,
                 defunt=arg, mention=info, reponse=r.get("message"), etat=r.get("state"))
        return _ok(f"{label} : {r.get('message', 'ok')}", state=r.get("state"), live=live_status(ctx))
    except live.OrchestratorError as e:
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.echec", ip=ctx.ip, commande=label, erreur=str(e))
        return JSONResponse({"error": "injoignable", "message": f"{label} : échec — {e}"}, status_code=503)


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


@router.post("/clips/{cid}/decision")
async def clip_decision(cid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("validate")
    c, _ = clip_or_404(ctx, cid)
    if f.get("decision") == "rouvrir":
        content.reopen(ctx.conn, c, ctx.user, ctx.ip)
        return _ok("Clip remis dans la file de validation.", status="a_valider")
    content.decide(ctx.conn, c, f.get("decision", ""), ctx.user, f.get("reason", ""), ctx.ip)
    return _ok("Clip validé." if f.get("decision") == "valide" else "Clip rejeté.", status=f.get("decision"))


@router.post("/clips/{cid}/regenerate")
async def clip_regenerate(cid: int, ctx: CtxDep):
    await ctx.body()
    ctx.require("validate")
    c, _ = clip_or_404(ctx, cid)
    content.start_regeneration(ctx.conn, ctx.request.app.state.db_path, c, ctx.user, ctx.ip,
                               background=ctx.request.app.state.regen_background)
    return _ok("Régénération lancée (environ une minute) : la fiche se met à jour toute seule.", regen_state="en_cours")


@router.post("/clips/{cid}/publish")
async def clip_publish(cid: int, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("publish")
    c, _ = clip_or_404(ctx, cid)
    res = await run_in_threadpool(publish.publish, ctx.conn, c, f.get("connector", ""), ctx.user,
                                  f.get("caption", ""), ctx.ip)
    if not res.ok:
        return JSONResponse({"error": "refusé", "message": f"Publication refusée : {res.message}"}, status_code=422)
    return _ok(f"Publié : {res.location}", location=res.location)


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


@router.patch("/settings/mosque")
async def mosque_save(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    vals = {k: (f.get(k) or "").strip() for k in db.MOSQUE_SETTINGS if k != "logo_path" and k in f}
    current = {k: ctx.mosque.get(k) or "" for k in db.MOSQUE_SETTINGS if k != "logo_path"}
    vals = {**current, **vals}          # un formulaire partiel ne vide pas les autres réglages
    if not vals["name"]:
        raise Invalid("Le nom est obligatoire.")
    if vals["orchestrator_url"] and not re.match(r"^https?://", vals["orchestrator_url"]):
        raise Invalid("L'adresse de l'orchestrateur doit commencer par http:// ou https://.")
    if vals["orchestrator_token"] == db.MASK:
        vals["orchestrator_token"] = ctx.mosque["orchestrator_token"]
    di = auth.imam_id(ctx.conn, ctx.mosque["id"], f.get("default_imam_id", ctx.mosque["default_imam_id"]))
    auto = f.get("auto_import")
    auto_import = (ctx.mosque.get("auto_import") or 0) if auto in ("", None) else (1 if str(auto) in ("1", "true", "on") else 0)
    db.update_mosque(ctx.conn, ctx.mosque["id"], **vals, default_imam_id=di, auto_import=auto_import)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.configuree", "mosquee", ctx.mosque["id"], ip=ctx.ip,
             champs=[k for k in vals if k != "orchestrator_token" and str(vals[k]) != str(ctx.mosque.get(k))])
    return _ok("Configuration enregistrée.")


@router.put("/settings/model")
async def model_save(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    url = (f.get("url") or "").strip()
    model = (f.get("model") or "").strip()
    proto = (f.get("protocol") or "").strip()
    if url and not re.match(r"^https?://", url):
        raise Invalid("L'adresse du modèle doit commencer par http:// ou https://.")
    if proto not in ("", "openai", "anthropic"):
        raise Invalid("Format d'API inconnu.")
    enabled = str(f.get("enabled", "0")) in ("1", "true", "on", "oui")
    llm.save_config(enabled=enabled, url=url, model=model, protocol=proto)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "modele.configure", "mosque", ctx.mosque["id"], ip=ctx.ip,
             actif=enabled, url=url, modele=model, protocole=proto)
    return _ok("Modèle de langage enregistré. Le pipeline l'applique au prochain lancement (les variables "
               "d'environnement restent prioritaires).", label=llm.settings().label())


@router.post("/settings/model/test")
def model_test(ctx: CtxDep):
    ctx.require("admin")
    ok, msg = llm.probe()
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 422)


@router.post("/settings/connectors/{key}")
async def connector_save(key: str, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    if key not in publish.CONNECTORS:
        raise NotFound()
    enabled = str(f.get("enabled", "0")) in ("1", "true", "on", "oui")
    publish.save_connector(ctx.conn, ctx.mosque["id"], key, enabled, f, ctx.user, ctx.ip)
    return _ok(f"Connecteur « {publish.CONNECTORS[key].label} » enregistré.")


@router.post("/settings/logo")
async def logo_save(ctx: CtxDep):
    ctx.require("admin")
    await ctx.body()                     # jeton CSRF vérifié ; le fichier est gardé en mémoire (2 Mo max)
    up = ctx.files.get("logo")
    if up is None or not getattr(up, "filename", ""):
        raise Invalid("Aucun fichier choisi.")
    donnees = await up.read()
    rel = branding.save(ctx.mosque, up.filename, donnees)
    db.update_mosque(ctx.conn, ctx.mosque["id"], logo_path=rel)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.logo", "mosque", ctx.mosque["id"], ip=ctx.ip, fichier=rel)
    return _ok("Logo enregistré.", logo=branding.url({**ctx.mosque, "logo_path": rel}))


@router.delete("/settings/logo")
def logo_clear(ctx: CtxDep):
    ctx.need_csrf()
    ctx.require("admin")
    branding.clear(ctx.mosque)
    db.update_mosque(ctx.conn, ctx.mosque["id"], logo_path="")
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.logo.retire", "mosque", ctx.mosque["id"], ip=ctx.ip)
    return _ok("Logo retiré : emblème neutre repris.")


# ------------------------------------------------------------------ divers

AUDIT_LABELS = {
    "connexion": "Connexion", "connexion.echec": "Échec de connexion", "deconnexion": "Déconnexion",
    "live.commande": "Commande live", "live.echec": "Commande live en échec",
    "import.khutba": "Import khutba", "import.clip": "Import clip", "import.manuel": "Import lancé",
    "khutba.imam": "Imam rattaché", "correction.sous-titre": "Sous-titre corrigé",
    "correction.traduction": "Traduction corrigée", "clip.regeneration": "Régénération demandée",
    "clip.regenere": "Clip régénéré", "clip.valide": "Clip validé", "clip.rejete": "Clip rejeté",
    "clip.remis_en_file": "Clip remis en file", "publication.publie": "Publié",
    "publication.refuse": "Publication refusée", "publication.erreur": "Publication en erreur",
    "utilisateur.cree": "Compte créé", "utilisateur.modifie": "Compte modifié",
    "utilisateur.mot_de_passe": "Mot de passe changé", "mosquee.configuree": "Configuration modifiée",
    "connecteur.configure": "Connecteur configuré", "acces.refuse": "Accès refusé",
}


def _a_table(conn, nom: str) -> bool:
    return bool(db.one(conn, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", nom))
