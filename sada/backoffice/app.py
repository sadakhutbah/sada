"""Web application of the back-office: JSON API (`/api/v1`) and React front served at the root.

Page rendering (Jinja2) has been removed: the only interface is the React application of `web/`,
built into `web/dist` then served as static files. This module therefore only produces
JSON (API and media); screens are described once, in the front.
"""
from __future__ import annotations

import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import paths
from ..paths import ASSETS
from . import auth, branding, content, db, docpage, gabarit, habillage, importer, live, traitement
from . import screens as screens_mod
from .api import router as api_router
from .context import (ConnDep, CtxDep, Forbidden, Invalid, LoginRequired, NotFound, _sec,
                      clip_or_404, khutba_or_404, live_status)

HERE = Path(__file__).resolve().parent
FRONT = HERE.parent.parent / "web" / "dist"
DOCS = HERE.parent.parent / "docs"

# La table des matières servie sous /docs : un seul endroit à tenir à jour. Le titre et le résumé
# existent en français et en anglais. Les fichiers vivent dans ``docs/fr/<slug>.md`` et
# ``docs/en/<slug_en>.md`` ; l'anglais est facultatif et, s'il manque, la page française est servie
# avec un avertissement. Sous /docs/en/, le slug anglais et le slug français mènent à la même page.
DOCUMENTS = (
    {"slug": "ecrans-mode-emploi", "slug_en": "screens",
     "titre_fr": "Mode d'emploi des écrans",
     "titre_en": "Screen manual",
     "resume_fr": "ce que sont les six écrans pleins, comment changer leurs textes et ce qui ne marche pas comme on croit",
     "resume_en": "the six full screens, how to change their texts, and what does not work the way one expects"},
    {"slug": "orchestrateur", "slug_en": "orchestrator",
     "titre_fr": "L'orchestrateur et les jetons",
     "titre_en": "The orchestrator and the tokens",
     "resume_fr": "les deux programmes, pourquoi ils se parlent, ce que sont les deux jetons et l'ordre de démarrage",
     "resume_en": "the two programs, why they talk to each other, what the two tokens are, and the startup order"},
    {"slug": "agent-gabarit-ecrans", "slug_en": "screen-template-prompt",
     "titre_fr": "Guide de gabarit d'écrans",
     "titre_en": "Screen template guide",
     "resume_fr": "la consigne à donner à un assistant conversationnel pour obtenir un visuel d'écrans à soi",
     "resume_en": "the instruction to give a chat assistant to get a screen mockup of your own"},
    {"slug": "mise-en-route", "slug_en": "getting-started",
     "titre_fr": "Mise en route",
     "titre_en": "Getting started",
     "resume_fr": "la première installation, les quatre écrans d'accueil, et ce qui se passe au premier direct",
     "resume_en": "the first installation, the four welcome screens, and what happens on the first live"},
    {"slug": "connecteurs", "slug_en": "connectors",
     "titre_fr": "Connecteurs : Facebook, YouTube, Instagram",
     "titre_en": "Connectors: Facebook, YouTube, Instagram",
     "resume_fr": "ce qu'il faut obtenir côté plateforme, où le saisir, et ce que le connecteur ne fait pas",
     "resume_en": "what to obtain from each platform, where to enter it, and what the connector does not do"},
    {"slug": "rediffusions", "slug_en": "recordings",
     "titre_fr": "Rediffusions et compartiment S3",
     "titre_en": "Recordings and S3 buckets",
     "resume_fr": "les clips, le dossier publié, et l'envoi facultatif vers un compartiment S3 ou R2",
     "resume_en": "clips, the published folder, and the optional upload to an S3 or R2 bucket"},
)


# ------------------------------------------------------------------ application

