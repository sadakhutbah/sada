"""Application web du back-office : API JSON (`/api/v1`) et front React servi à la racine.

Le rendu des pages (Jinja2) a été retiré : l'unique interface est l'application React de `web/`,
construite dans `web/dist` puis servie en fichiers statiques. Ce module ne produit donc plus que
du JSON (API et médias) ; les écrans sont décrits une seule fois, dans le front.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, branding, content, db, importer, live

HERE = Path(__file__).resolve().parent
FRONT = HERE.parent.parent / "web" / "dist"
PRE_COOKIE = "minbar_pre"


# ------------------------------------------------------------------ erreurs

class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    """Requête lisible mais refusée par une règle métier (champ vide, valeur inconnue…)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ contexte

class Ctx:
    def __init__(self, request: Request, conn, session: dict, user: dict, mosque: dict):
        self.request, self.conn, self.session, self.user, self.mosque = request, conn, session, user, mosque
        self.ip = request.client.host if request.client else None

    def can(self, perm: str) -> bool:
        return auth.can(self.user, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            db.audit(self.conn, self.mosque["id"], self.user["id"], "acces.refuse", ip=self.ip,
                     chemin=self.request.url.path, droit=perm)
            raise Forbidden()

    def csrf_ok(self) -> bool:
        """Jeton anti-rejeu de l'écriture : en-tête `X-CSRF` (le front), ou paramètre `csrf`."""
        jeton = self.request.headers.get("x-csrf") or self.request.query_params.get("csrf")
        return auth.check_csrf(self.session, jeton)

    def need_csrf(self) -> None:
        if not self.csrf_ok():
            raise Forbidden()

    async def body(self) -> dict:
        """Corps de la requête, après vérification du jeton CSRF.

        JSON pour le front ; un formulaire nu reste accepté pour un client sans en-tête.
        Les fichiers envoyés (logo) sont gardés dans `self.files`.
        """
        self.need_csrf()
        self.files: dict = {}
        ctype = (self.request.headers.get("content-type") or "").lower()
        try:
            if ctype.startswith("application/json"):
                corps = await self.request.json()
            else:
                form = await self.request.form()
                self.files = {k: v for k, v in form.items() if not isinstance(v, str)}
                corps = dict(form)
        except Exception:                       # noqa: BLE001 — corps vide ou illisible
            corps = {}
        if not isinstance(corps, dict):
            corps = {}
        return {k: (v if isinstance(v, str) else "") for k, v in corps.items()}


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


def get_ctx(request: Request, conn: ConnDep) -> Ctx:
    res = auth.load_session(conn, request.cookies.get(auth.COOKIE), request.headers.get("user-agent"),
                            request.client.host if request.client else None)
    if not res:
        raise LoginRequired()
    s, u = res
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", u["mosque_id"])
    if not m:
        raise LoginRequired()
    return Ctx(request, conn, s, u, m)


CtxDep = Annotated[Ctx, Depends(get_ctx)]


# ------------------------------------------------------------------ règles partagées

def imam_filter(ctx: Ctx) -> tuple[str, tuple]:
    """Khutbas visibles (alias k) : celles de la mosquée ; pour un imam, seulement les siennes."""
    if ctx.can("history_all"):
        return "k.mosque_id = ?", (ctx.mosque["id"],)
    return "k.mosque_id = ? AND k.imam_id = ?", (ctx.mosque["id"], ctx.user["id"])


def count_clips(ctx: Ctx, status: str) -> int:
    where, args = imam_filter(ctx)
    return db.one(ctx.conn, f"SELECT COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                            f"WHERE {where} AND c.status = ?", *args, status)["n"]


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


def live_status(ctx: Ctx) -> dict:
    try:
        return {"ok": True, **live.summarize(live.status(ctx.mosque))}
    except live.OrchestratorError as e:
        return {"ok": False, "error": str(e)}


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
    app.state.regen_background = regen_background
    # Polices et emblème : les seuls fichiers servis par le backend (le front a son propre build).
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ---------------------------------------------------------- middleware et erreurs
    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                                   "form-action 'self'; base-uri 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if _sec(request):
            # uniquement derrière HTTPS (proxy ou serveur central) : un HSTS sur du HTTP local bloquerait
            # l'accès des autres postes du réseau local à cette machine
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        # Les fichiers du front portent une empreinte dans leur nom : ils se gardent en cache.
        # Tout le reste (API, index.html) ne doit jamais être servi depuis un cache.
        if not request.url.path.startswith(("/static", "/media", "/assets")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def error(request: Request, code: int, message: str):
        return JSONResponse({"error": message}, status_code=code)

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, exc):
        return error(request, 401, "connexion requise")

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc):
        return error(request, 403, "accès refusé")

    @app.exception_handler(NotFound)
    async def _nf(request: Request, exc):
        return error(request, 404, "introuvable")

    @app.exception_handler(Invalid)
    async def _invalid(request: Request, exc: Invalid):
        return JSONResponse({"error": "refusé", "message": exc.message}, status_code=422)

    @app.exception_handler(ValueError)
    async def _value(request: Request, exc: ValueError):
        return JSONResponse({"error": "refusé", "message": str(exc)}, status_code=422)

    # ---------------------------------------------------------- état du direct
    @app.get("/api/live")
    def api_live(ctx: CtxDep):
        return live_status(ctx)

    # ---------------------------------------------------------- médias (par identifiant : pas de chemin dans l'URL)
    @app.get("/media/sermon/{kid}.mp4")
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

    # ---------------------------------------------------------- logo de l'interface
    @app.get("/logo/{slug}")
    def logo_file(slug: str, conn: ConnDep):
        m = db.one(conn, "SELECT * FROM mosques WHERE slug = ?", slug)
        if not m or not (m.get("logo_path") or "").strip():
            raise NotFound()
        f = branding.path_for(m)
        if not f.exists():
            raise NotFound()
        return FileResponse(f, media_type=branding.EXT.get(f.suffix.lower(), "application/octet-stream"),
                            headers={"Cache-Control": "private, max-age=300"})

    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------------------------------------------------------- API JSON du front
    from . import api_v1            # import tardif : api_v1 lit ce module
    app.include_router(api_v1.router)

    # ------------------------------------------------- anciennes adresses françaises
    # Conservées en redirection permanente : un signet ou un lien déjà envoyé continue de fonctionner.
    ANCIENNES = {
        "/connexion": "/login", "/deconnexion": "/", "/compte": "/settings", "/journal": "/audit",
        "/sermons": "/khutbas", "/publication": "/publications", "/sante": "/health",
        "/api/dossiers": "/api/v1/folders", "/admin/utilisateurs": "/users",
        "/admin/mosquee": "/settings", "/admin/modele": "/settings",
        "/admin/modele/tester": "/api/v1/settings/model/test",
    }
    ANCIENS_MOTIFS = (
        ("/sermons/{kid}", "/khutbas/{kid}"),
        ("/clips/{cid}/regenerer", "/api/v1/clips/{cid}/regenerate"),
        ("/clips/{cid}/publier", "/api/v1/clips/{cid}/publish"),
        ("/media/khutba/{kid}.mp4", "/media/sermon/{kid}.mp4"),
        ("/admin/connecteurs/{key}", "/settings"),
    )

    def _redirection(cible: str):
        def aller(request: Request):
            adresse = cible
            for nom, valeur in request.path_params.items():
                adresse = adresse.replace("{" + nom + "}", str(valeur))
            q = request.url.query
            return RedirectResponse(adresse + (f"?{q}" if q else ""), status_code=308)
        return aller

    for _vieux, _neuf in ANCIENNES.items():
        app.add_api_route(_vieux, _redirection(_neuf), methods=["GET", "POST"], include_in_schema=False)
    for _vieux, _neuf in ANCIENS_MOTIFS:
        app.add_api_route(_vieux, _redirection(_neuf), methods=["GET", "POST"], include_in_schema=False)

    # ---------------------------------------------------------------- front React
    # Servi en dernier recours : toute route déclarée au-dessus garde la priorité, et les chemins
    # inconnus retombent sur index.html pour que le routeur du front gère ses propres adresses.
    if FRONT.is_dir():
        # Repli explicite sur index.html : le routeur du front gère ses propres adresses
        # (/khutbas/12), le serveur ne doit pas répondre 404 sur ces chemins.
        app.frontend("/", directory=str(FRONT), fallback="index.html")
    else:
        @app.get("/")
        def _front_absent():
            return JSONResponse({"error": "front non construit : npm --prefix web run build"}, status_code=503)

    return app
