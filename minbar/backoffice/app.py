"""Application web du back-office (FastAPI + Jinja2, rendu serveur, sans dépendance JS externe)."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from .. import text
from ..pipeline import llm, verses
from ..pipeline.output import SessionOutput
from . import auth, branding, content, db, dirs as dirs_mod, importer, live, publish

HERE = Path(__file__).resolve().parent
PRE_COOKIE = "minbar_pre"


# ------------------------------------------------------------------ erreurs

class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Ctx:
    def __init__(self, request: Request, conn, session: dict, user: dict, mosque: dict):
        self.request, self.conn, self.session, self.user, self.mosque = request, conn, session, user, mosque
        self.ip = request.client.host if request.client else None
        self.files: dict = {}

    def can(self, perm: str) -> bool:
        return auth.can(self.user, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            db.audit(self.conn, self.mosque["id"], self.user["id"], "acces.refuse", ip=self.ip, chemin=self.request.url.path, droit=perm)
            raise Forbidden()

    def flash(self, msg: str, kind: str = "ok") -> None:
        self.conn.execute("UPDATE auth_sessions SET flash = ? WHERE id = ?", (json.dumps([kind, msg]), self.session["id"]))

    async def form(self) -> dict:
        """Champs texte du formulaire ; jeton CSRF vérifié. `self.files` garde les fichiers envoyés."""
        f = await self.request.form()
        if not auth.check_csrf(self.session, f.get("csrf")):
            raise Forbidden()
        self.files = {k: v for k, v in f.items() if not isinstance(v, str)}
        return {k: (v if isinstance(v, str) else "") for k, v in f.items()}


def get_conn(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _sec(request) -> bool:
    """Requête servie en HTTPS (directement ou via un proxy qui pose X-Forwarded-Proto)."""
    fwd = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return request.url.scheme == "https" or fwd == "https"


ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]


def get_any_mosque(conn: ConnDep) -> dict | None:
    """Mosquée de l'instance (une par machine en V1) : sert au logo de la page de connexion."""
    return db.one(conn, "SELECT * FROM mosques ORDER BY id LIMIT 1")


def get_ctx(request: Request, conn: ConnDep) -> Ctx:
    res = auth.load_session(conn, request.cookies.get(auth.COOKIE), request.headers.get("user-agent"),
                            request.client.host if request.client else None)
    if not res:
        raise LoginRequired()
    s, u = res
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", u["mosque_id"])
    return Ctx(request, conn, s, u, m)


CtxDep = Annotated[Ctx, Depends(get_ctx)]
AnyMosqueDep = Annotated[dict | None, Depends(get_any_mosque)]


# ------------------------------------------------------------------ filtres



