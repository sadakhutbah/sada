"""Web application of the back-office: JSON API (`/api/v1`) and React front served at the root.

Page rendering (Jinja2) has been removed: the only interface is the React application of `web/`,
built into `web/dist` then served as static files. This module therefore only produces
JSON (API and media); screens are described once, in the front.
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import paths
from . import auth, branding, content, db, importer, live
from .api import router as api_router
from .context import (ConnDep, CtxDep, Forbidden, Invalid, LoginRequired, NotFound, _sec,
                      clip_or_404, khutba_or_404, live_status)

HERE = Path(__file__).resolve().parent
FRONT = HERE.parent.parent / "web" / "dist"
DOCS = HERE.parent.parent / "docs"


# ------------------------------------------------------------------ application

def create_app(db_path: str | Path = db.DEFAULT_DB, secure_cookies: bool = False, auto_import_s: float = 0,
               regen_background: bool = True) -> FastAPI:
    """regen_background=False: clip regeneration happens inside the request (tests)."""
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

        def chauffe_statut():
            # The first display of the dashboard must not pay for the orchestrator's wait.
            conn = db.connect(db_path)
            try:
                for m in db.all_(conn, "SELECT * FROM mosques WHERE orchestrator_url != ''"):
                    live.status_cached(dict(m))
            except Exception:  # noqa: BLE001 — an unavailable status does not prevent serving
                pass
            finally:
                conn.close()

        threading.Thread(target=chauffe_statut, daemon=True, name="statut-live-chauffe").start()
        yield
        stop.set()

    app = FastAPI(title="Minbar Live — back-office", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db_path = str(db_path)
    app.state.secure = secure_cookies
    app.state.limiter = auth.RateLimiter()
    app.state.regen_background = regen_background
    app.state.events_file = str(paths.EVENTS)
    # Fonts and emblem: the only files served by the backend (the front has its own build).
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ---------------------------------------------------------- middleware and errors
    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                                   "form-action 'self'; base-uri 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if _sec(request):
            # only behind HTTPS (proxy or central server): an HSTS on local HTTP would block
            # access to this machine from the other computers on the local network
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        # Front files carry a fingerprint in their name: they are kept in the cache.
        # Everything else (API, index.html) must never be served from a cache.
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

    # ---------------------------------------------------------- live status
    @app.get("/api/live")
    def api_live(ctx: CtxDep):
        return live_status(ctx)

    # ---------------------------------------------------------- media (by id: no path in the URL)
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

    # ---------------------------------------------------------- interface logo
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

    # ---------------------------------------------------------- public guide (for any chat assistant)
    @app.get("/docs/agent-gabarit-ecrans.md")
    def guide_gabarit():
        """The guide a mosque hands to any chat assistant. No login: it holds nothing private."""
        f = DOCS / "agent-gabarit-ecrans.md"
        if not f.exists():
            raise NotFound()
        return FileResponse(f, media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": "inline; filename=\"agent-gabarit-ecrans.md\""})

    @app.get("/docs/ecrans-mode-emploi.md")
    def doc_ecrans():
        """The human manual for the screen texts, written for the mosque administrator."""
        f = DOCS / "ecrans-mode-emploi.md"
        if not f.exists():
            raise NotFound()
        return FileResponse(f, media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": "inline; filename=\"ecrans-mode-emploi.md\""})

    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------------------------------------------------------- JSON API of the front
    app.include_router(api_router)

    # ------------------------------------------------- old French addresses
    # Kept as permanent redirects: a bookmark or a link already sent keeps working.
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

    # ---------------------------------------------------------------- React front
    # Served as a last resort: every route declared above keeps priority, and unknown
    # paths fall back to index.html so that the front router handles its own addresses.
    if FRONT.is_dir():
        # Explicit fallback to index.html: the front router handles its own addresses
        # (/khutbas/12), the server must not answer 404 on those paths.
        app.frontend("/", directory=str(FRONT), fallback="index.html")
    else:
        @app.get("/")
        def _front_absent():
            return JSONResponse({"error": "front non construit : npm --prefix web run build"}, status_code=503)

    return app
