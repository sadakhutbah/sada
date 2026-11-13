"""Habillage d'une vidéo de session : réglages, aperçu, rendu en fond, fichiers servis par identifiant.

Lecture : quiconque voit la session. Écriture (aperçu, rendu) : le droit `assign`, celui de l'import
de vidéo. Aucun chemin de la machine dans les réponses : les fichiers passent par les adresses ci-dessous.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from ...i18n import _
from .. import habillage, traduction
from ..context import CtxDep, Invalid, NotFound, khutba_or_404
from .common import _ok

router = APIRouter()


def _vue(ctx, k: dict) -> dict:
    return {**habillage.etat(ctx.conn, k, ctx.mosque), "can_edit": ctx.can("assign")}


def _entetes_piste(info: dict, base: dict | None = None) -> dict:
    """État de la piste dans les en-têtes : le fichier partiel reste lisible, l'écran sait où il en est."""
    return {
        **(base or {}),
        "X-Sada-Subtitles-Complete": "1" if info.get("complete") else "0",
        "X-Sada-Subtitles-Done": str(info.get("done", 0)),
        "X-Sada-Subtitles-Total": str(info.get("total", 0)),
    }


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
    f = habillage.fichier_video(k)
    if not habillage.ligne(ctx.conn, kid) or not f.exists():
        raise NotFound()
    if download:
        return FileResponse(f, media_type="video/mp4", filename=habillage.nom_telechargement(k))
    return FileResponse(f, media_type="video/mp4")


@router.get("/sermons/{kid}/habillage/sous-titres")
def habillage_sous_titres_etat(kid: int, ctx: CtxDep):
    """Où en est chaque piste : complétude et état de la traduction de fond.

    Ne lance rien et ne bloque sur rien : c'est ce que l'écran interroge pendant une traduction.
    """
    k = khutba_or_404(ctx, kid)
    plan = habillage.langues(ctx.conn, k)
    return {"languages": plan["languages"], "translation": traduction.etat(k)}


@router.post("/sermons/{kid}/habillage/sous-titres")
def habillage_sous_titres_traduire(kid: int, ctx: CtxDep):
    """Traduit en fond les passages manquants des pistes (même modèle, même glossaire, caches)."""
    ctx.require("assign")
    k = khutba_or_404(ctx, kid)
    try:
        traduction.lancer(
            ctx.conn,
            ctx.request.app.state.db_path,
            k,
            ctx.user,
            ctx.ip,
            background=ctx.request.app.state.regen_background,
        )
    except ValueError as e:
        raise Invalid(str(e)) from e
    return _ok(_("Translation started: the track will be complete in a moment."), **_vue(ctx, k))


@router.get("/sermons/{kid}/habillage/sous-titres/{langue}.srt")
def habillage_sous_titres(kid: int, langue: str, ctx: CtxDep, brut: bool = False):
    """Le .srt d'une langue, aligné sur la vidéo habillée (`brut=1` : sur la vidéo d'origine).

    Toujours servi en pièce jointe : c'est un fichier à déposer dans YouTube Studio, pas une page.
    Une piste encore en traduction est servie telle qu'elle est (les passages connus), avec son état
    dans les en-têtes `X-Sada-Subtitles-*` : la requête n'attend jamais le modèle."""
    k = khutba_or_404(ctx, kid)
    if not habillage.version_habillee(ctx.conn, k):
        raise NotFound()
    plan = habillage.langues(ctx.conn, k)
    langue = langue.strip().lower()
    try:
        nom, contenu = habillage.sous_titres_srt(ctx.conn, k, langue, brut=brut, passages=plan["passages"])
    except ValueError as e:
        raise Invalid(str(e)) from e
    return Response(
        contenu,
        media_type="application/x-subrip; charset=utf-8",
        headers=_entetes_piste(
            plan["languages"].get(langue) or {},
            {"Content-Disposition": f'attachment; filename="{nom}"', "Cache-Control": "private, no-cache"},
        ),
    )


@router.get("/sermons/{kid}/habillage/sous-titres/{langue}.vtt")
def habillage_sous_titres_vtt(kid: int, langue: str, ctx: CtxDep, brut: bool = False):
    """Le WebVTT d'une langue, servi en ligne pour le `<track>` du lecteur.

    `brut=1` l'aligne sur la vidéo d'origine, sinon sur la vidéo habillée (carton d'ouverture compris).
    Aucune vidéo habillée n'est exigée : dès que la transcription existe, le lecteur a ses sous-titres ;
    sans transcription, l'adresse n'existe pas (404)."""
    k = khutba_or_404(ctx, kid)
    plan = habillage.langues(ctx.conn, k)
    if not plan["passages"]:
        raise NotFound()
    langue = langue.strip().lower()
    try:
        contenu = habillage.sous_titres_vtt(ctx.conn, k, langue, brut=brut, passages=plan["passages"])
    except ValueError as e:
        raise Invalid(str(e)) from e
    return Response(
        contenu,
        media_type="text/vtt; charset=utf-8",
        headers=_entetes_piste(
            plan["languages"].get(langue) or {}, {"Content-Disposition": "inline", "Cache-Control": "private, no-cache"}
        ),
    )