def f_local(iso: str | None, m: dict | None = None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if not iso:
        return "—"
    try:
        t = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return t.astimezone(db.timezone(m or {})).strftime(fmt)


def f_mmss(s) -> str:
    return "—" if s is None else text.duration(s)


def f_date(d: str | None) -> str:
    if not d:
        return "—"
    try:
        return text.french_date(dt.date.fromisoformat(d))
    except ValueError:
        return d


def f_markdown(md: str) -> Markup:
    """Markdown minimal des notes du pipeline (titres, listes, citations, gras) ; tout est échappé d'abord."""
    out, in_list = [], False
    for raw in md.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", line)
        is_li = raw.startswith("- ")
        if in_list and not is_li:
            out.append("</ul>")
            in_list = False
        if raw.startswith("# "):
            continue                                   # titre redondant avec la page
        if raw.startswith("## "):
            out.append(f"<h3>{line[3:]}</h3>")
        elif raw.startswith("&gt; ") or raw.startswith("> "):
            out.append(f"<p class=note-warn>{line[5:] if line.startswith('&gt; ') else line[2:]}</p>")
        elif is_li:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
        elif raw.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return Markup("\n".join(out))


AUDIT_LABELS = {
    "connexion": "Connexion", "connexion.echec": "Échec de connexion", "deconnexion": "Déconnexion",
    "live.commande": "Commande live", "live.echec": "Commande live en échec",
    "import.khutba": "Import khutba", "import.clip": "Import clip", "import.manuel": "Import lancé",
    "khutba.imam": "Imam rattaché", "correction.sous-titre": "Sous-titre corrigé", "correction.traduction": "Traduction corrigée",
    "clip.regeneration": "Régénération demandée", "clip.regenere": "Clip régénéré", "clip.valide": "Clip validé",
    "clip.rejete": "Clip rejeté", "clip.remis_en_file": "Clip remis en file",
    "publication.publie": "Publié", "publication.refuse": "Publication refusée", "publication.erreur": "Publication en erreur",
    "utilisateur.cree": "Compte créé", "utilisateur.modifie": "Compte modifié", "utilisateur.mot_de_passe": "Mot de passe changé",
    "mosquee.configuree": "Configuration modifiée", "connecteur.configure": "Connecteur configuré", "acces.refuse": "Accès refusé",
}


def f_details(details: str) -> str:
    try:
        d = json.loads(details or "{}")
    except ValueError:
        return details
    parts = []
    for k, v in d.items():
        if v is None or v == [] or v == "":
            continue
        if isinstance(v, list):
            v = ", ".join(map(str, v))
        v = str(v)
        parts.append(f"{k} : {v[:140] + '…' if len(v) > 140 else v}")
    return " · ".join(parts)


# ------------------------------------------------------------------ application

def create_app(db_path: str | Path = db.DEFAULT_DB, secure_cookies: bool = False, auto_import_s: float = 0,
               regen_background: bool = True) -> FastAPI:
    """regen_background=False : la régénération d'un clip se fait dans la requête (tests)."""
    db.init(db_path)
    stop = threading.Event()

    conn = db.connect(db_path)
    try:
        content.recover_interrupted(conn)
    finally:
        conn.close()

    def import_all():
        conn = db.connect(db_path)
        try:
            for m in db.all_(conn, "SELECT * FROM mosques WHERE auto_import = 1"):
                importer.scan(conn, m, log=lambda msg: print("[import]", msg, flush=True))
        except Exception as e:  # noqa: BLE001
            print("[import] erreur :", e, flush=True)
        finally:
            conn.close()

    def importer_loop():
        while not stop.wait(auto_import_s):
            import_all()

    @asynccontextmanager
    async def lifespan(app):
        if auto_import_s:
            import_all()
            threading.Thread(target=importer_loop, daemon=True, name="auto-import").start()
        yield
        stop.set()

    app = FastAPI(title="Minbar Live — back-office", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db_path = str(db_path)
    app.state.secure = secure_cookies
    app.state.limiter = auth.RateLimiter()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tpl = Jinja2Templates(directory=str(HERE / "templates"))
    tpl.env.filters.update(local=f_local, mmss=f_mmss, date_fr=f_date, markdown=f_markdown, details=f_details)
    tpl.env.globals.update(STATUS=content.STATUS_LABELS, STATUS_TABS=content.STATUS_TABS, LIVE_COMMANDS=live.COMMANDS, ROLES=auth.ROLE_LABELS, AUDIT=AUDIT_LABELS, MASK=db.MASK, LOGO=branding.url, SHORT=dirs_mod.label)

    def render(ctx: Ctx | None, name: str, status_code: int = 200, **kw) -> HTMLResponse:
        flash = None
        if ctx is not None:
            row = db.one(ctx.conn, "SELECT flash FROM auth_sessions WHERE id = ?", ctx.session["id"])
            if row and row["flash"]:
                flash = json.loads(row["flash"])
                ctx.conn.execute("UPDATE auth_sessions SET flash = NULL WHERE id = ?", (ctx.session["id"],))
        request = ctx.request if ctx else kw.pop("request")
        nav_pending = count_clips(ctx, "a_valider") if ctx is not None else 0
        base = {"nav_pending": nav_pending, "ctx": ctx, "user": ctx.user if ctx else None, "mosque": ctx.mosque if ctx else None,
                "csrf": ctx.session["csrf"] if ctx else "", "flash": flash, "path": request.url.path}
        return tpl.TemplateResponse(request, name, {**base, **kw}, status_code=status_code)

    SECTIONS = ("identite", "image", "live", "dossiers", "modele", "connecteurs", "acces")

    def section(s: str, cle: str = "") -> str:
        """Onglet cible après un enregistrement : inconnu -> première section. `cle` vise le sous-onglet connecteur."""
        if s not in SECTIONS:
            return "/admin/mosquee?section=identite"
        return f"/admin/mosquee?section={s}&c={cle}" if cle else f"/admin/mosquee?section={s}"

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    # ---------------------------------------------------------- middleware et erreurs
    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
                                                   "script-src 'self'; font-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if _sec(request):
            # uniquement derrière HTTPS (proxy ou serveur central) : un HSTS sur du HTTP local bloquerait
            # l'accès des autres postes du réseau local à cette machine
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if not request.url.path.startswith(("/static", "/media")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def error(request: Request, code: int, title: str, message: str, api_message: str):
        if request.url.path.startswith(("/api/", "/media/")):
            return JSONResponse({"error": api_message}, status_code=code)
        return tpl.TemplateResponse(request, "error.html", {"code": code, "title": title, "user": None, "ctx": None,
                                                             "message": message, "path": ""}, status_code=code)

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, exc):
        if request.url.path.startswith(("/api/", "/media/")):
            return error(request, 401, "", "", "connexion requise")
        if request.method != "GET":
            return back("/connexion")
        return back("/connexion?suite=" + quote(request.url.path))

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc):
        return error(request, 403, "Accès refusé", "Votre rôle ne permet pas cette action.", "accès refusé")

    @app.exception_handler(NotFound)
    async def _nf(request: Request, exc):
        return error(request, 404, "Introuvable", "Cette page n'existe pas ou ne vous est pas accessible.", "introuvable")

    # ---------------------------------------------------------- accès scopés (tenant + imam)
    def khutba_or_404(ctx: Ctx, kid: int) -> dict:
        where, args = imam_filter(ctx)
        k = db.one(ctx.conn, f"SELECT k.* FROM khutbas k WHERE k.id = ? AND {where}", kid, *args)
        if not k:
            raise NotFound()
        return k

    def clip_or_404(ctx: Ctx, cid: int) -> tuple[dict, dict]:
        c = db.one(ctx.conn, "SELECT * FROM clips WHERE id = ? AND mosque_id = ?", cid, ctx.mosque["id"])
        if not c:
            raise NotFound()
        return c, khutba_or_404(ctx, c["khutba_id"])

    def imam_filter(ctx: Ctx) -> tuple[str, tuple]:
        """Khutbas visibles (alias k) : celles de la mosquée ; pour un imam, seulement les siennes."""
        if ctx.can("history_all"):
            return "k.mosque_id = ?", (ctx.mosque["id"],)
        return "k.mosque_id = ? AND k.imam_id = ?", (ctx.mosque["id"], ctx.user["id"])

    def count_clips(ctx: Ctx, status: str) -> int:
        where, args = imam_filter(ctx)
        return db.one(ctx.conn, f"SELECT COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id WHERE {where} AND c.status = ?",
                      *args, status)["n"]

    # ---------------------------------------------------------- connexion
    @app.get("/connexion")
    def login_page(request: Request, conn: ConnDep, any_mosque: AnyMosqueDep, suite: str = "/"):
        if auth.load_session(conn, request.cookies.get(auth.COOKIE), request.headers.get("user-agent"),
                             request.client.host if request.client else None):
            return back("/")
        pre = request.cookies.get(PRE_COOKIE) or secrets.token_urlsafe(16)
        resp = tpl.TemplateResponse(request, "login.html", {"pre": pre, "suite": suite, "error": None, "user": None, "ctx": None, "path": "",
                                                 "mosque": any_mosque, "csrf": ""})
        resp.set_cookie(PRE_COOKIE, pre, httponly=True, samesite="strict", secure=_secure(request), max_age=3600)
        return resp

    def _secure(request: Request) -> bool:
        return app.state.secure or _sec(request)

    @app.post("/connexion")
    async def login(request: Request, conn: ConnDep):
        f = await request.form()
        username, password = str(f.get("username", "")).strip(), str(f.get("password", ""))
        suite = str(f.get("suite") or "/")
        if not suite.startswith("/") or suite.startswith("//"):
            suite = "/"
        ip = request.client.host if request.client else "?"
        pre = request.cookies.get(PRE_COOKIE)

        def fail(msg, code=400):
            return tpl.TemplateResponse(request, "login.html", {"pre": pre or secrets.token_urlsafe(16), "suite": suite, "error": msg,
                                                                "username": username, "user": None, "ctx": None, "path": "", "mosque": None, "csrf": ""}, status_code=code)
        if not pre or not secrets.compare_digest(pre, str(f.get("pre", ""))):
            return fail("Session de connexion expirée : rechargez la page.", 403)
        lim = app.state.limiter
        if lim.blocked(ip, username):
            return fail("Trop de tentatives. Réessayez dans quelques minutes.", 429)
        u = await run_in_threadpool(auth.authenticate, conn, username, password)
        if not u:
            lim.fail(ip, username)
            known = db.one(conn, "SELECT mosque_id FROM users WHERE username = ?", username)
            db.audit(conn, known["mosque_id"] if known else None, None, "connexion.echec", ip=ip, identifiant=username[:64])
            return fail("Identifiant ou mot de passe incorrect.", 401)
        lim.success(ip, username)
        token = auth.open_session(conn, u["id"], ip, request.headers.get("user-agent"))
        db.audit(conn, u["mosque_id"], u["id"], "connexion", ip=ip)
        resp = back(suite)
        resp.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=_secure(request), max_age=auth.SESSION_TTL, path="/")
        resp.delete_cookie(PRE_COOKIE)
        return resp

    @app.post("/deconnexion")
    async def logout(ctx: CtxDep):
        await ctx.form()
        auth.close_session(ctx.conn, ctx.request.cookies.get(auth.COOKIE))
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "deconnexion", ip=ctx.ip)
        resp = back("/connexion")
        resp.delete_cookie(auth.COOKIE, path="/")
        return resp

    # ---------------------------------------------------------- tableau de bord
    def live_status(ctx: Ctx) -> dict:
        try:
            return {"ok": True, **live.summarize(live.status(ctx.mosque))}
        except live.OrchestratorError as e:
            return {"ok": False, "error": str(e)}

    @app.get("/")
    def dashboard(ctx: CtxDep):
        where, args = imam_filter(ctx)
        pending, ready = count_clips(ctx, "a_valider"), count_clips(ctx, "valide")
        recent = db.all_(ctx.conn, f"SELECT k.*, u.display_name imam FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id WHERE {where} "
                                   "ORDER BY k.date DESC, k.run_id DESC, k.session_index LIMIT 4", *args)
        total = db.one(ctx.conn, f"SELECT COUNT(*) n FROM khutbas k WHERE {where}", *args)["n"]
        cmds = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                                 "WHERE a.mosque_id = ? AND a.action LIKE 'live.%' ORDER BY a.id DESC LIMIT 5", ctx.mosque["id"])
        return render(ctx, "dashboard.html", live=live_status(ctx), pending=pending, ready=ready, recent=recent, cmds=cmds, total=total)

    @app.get("/api/live")
    def api_live(ctx: CtxDep):
        return live_status(ctx)

    @app.post("/live/{cmd}")
    async def live_cmd(cmd: str, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("live")
        if cmd not in live.COMMANDS:
            raise NotFound()
        arg = (f.get("name") or "").strip() or None
        info = (f.get("info") or "").strip() or None      # fiche janaza : mention sous le nom
        try:
            r = await run_in_threadpool(live.command, ctx.mosque, cmd, arg, info)
            db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.commande", ip=ctx.ip, commande=live.COMMANDS[cmd].label,
                     defunt=arg, mention=info, reponse=r.get("message"), etat=r.get("state"))
            ctx.flash(f"{live.COMMANDS[cmd].label} : {r.get('message', 'ok')}")
        except live.OrchestratorError as e:
            db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "live.echec", ip=ctx.ip, commande=live.COMMANDS[cmd].label, erreur=str(e))
            ctx.flash(f"{live.COMMANDS[cmd].label} : échec — {e}", "err")
        return back("/")

    # ---------------------------------------------------------- historique
    @app.get("/khutbas")
    def khutbas(ctx: CtxDep):
        where, args = imam_filter(ctx)
        rows = db.all_(ctx.conn, f"""SELECT k.*, u.display_name imam,
                   (SELECT COUNT(*) FROM clips c WHERE c.khutba_id = k.id) n_clips,
                   (SELECT COUNT(*) FROM clips c WHERE c.khutba_id = k.id AND c.status = 'a_valider') n_pending
                   FROM khutbas k LEFT JOIN users u ON u.id = k.imam_id WHERE {where}
                   ORDER BY k.date DESC, k.run_id DESC, k.session_index""", *args)
        return render(ctx, "khutbas.html", rows=rows)

    @app.post("/import")
    async def do_import(ctx: CtxDep):
        f = await ctx.form()
        ctx.require("assign")
        run = (f.get("run") or "").strip()
        try:
            if run:
                res = [await run_in_threadpool(importer.import_run, ctx.conn, ctx.mosque, run)]
            else:
                res = await run_in_threadpool(importer.scan, ctx.conn, ctx.mosque)
            nk, nc = sum(r["khutbas_new"] for r in res), sum(r["clips_new"] for r in res)
            db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "import.manuel", ip=ctx.ip, run=run or "tous", khutbas=nk, clips=nc)
            ctx.flash(f"Import : {len(res)} run(s) parcouru(s), {nk} khutba(s) et {nc} clip(s) ajoutés.")
        except (FileNotFoundError, ValueError) as e:
            ctx.flash(f"Import impossible : {e}", "err")
        return back("/khutbas")

    @app.get("/khutbas/{kid}")
    def khutba(kid: int, ctx: CtxDep):
        k = khutba_or_404(ctx, kid)
        data = content.load(ctx.conn, k)
        notes = SessionOutput(k["output_dir"]).notes
        clips = db.all_(ctx.conn, "SELECT * FROM clips WHERE khutba_id = ? ORDER BY idx", k["id"])
        imams = auth.imams(ctx.conn, ctx.mosque["id"]) if ctx.can("assign") else []
        imam = db.one(ctx.conn, "SELECT display_name FROM users WHERE id = ?", k["imam_id"]) if k["imam_id"] else None
        return render(ctx, "khutba.html", k=k, rows=content.transcript_rows(data), verses=verses.for_display(data["verses"]),
                      notes=notes.read_text(encoding="utf-8") if notes.exists() else "", clips=clips, imams=imams, imam=imam,
                      tr=data, has_video=bool(k["video_path"] and Path(k["video_path"]).exists()))

    @app.post("/khutbas/{kid}/imam")
    async def khutba_imam(kid: int, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("assign")
        k = khutba_or_404(ctx, kid)
        iid = auth.imam_id(ctx.conn, ctx.mosque["id"], f.get("imam_id"))
        if f.get("imam_id") and iid is None:
            raise NotFound()
        speaker = (f.get("speaker") or "").strip()[:120]
        ctx.conn.execute("UPDATE khutbas SET imam_id = ?, speaker = ? WHERE id = ?", (iid, speaker, k["id"]))
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "khutba.imam", "khutba", k["id"], ip=ctx.ip, imam=iid, intervenant=speaker or None)
        ctx.flash("Khutba mise à jour.")
        return back(f"/khutbas/{k['id']}")

    @app.post("/khutbas/{kid}/correction")
    async def khutba_correction(kid: int, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("validate")
        k = khutba_or_404(ctx, kid)
        nxt = f.get("next") or f"/khutbas/{k['id']}"
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        try:
            n = content.save_correction(ctx.conn, k, int(f.get("seg", "-1")), f.get("field", ""), f.get("value", ""), ctx.user, ctx.ip)
            ctx.flash("Correction enregistrée" + (f" ; {n} clip(s) à régénérer puis revalider." if n else "."))
        except ValueError as e:
            ctx.flash(str(e), "err")
        return back(nxt)

    # ---------------------------------------------------------- clips
    @app.get("/clips")
    def clips(ctx: CtxDep, statut: str = "a_valider"):
        where, args = imam_filter(ctx)
        cond = "" if statut == "tous" else "AND c.status = ?"
        extra = () if statut == "tous" else (statut,)
        rows = db.all_(ctx.conn, f"""SELECT c.*, k.label klabel, k.date kdate, u.display_name decider
                   FROM clips c JOIN khutbas k ON k.id = c.khutba_id LEFT JOIN users u ON u.id = c.decided_by
                   WHERE {where} {cond} ORDER BY k.date DESC, k.run_id DESC, k.session_index, c.idx""", *args, *extra)
        counts = {r["status"]: r["n"] for r in db.all_(ctx.conn, f"SELECT c.status, COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id WHERE {where} GROUP BY c.status", *args)}
        return render(ctx, "clips.html", rows=rows, statut=statut, counts=counts)

    @app.get("/clips/{cid}")
    def clip(cid: int, ctx: CtxDep):
        c, k = clip_or_404(ctx, cid)
        data = content.load(ctx.conn, k)
        rows = content.transcript_rows(data, c["start"], c["end"])
        regen_ok, regen_why = content.can_regenerate(c)
        hist = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                                 "WHERE a.mosque_id = ? AND ((a.object_type = 'clip' AND a.object_id = ?) OR "
                                 "(a.object_type = 'khutba' AND a.object_id = ? AND a.action LIKE 'correction.%')) ORDER BY a.id DESC LIMIT 50",
                       ctx.mosque["id"], c["id"], k["id"])
        pubs = db.all_(ctx.conn, "SELECT p.*, u.display_name who FROM publications p LEFT JOIN users u ON u.id = p.user_id "
                                 "WHERE p.clip_id = ? ORDER BY p.id DESC", c["id"])
        decider = db.one(ctx.conn, "SELECT display_name FROM users WHERE id = ?", c["decided_by"]) if c["decided_by"] else None
        refs = [r["verse"]["ref"] for r in rows if r["verse"] and not r["verse"]["ambiguous"]]
        return render(ctx, "clip.html", c=c, k=k, rows=rows, regen_ok=regen_ok, regen_why=regen_why, hist=hist, pubs=pubs,
                      decider=decider, connectors=publish.connector_rows(ctx.conn, ctx.mosque),
                      caption=publish.default_caption(k, ctx.mosque, list(dict.fromkeys(refs))),
                      exists=Path(c["file_path"]).exists(), validate_ok=content.can_validate(c)[0], publish_ok=publish.can_publish(c)[0])

    @app.post("/clips/{cid}/decision")
    async def clip_decision(cid: int, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("validate")
        c, k = clip_or_404(ctx, cid)
        try:
            if f.get("decision") == "rouvrir":
                content.reopen(ctx.conn, c, ctx.user, ctx.ip)
                ctx.flash("Clip remis dans la file de validation.")
            else:
                content.decide(ctx.conn, c, f.get("decision", ""), ctx.user, f.get("reason", ""), ctx.ip)
                ctx.flash("Clip validé." if f.get("decision") == "valide" else "Clip rejeté.")
        except ValueError as e:
            ctx.flash(str(e), "err")
        return back(f"/clips/{c['id']}")

    @app.post("/clips/{cid}/regenerer")
    async def clip_regen(cid: int, ctx: CtxDep):
        await ctx.form()
        ctx.require("validate")
        c, k = clip_or_404(ctx, cid)
        try:
            content.start_regeneration(ctx.conn, app.state.db_path, c, ctx.user, ctx.ip, background=regen_background)
            ctx.flash("Régénération lancée (environ une minute) : la page se met à jour toute seule.")
        except ValueError as e:
            ctx.flash(str(e), "err")
        return back(f"/clips/{c['id']}")


    @app.get("/api/clips/{cid}")
    def api_clip(cid: int, ctx: CtxDep):
        c, _ = clip_or_404(ctx, cid)
        return {"id": c["id"], "status": c["status"], "version": c["version"], "regen_state": c["regen_state"], "needs_regen": bool(c["needs_regen"])}

    @app.post("/clips/{cid}/publier")
    async def clip_publish(cid: int, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("publish")
        c, k = clip_or_404(ctx, cid)
        res = await run_in_threadpool(publish.publish, ctx.conn, c, f.get("connector", ""), ctx.user, f.get("caption", ""), ctx.ip)
        ctx.flash(f"Publié : {res.location}" if res.ok else f"Publication refusée : {res.message}", "ok" if res.ok else "err")
        return back(f"/clips/{c['id']}")

    @app.get("/publication")
    def publication(ctx: CtxDep):
        ctx.require("publish")
        ready = db.all_(ctx.conn, "SELECT c.*, k.label klabel, k.date kdate FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                                  "WHERE c.mosque_id = ? AND c.status = 'valide' ORDER BY c.decided_at DESC", ctx.mosque["id"])
        pubs = db.all_(ctx.conn, "SELECT p.*, u.display_name who, c.idx, c.kind, k.label klabel, k.date kdate FROM publications p "
                                 "JOIN clips c ON c.id = p.clip_id JOIN khutbas k ON k.id = c.khutba_id LEFT JOIN users u ON u.id = p.user_id "
                                 "WHERE p.mosque_id = ? ORDER BY p.id DESC LIMIT 100", ctx.mosque["id"])
        return render(ctx, "publication.html", ready=ready, pubs=pubs, connectors=publish.connector_rows(ctx.conn, ctx.mosque))

    # ---------------------------------------------------------- médias (par identifiant : pas de chemin dans l'URL)
    @app.get("/media/khutba/{kid}.mp4")
    def media_khutba(kid: int, ctx: CtxDep):
        k = khutba_or_404(ctx, kid)
        if not k["video_path"] or not Path(k["video_path"]).exists():
            raise NotFound()
        return FileResponse(k["video_path"], media_type="video/mp4")

    @app.get("/media/clip/{cid}.mp4")
    def media_clip(cid: int, ctx: CtxDep):
        c, _ = clip_or_404(ctx, cid)
        if not Path(c["file_path"]).exists():
            raise NotFound()
        return FileResponse(c["file_path"], media_type="video/mp4")

    # ---------------------------------------------------------- journal
    @app.get("/journal")
    def journal(ctx: CtxDep, action: str = ""):
        ctx.require("journal")
        rows = db.all_(ctx.conn, "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                                 "WHERE a.mosque_id = ? AND (? = '' OR a.action LIKE ? || '%') ORDER BY a.id DESC LIMIT 300",
                       ctx.mosque["id"], action, action)
        return render(ctx, "journal.html", rows=rows, action=action)

    # ---------------------------------------------------------- compte
    @app.get("/compte")
    def account(ctx: CtxDep):
        return render(ctx, "account.html")

    @app.post("/compte")
    async def account_pw(ctx: CtxDep):
        f = await ctx.form()
        if not await run_in_threadpool(auth.verify_password, ctx.user["password_hash"], f.get("current", "")):
            ctx.flash("Mot de passe actuel incorrect.", "err")
            return back("/compte")
        if f.get("new") != f.get("confirm"):
            ctx.flash("Les deux nouveaux mots de passe diffèrent.", "err")
            return back("/compte")
        try:
            await run_in_threadpool(auth.set_password, ctx.conn, ctx.user["id"], f.get("new", ""))
        except ValueError as e:
            ctx.flash(str(e), "err")
            return back("/compte")
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", ctx.user["id"], ip=ctx.ip)
        return back("/connexion")

    # ---------------------------------------------------------- administration
    @app.get("/admin/utilisateurs")
    def users(ctx: CtxDep):
        ctx.require("admin")
        rows = db.all_(ctx.conn, "SELECT * FROM users WHERE mosque_id = ? ORDER BY role, display_name", ctx.mosque["id"])
        return render(ctx, "users.html", rows=rows)

    @app.post("/admin/utilisateurs")
    async def user_create(ctx: CtxDep):
        f = await ctx.form()
        ctx.require("admin")
        try:
            uid = await run_in_threadpool(auth.create_user, ctx.conn, ctx.mosque["id"], f.get("username", ""),
                                          f.get("password", ""), f.get("role", ""), f.get("display_name", ""))
            db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.cree", "utilisateur", uid, ip=ctx.ip,
                     identifiant=f.get("username"), role=f.get("role"))
            ctx.flash(f"Compte « {f.get('username')} » créé.")
        except ValueError as e:
            ctx.flash(str(e), "err")
        return back("/admin/utilisateurs")

    @app.post("/admin/utilisateurs/{uid}")
    async def user_update(uid: int, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("admin")
        u = db.one(ctx.conn, "SELECT * FROM users WHERE id = ? AND mosque_id = ?", uid, ctx.mosque["id"])
        if not u:
            raise NotFound()
        try:
            if f.get("password"):
                await run_in_threadpool(auth.set_password, ctx.conn, uid, f["password"])
                db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.mot_de_passe", "utilisateur", uid, ip=ctx.ip)
            role, active = f.get("role", u["role"]), 1 if f.get("active") == "1" else 0
            if role not in auth.ROLES:
                raise ValueError("rôle inconnu")
            if uid == ctx.user["id"] and (role != "admin" or not active):
                raise ValueError("vous ne pouvez pas retirer vos propres droits d'administrateur")
            ctx.conn.execute("UPDATE users SET role = ?, active = ?, display_name = ? WHERE id = ?",
                             (role, active, (f.get("display_name") or u["display_name"]).strip(), uid))
            if not active:
                ctx.conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
            db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "utilisateur.modifie", "utilisateur", uid, ip=ctx.ip, role=role, actif=bool(active))
            ctx.flash(f"Compte « {u['username']} » mis à jour.")
        except ValueError as e:
            ctx.flash(str(e), "err")
        return back("/admin/utilisateurs")

    # ---------------------------------------------------------- choix des dossiers (machine locale)
    @app.get("/api/dossiers")
    def api_dirs(ctx: CtxDep, path: str = ""):
        """Arborescence de la machine qui héberge l'application, pour le bouton « Parcourir »."""
        ctx.require("admin")
        try:
            return {"roots": dirs_mod.roots(), **dirs_mod.listing(path or None)}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    # ---------------------------------------------------------- logo de l'interface
    @app.get("/logo/{slug}")
    def logo_file(slug: str, conn: ConnDep):
        m = db.one(conn, "SELECT * FROM mosques WHERE slug = ?", slug)
        if not m or not (m.get("logo_path") or "").strip():
            raise NotFound()
        f = branding.path_for(m)
        if not f.exists():
            raise NotFound()
        ext = f.suffix.lower()
        return FileResponse(f, media_type=branding.EXT.get(ext, "application/octet-stream"),
                            headers={"Cache-Control": "private, max-age=300"})

    @app.post("/admin/logo")
    async def logo_save(ctx: CtxDep):
        ctx.require("admin")
        f = await ctx.form()   # jeton CSRF vérifié ; le fichier est gardé en mémoire (2 Mo max)
        up = ctx.files.get("logo")
        if up is None or not getattr(up, "filename", ""):
            ctx.flash("Aucun fichier choisi.", "err")
            return back(section("image"))
        content_bytes = await up.read()
        try:
            rel = branding.save(ctx.mosque, up.filename, content_bytes)
        except ValueError as e:
            ctx.flash(str(e)[0].upper() + str(e)[1:] + ".", "err")
            return back(section("image"))
        db.update_mosque(ctx.conn, ctx.mosque["id"], logo_path=rel)
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.logo", "mosque", ctx.mosque["id"], ip=ctx.ip, fichier=rel)
        ctx.flash("Logo enregistré : il apparaît dans la barre et comme icône de l'onglet.")
        return back(section("image"))

    @app.post("/admin/logo/supprimer")
    async def logo_clear(ctx: CtxDep):
        await ctx.form()
        ctx.require("admin")
        branding.clear(ctx.mosque)
        db.update_mosque(ctx.conn, ctx.mosque["id"], logo_path="")
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.logo.retire", "mosque", ctx.mosque["id"], ip=ctx.ip)
        ctx.flash("Logo retiré : emblème neutre repris.")
        return back(section("image"))

    @app.get("/admin/mosquee")
    def mosque_page(ctx: CtxDep, c: str = ""):
        ctx.require("admin")
        imams = auth.imams(ctx.conn, ctx.mosque["id"])
        connecteurs = publish.connector_rows(ctx.conn, ctx.mosque)
        # un seul connecteur affiché à la fois : quatre formulaires empilés faisaient une page démesurée
        actif = next((x for x in connecteurs if x["key"] == c), connecteurs[0] if connecteurs else None)
        return render(ctx, "mosque.html", imams=imams, dirs=db.mosque_dirs(ctx.mosque), connectors=connecteurs, conn=actif,
                      llm=llm.config(), llm_now=llm.settings().label(), llm_on=llm.enabled(), llm_file=str(llm.config_path()))

    @app.post("/admin/modele")
    async def llm_save(ctx: CtxDep):
        f = await ctx.form()
        ctx.require("admin")
        url = (f.get("llm_url") or "").strip()
        model = (f.get("llm_model") or "").strip()
        proto = (f.get("llm_protocol") or "").strip()
        if url and not re.match(r"^https?://", url):
            ctx.flash("L'adresse du modèle doit commencer par http:// ou https://", "err")
            return back(section("modele"))
        if proto not in ("", "openai", "anthropic"):
            ctx.flash("Format d'API inconnu.", "err")
            return back(section("modele"))
        llm.save_config(enabled=f.get("llm_enabled") == "1", url=url, model=model, protocol=proto)
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "modele.configure", "mosque", ctx.mosque["id"], ip=ctx.ip,
                 actif=f.get("llm_enabled") == "1", url=url, modele=model, protocole=proto, fichier=str(llm.config_path()))
        ctx.flash("Modèle de langage enregistré. Le pipeline l'applique au prochain lancement (les variables "
                  "d'environnement et les options de ligne de commande restent prioritaires).")
        return back(section("modele"))

    @app.post("/admin/modele/tester")
    def llm_test(ctx: CtxDep):
        ctx.require("admin")
        ok, msg = llm.probe()
        ctx.flash(msg, "" if ok else "err")
        return back(section("modele"))

    @app.post("/admin/mosquee")
    async def mosque_save(ctx: CtxDep):
        f = await ctx.form()
        ctx.require("admin")
        sect = "live" if "orchestrator_url" in f or "orchestrator_token" in f else (
            "dossiers" if any(k in f for k in ("state_dir", "output_dir", "publish_dir")) else "identite")
        vals = {k: (f.get(k) or "").strip() for k in db.MOSQUE_SETTINGS if k != "logo_path" and k in f}
        current = {k: ctx.mosque.get(k) or "" for k in db.MOSQUE_SETTINGS if k != "logo_path"}
        vals = {**current, **vals}          # un formulaire partiel ne vide pas les autres réglages
        if not vals["name"]:
            ctx.flash("Le nom est obligatoire.", "err")
            return back(section(sect))
        if vals["orchestrator_url"] and not re.match(r"^https?://", vals["orchestrator_url"]):
            ctx.flash("L'adresse de l'orchestrateur doit commencer par http:// ou https://", "err")
            return back(section(sect))
        if vals["orchestrator_token"] == db.MASK:
            vals["orchestrator_token"] = ctx.mosque["orchestrator_token"]
        di = auth.imam_id(ctx.conn, ctx.mosque["id"], f.get("default_imam_id", ctx.mosque["default_imam_id"]))
        try:
            db.update_mosque(ctx.conn, ctx.mosque["id"], **vals, default_imam_id=di, auto_import=1 if f.get("auto_import") == "1" else 0)
        except ValueError as e:
            ctx.flash(f"{str(e)[0].upper()}{str(e)[1:]}.", "err")
            return back(section(sect))
        db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.configuree", "mosquee", ctx.mosque["id"], ip=ctx.ip,
                 champs=[k for k in vals if k != "orchestrator_token" and str(vals[k]) != str(ctx.mosque.get(k))])
        ctx.flash("Configuration enregistrée.")
        return back(section(sect))

    @app.post("/admin/connecteurs/{key}")
    async def connector_save(key: str, ctx: CtxDep):
        f = await ctx.form()
        ctx.require("admin")
        if key not in publish.CONNECTORS:
            raise NotFound()
        publish.save_connector(ctx.conn, ctx.mosque["id"], key, f.get("enabled") == "1", f, ctx.user, ctx.ip)
        ctx.flash(f"Connecteur « {publish.CONNECTORS[key].label} » enregistré.")
        return back(section("connecteurs", key))

    @app.get("/sante")
    def health():
        return {"ok": True}

    return app