def create_app(db_path: str | Path = db.DEFAULT_DB, secure_cookies: bool = False, auto_import_s: float = 0,
               regen_background: bool = True) -> FastAPI:
    """regen_background=False: clip regeneration happens inside the request (tests)."""
    db.init(db_path)
    stop = threading.Event()

    conn = db.connect(db_path)
    try:
        content.recover_interrupted(conn)
        habillage.recover_interrupted(conn)
    finally:
        conn.close()

    def import_all():
        conn = db.connect(db_path)
        try:
            for m in db.all_(conn, "SELECT * FROM mosques WHERE auto_import = 1"):
                m = dict(m)
                traitement.tour(conn, m, log=lambda msg: print("[traitement]", msg, flush=True))
                importer.scan(conn, m, log=lambda msg: print("[import]", msg, flush=True))
        except Exception as e:  # noqa: BLE001
            print("[import] erreur :", e, flush=True)
        finally:
            conn.close()

    def importer_loop():
        # Premier tour tout de suite, en fond : un traitement long ne retarde pas le démarrage.
        import_all()
        while not stop.wait(auto_import_s):
            import_all()

    @asynccontextmanager
    async def lifespan(app):
        if auto_import_s:
            c = db.connect(db_path)
            try:
                for m in db.all_(c, "SELECT * FROM mosques WHERE auto_import = 1"):
                    traitement.reprendre(dict(m))
            finally:
                c.close()
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

    app = FastAPI(title="Sadā", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
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
        csp = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
               "script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
               "form-action 'self'; base-uri 'none'")
        if request.url.path.startswith("/screens/"):
            # The imported gabarit is shown in an iframe of the settings page: same origin only.
            # Everywhere else stays unframeable (frame-ancestors 'none' / X-Frame-Options DENY).
            csp = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
            resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = csp
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if _sec(request):
            # only behind HTTPS (proxy or central server): an HSTS on local HTTP would block
            # access to this machine from the other computers on the local network
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
    def media_clip(cid: int, ctx: CtxDep, download: bool = False):
        """``?download=1`` : même fichier, mêmes droits, mais servi en pièce jointe (bouton
        « Télécharger » du téléphone). Le nom ne reprend jamais le chemin sur la machine."""
        c, _ = clip_or_404(ctx, cid)
        if not Path(c["file_path"]).exists():
            raise NotFound()
        if download:
            return FileResponse(c["file_path"], media_type="video/mp4",
                                filename=f"sada-clip-{cid}-v{c['version']}.mp4")
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

    # ------------------------------------------------- imported screen template (reference)
    @app.get("/screens/{slug}/ecrans.html")
    def screens_gabarit_file(slug: str, ctx: CtxDep, ecran: str | None = None):
        """The mosque's imported template, shown in the settings iframe (same origin, admin only).

        ``ecran`` déplace l'écran mis en avant, pour que l'aperçu suive l'onglet ouvert plutôt que
        de montrer toujours le même. Le fichier conservé n'est pas modifié.
        """
        ctx.require("admin")
        if slug != ctx.mosque["slug"]:
            raise NotFound()          # another mosque's reference is not readable by slug
        f = gabarit.path_for(ctx.mosque)
        if not f.is_file():
            raise NotFound()
        if ecran:
            page = gabarit.pour_ecran(f.read_text(encoding="utf-8", errors="replace"), ecran)
            # Les variables d'écran sont remplies comme sur le direct, sinon l'aperçu affiche
            # « Début à {heure} » quand le moteur affiche « Début à 14 h 36 ».
            page = gabarit.remplir(page, screens_mod.valeurs_apercu(ctx.mosque, ecran))
            return HTMLResponse(page, headers={"Cache-Control": "no-store"})
        return FileResponse(f, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store"})

    @app.get("/screens/assets/fonts/{nom}")
    def screens_gabarit_font(nom: str, ctx: CtxDep):
        """Les polices du moteur, pour que l'aperçu du gabarit ait la même typographie que le direct.

        Un modèle écrit ``../assets/fonts/<fichier>.ttf`` : depuis /screens/<slug>/ c'est cette
        adresse-là qu'il demande. Sans elle il retombait sur les polices du système, et l'aperçu ne
        ressemblait plus à ce que la mosquée verra.
        """
        ctx.require("admin")
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.ttf", nom):
            raise NotFound()
        f = ASSETS / "fonts" / nom
        if not f.is_file():
            raise NotFound()
        return FileResponse(f, media_type="font/ttf", headers={"Cache-Control": "private, max-age=86400"})

    # ---------------------------------------------------------- documentation
    # Une seule table de matières : les pages, leur version texte et leur traduction sont dérivées.
    def _doc(slug: str, langue: str) -> tuple[Path, bool]:
        """Le fichier à servir, et si c'est bien la langue demandée (sinon repli sur le français)."""
        d = _document(slug)
        fr = DOCS / "fr" / f"{d['slug']}.md"
        if langue == "en":
            en = DOCS / "en" / f"{d['slug_en']}.md"
            if en.exists():
                return en, True
            return fr, False
        return fr, True


    @app.get("/docs")
    def docs_index(langue: str = "en"):
        """La liste des documents, dans la langue demandée."""
        en = langue == "en"
        lignes = ["# Documentation" if not en else "# Documentation", "",
                  ("These documents are served by this instance: they describe what it does, and they"
                   " are updated with it.") if en
                  else ("Ces documents sont servis par cette instance : ils décrivent ce qu'elle fait,"
                        " et se mettent à jour avec elle."), ""]
        for d in DOCUMENTS:
            titre = d["titre_en"] if en else d["titre_fr"]
            resume = d["resume_en"] if en else d["resume_fr"]
            url = f"/docs/en/{d['slug_en']}" if en else f"/docs/{d['slug']}"
            lignes.append(f"- [{titre}]({url}) — {resume}")
        return HTMLResponse(docpage.page("\n".join(lignes), titre="Documentation", source=None,
                                         lang="en" if en else "fr",
                                         alterne=("/docs?langue=fr" if en else "/docs",
                                                  "Français" if en else "English")))

    # Les adresses en « .md » passent avant, sinon le joker de {slug} les avale.
    @app.get("/docs/en/{slug}.md")
    def doc_source_en(slug: str):
        return _doc_source(slug, "en")

    @app.get("/docs/{slug}.md")
    def doc_source(slug: str):
        return _doc_source(slug, "fr")

    @app.get("/docs/en/{slug}")
    def doc_page_en(slug: str):
        return _doc_page(slug, "en")

    @app.get("/docs/{slug}")
    def doc_page(slug: str):
        return _doc_page(slug, "fr")

    def _doc_page(slug: str, langue: str):
        """Le document rendu en page. Lire du Markdown dans un navigateur n'est pas lire un mode d'emploi."""
        d = _document(slug)
        slug, slug_en = d["slug"], d["slug_en"]
        f, exact = _doc(slug, langue)
        if not f.exists():
            raise NotFound()
        avis = None
        if not exact:
            avis = ("La version anglaise de ce document n'est pas encore écrite. Le texte affiché est "
                    "le français." if langue == "en" else None)
        alterne = (f"/docs/{slug}" if langue == "en" else f"/docs/en/{slug_en}",
                   "Français" if langue == "en" else "English")
        return HTMLResponse(docpage.page(
            f.read_text(encoding="utf-8"),
            titre=(d["titre_en"] if langue == "en" else d["titre_fr"]),
            source=(f"/docs/en/{slug_en}.md" if langue == "en" and exact else f"/docs/{slug}.md"),
            lang=langue, alterne=alterne, avis=avis))

    def _doc_source(slug: str, langue: str):
        """Le même texte, brut : pour le coller dans un chat quand un lien ne s'ouvre pas."""
        _document(slug)
        f, exact = _doc(slug, langue)
        if not f.exists() or not exact:
            raise NotFound()
        return FileResponse(f, media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": f'inline; filename="{f.name}"'})

    def _document(slug: str) -> dict:
        for d in DOCUMENTS:
            if slug in (d["slug"], d["slug_en"]):
                return d
        raise NotFound()

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