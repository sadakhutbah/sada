"""Habillage d'une vidéo de session : réglages, aperçu, rendu en fond, fichiers servis par identifiant.

Lecture : quiconque voit la session. Écriture (aperçu, rendu) : le droit `assign`, celui de l'import
de vidéo. Aucun chemin de la machine dans les réponses : les fichiers passent par les adresses ci-dessous.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from ...i18n import _
from .. import habillage
from ..context import CtxDep, Invalid, NotFound, khutba_or_404
from .common import _ok

router = APIRouter()


def _vue(ctx, k: dict) -> dict:
    return {**habillage.etat(ctx.conn, k, ctx.mosque), "can_edit": ctx.can("assign")}


@router.get("/sermons/{kid}/habillage")
def habillage_etat(kid: int, ctx: CtxDep):
    return _vue(ctx, khutba_or_404(ctx, kid))


@router.post("/sermons/{kid}/habillage/apercu")
async def habillage_apercu(kid: int, ctx: CtxDep):
    brut = await ctx.raw_body()
    ctx.require("assign")
    k = khutba_or_404(ctx, kid)
    try:
        await run_in_threadpool(habillage.apercu, ctx.conn, k, ctx.mosque, brut.get("options") or {})
    except (ValueError, RuntimeError) as e:
        raise Invalid(habillage.sans_chemin(str(e))) from e
    return _ok(_("Preview up to date."), **_vue(ctx, k))


@router.post("/sermons/{kid}/habillage")
async def habillage_lancer(kid: int, ctx: CtxDep):
    brut = await ctx.raw_body()
    ctx.require("assign")
    k = khutba_or_404(ctx, kid)
    try:
        habillage.lancer(
            ctx.conn,
            ctx.request.app.state.db_path,
            k,
            ctx.mosque,
            brut.get("options") or {},
            ctx.user,
            ctx.ip,
            background=ctx.request.app.state.regen_background,
        )
    except ValueError as e:
        raise Invalid(str(e)) from e
    return _ok(
        _("Rendering started: the dressed video will be ready in a few minutes, this page follows the progress."),
        **_vue(ctx, k),
    )


@router.get("/sermons/{kid}/habillage/apercu.jpg")
def habillage_apercu_image(kid: int, ctx: CtxDep):
    f = habillage.fichier_apercu(khutba_or_404(ctx, kid))
    if not f.exists():
        raise NotFound()
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})


@router.get("/sermons/{kid}/habillage/video.mp4")
def habillage_video(kid: int, ctx: CtxDep, download: bool = False):
    k = khutba_or_404(ctx, kid)
    h = habillage.ligne(ctx.conn, kid)
    f = habillage.fichier_video(k)
    if not h or h["status"] != "pret" or not f.exists():
        raise NotFound()
    if download:
        return FileResponse(f, media_type="video/mp4", filename=habillage.nom_telechargement(k))
    return FileResponse(f, media_type="video/mp4")
