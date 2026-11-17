"""Platform settings: the installation the operator runs, never a mosque's screen.

Hosted mode (`SADA_MODE=hosted`): one machine, many mosques. The replays bucket, the folders of
the machine, the public address, the model and the transcription provider belong to whoever runs
the platform. No mosque administration receives them, and the server refuses every write
(`platform` permission) — the check lives here, not in a screen.

Self-hosted: the same screen is the installation's; the first administrator of an install is also
its platform administrator (`__main__.py`), so it stays reachable from the menu, with the
orchestrator of the single mosque on it.
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ...i18n import _
from ...pipeline import llm
from ...pipeline import remote as remote_transcription
from .. import auth, db, instance, orm, storage
from .. import dirs as dirs_mod
from ..context import Invalid, NotFound, PreCtxDep
from ..models import Mosque
from .common import _ok

router = APIRouter()

#: Tabs of the screen. `orchestrator` is added when this installation has no picker (one mosque).
SECTIONS = ("replays", "folders", "public_url", "model", "transcription")


def _require(ctx) -> None:
    """`platform` only: the operator of the hosting, or the administrator of a self-hosted install."""
    ctx.require(auth.PLATFORM)


def _installation_mosque(ctx) -> dict | None:
    """The mosque whose rows hold the installation's settings.

    The platform screen opens without a mosque: the session's mosque counts when there is one,
    otherwise the only mosque of the installation. None when there is nothing to write to.
    """
    if ctx.mosque:
        return ctx.mosque
    ids = list(ctx.conn.scalars(select(Mosque.id).limit(2)))
    return orm.first(ctx.conn, Mosque, id=ids[0]) if len(ids) == 1 else None


def _audit_mosque(ctx) -> int | None:
    """Where the journal line goes: the mosque of the setting, none when the platform has none."""
    mosque = _installation_mosque(ctx)
    return mosque["id"] if mosque else None


def _folders_view(mosque: dict | None) -> dict:
    """Roots chosen on the screen; an empty field means the program's own folder.

    The hint is deliberately generic: the screen never shows a path of the machine, only the word
    for the default the program applies (the folders live beside the code, wherever it is installed).
    """
    roots = instance.platform_folders() if auth.hosted() else {}
    m = mosque or {}
    return {
        key: {
            "value": roots.get(key, "") if auth.hosted() else str(m.get(f"{key}_dir") or ""),
            "default": _("the program's own folder"),
        }
        for key in instance.FOLDERS
    }


def _model_view() -> dict:
    """Model setting for the screen: never the API key, only its presence."""
    cfg = llm.config()
    eff = llm.settings()
    return {
        "enabled": llm.enabled(),
        "url": cfg.get("url", ""),
        "model": cfg.get("model", ""),
        "protocol": cfg.get("protocol", ""),
        "has_key": bool(cfg.get("key")),
        "effective": {"url": eff.url, "model": eff.model, "protocol": eff.protocol},
        "label": eff.label(),
    }


def _orchestrator(m: dict) -> dict:
    """Orchestrator connection of one mosque: the token never comes back in clear."""
    token = str(m.get("orchestrator_token") or "")
    return {
        "id": m["id"],
        "slug": m["slug"],
        "name": m["name"],
        "orchestrator_url": str(m.get("orchestrator_url") or ""),
        "orchestrator_token": db.MASK if token else "",
        "has_token": bool(token),
    }


def _mosques(ctx) -> list[dict]:
    """Every mosque of the installation, for the operator's per-mosque panel."""
    return [_orchestrator(m) for m in orm.rows(ctx.conn, select(Mosque).order_by(Mosque.name))]


@router.get("/platform/settings")
def platform_settings(ctx: PreCtxDep):
    """Everything the platform screen shows: the installation, never a mosque's own form."""
    _require(ctx)
    mosque = _installation_mosque(ctx)
    sections = list(SECTIONS) + ([] if auth.hosted() or mosque is None else ["orchestrator"])
    base = instance.base(str(ctx.request.base_url))
    return {
        "hosted": auth.hosted(),
        "sections": sections,
        "public_url": instance.public_url(),
        "docs": base + "/docs",
        "folders": _folders_view(mosque),
        "storage": storage.view(ctx.conn, mosque["id"] if mosque else 0),
        "model": _model_view(),
        "transcription": remote_transcription.view(),
        # The orchestrator lives on this page only when no picker offers the mosques.
        "mosque": _orchestrator(mosque) if mosque and "orchestrator" in sections else None,
    }


@router.get("/platform/mosques")
def platform_mosques(ctx: PreCtxDep):
    """The installation's mosques, with their orchestrator connection (token masked)."""
    _require(ctx)
    return {"mosques": _mosques(ctx)}


@router.put("/platform/mosques/{mid}/orchestrator")
async def orchestrator_save(mid: int, ctx: PreCtxDep):
    """Orchestrator address and token of one mosque, set by the operator from the mosque list."""
    _require(ctx)
    f = await ctx.body()
    m = orm.first(ctx.conn, Mosque, id=mid)
    if not m:
        raise NotFound()
    url = (f.get("url") or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        raise Invalid(_("The orchestrator address must start with http:// or https://."))
    token = str(f.get("token") or "").strip()
    if token == db.MASK:  # mask sent back as is: the stored token stays
        token = str(m.get("orchestrator_token") or "")
    db.update_mosque(ctx.conn, mid, orchestrator_url=url, orchestrator_token=token)
    db.audit(
        ctx.conn,
        mid,
        ctx.user["id"],
        "orchestrateur.configure",
        "mosquee",
        mid,
        ip=ctx.ip,
        url=url,
        jeton=bool(token),
    )
    return _ok(
        _("Orchestrator connection saved."),
        mosque=_orchestrator({**m, "orchestrator_url": url, "orchestrator_token": token}),
    )


@router.put("/platform/public-url")
async def public_url_save(ctx: PreCtxDep):
    """Public address of this installation: the links the interface shows are built from it."""
    _require(ctx)
    corps = await ctx.raw_body()
    try:
        valeur = instance.enregistrer(str((corps or {}).get("public_url") or ""))
    except ValueError as e:
        raise Invalid(str(e)) from e
    db.audit(ctx.conn, _audit_mosque(ctx), ctx.user["id"], "plateforme.adresse", ip=ctx.ip, adresse=valeur)
    return {"ok": True, "public_url": valeur, "docs": (valeur or str(ctx.request.base_url).rstrip("/")) + "/docs"}


@router.get("/platform/folders")
def folders(ctx: PreCtxDep, path: str = ""):
    """Tree of the machine hosting the application, for the "Browse" button."""
    _require(ctx)
    try:
        return {"roots": dirs_mod.roots(), **dirs_mod.listing(path or None)}
    except ValueError as e:
        raise Invalid(str(e)) from e


@router.put("/platform/folders")
async def folders_save(ctx: PreCtxDep):
    """Roots of the machine's folders: the platform's (hosted) or the single mosque's (self-hosted)."""
    _require(ctx)
    f = await ctx.body()
    valeurs = {}
    for key in instance.FOLDERS:
        valeur = (f.get(key) or "").strip()
        if valeur:
            ok, pourquoi = dirs_mod.check(valeur, create_if_missing=True)
            if not ok:
                raise Invalid(_("Folder refused ({reason}).", reason=pourquoi))
        valeurs[key] = valeur
    if auth.hosted():
        instance.save_platform_folders(valeurs)
    else:
        mosque = _installation_mosque(ctx)
        if mosque is None:
            raise Invalid(_("No mosque to configure on this installation."))
        db.update_mosque(
            ctx.conn,
            mosque["id"],
            state_dir=valeurs["state"],
            output_dir=valeurs["output"],
            publish_dir=valeurs["publish"],
        )
    db.audit(ctx.conn, _audit_mosque(ctx), ctx.user["id"], "plateforme.dossiers", ip=ctx.ip, **valeurs)
    return _ok(
        _("Folders saved. The pipeline reads them at its next run."), folders=_folders_view(_installation_mosque(ctx))
    )


@router.put("/platform/storage")
async def storage_save(ctx: PreCtxDep):
    """Replays bucket: where the recordings are sent after the live."""
    _require(ctx)
    f = await ctx.body()
    try:
        v = storage.save(ctx.conn, (_installation_mosque(ctx) or {}).get("id") or 0, f)
    except storage.InvalidStorage as e:
        raise Invalid(" ".join(e.erreurs.values())) from e
    db.audit(
        ctx.conn,
        _audit_mosque(ctx),
        ctx.user["id"],
        "rediffusion.configure",
        ip=ctx.ip,
        compartiment=v["bucket"],
        dossier=v["prefix"],
        actif=v["enabled"],
    )
    return _ok(_("Replays bucket saved. The engine uses it from the next live."), storage=v)


@router.post("/platform/storage/test")
async def storage_test(ctx: PreCtxDep):
    """Writes then removes a tiny object: says whether the bucket really accepts uploads."""
    _require(ctx)
    f = await ctx.body()
    mosque_id = (_installation_mosque(ctx) or {}).get("id") or 0
    secret = str(f.get("secret_key", ""))
    if secret in ("", db.MASK):
        target_secret = storage.effective(ctx.conn, mosque_id) or {}
        f = dict(f, secret_key=target_secret.get("secret_key", ""))
    try:
        propre = storage.check(
            f.get("endpoint", ""),
            f.get("bucket", ""),
            f.get("region", ""),
            f.get("prefix", ""),
            f.get("access_key", ""),
        )
    except storage.InvalidStorage as e:
        raise Invalid(" ".join(e.erreurs.values())) from e
    if not f.get("secret_key"):
        raise Invalid(_("Secret key required."))
    propre.update(secret_key=f["secret_key"], bucket=propre["bucket"])
    ok, detail = storage.probe(propre)
    return {"ok": ok, "detail": detail, "bucket": propre["bucket"], "prefix": propre["prefix"]}


@router.put("/platform/model")
async def model_save(ctx: PreCtxDep):
    """Language model shared by the whole installation (the pipeline's provider)."""
    _require(ctx)
    f = await ctx.body()
    url = (f.get("url") or "").strip()
    model = (f.get("model") or "").strip()
    proto = (f.get("protocol") or "").strip()
    if url and not re.match(r"^https?://", url):
        raise Invalid(_("The model address must start with http:// or https://."))
    if proto not in ("", "openai", "anthropic"):
        raise Invalid(_("Unknown API format."))
    enabled = str(f.get("enabled", "0")) in ("1", "true", "on", "oui")
    llm.save_config(enabled=enabled, url=url, model=model, protocol=proto)
    db.audit(
        ctx.conn,
        _audit_mosque(ctx),
        ctx.user["id"],
        "modele.configure",
        ip=ctx.ip,
        actif=enabled,
        url=url,
        modele=model,
        protocole=proto,
    )
    return _ok(
        _(
            "Language model saved. The pipeline applies it at the next run (environment variables "
            "still take precedence)."
        ),
        label=llm.settings().label(),
    )


@router.post("/platform/model/test")
def model_test(ctx: PreCtxDep):
    """Asks the saved model a tiny question: the provider's answer, or why it did not come."""
    _require(ctx)
    ok, msg = llm.probe()
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 422)


@router.put("/platform/transcription")
async def transcription_save(ctx: PreCtxDep):
    """Local or remote: writes the setting `sada.pipeline.remote` reads at every transcription."""
    _require(ctx)
    f = await ctx.body()
    mode = (f.get("mode") or "").strip().lower()
    if mode not in remote_transcription.MODES:
        raise Invalid(_("Unknown transcription mode."))
    provider = (f.get("provider") or remote_transcription.DEFAULT_PROVIDER).strip().lower()
    if provider not in remote_transcription.PROVIDERS and provider != remote_transcription.CUSTOM:
        raise Invalid(_("Unknown transcription provider."))
    url = (f.get("url") or "").strip()
    model = (f.get("model") or "").strip()
    if provider == remote_transcription.CUSTOM:
        if not re.match(r"^https?://", url):
            raise Invalid(_("The transcription address must start with http:// or https://."))
        if not model:
            raise Invalid(_("The model is required for a custom address."))
    key = str(f.get("key") or "").strip()
    if key == db.MASK:  # mask sent back as is: the stored key stays
        key = ""
    vue = remote_transcription.save_config(mode=mode, provider=provider, url=url, model=model, key=key)
    db.audit(
        ctx.conn,
        _audit_mosque(ctx),
        ctx.user["id"],
        "transcription.configure",
        ip=ctx.ip,
        methode=mode,
        fournisseur=provider,
        modele=vue["model"],
        cle=vue["has_key"],
    )
    return _ok(_("Transcription saved. The pipeline applies it at the next run."), transcription=vue)


@router.post("/platform/transcription/test")
def transcription_test(ctx: PreCtxDep):
    """Does the provider really answer? Sends a short generated sample (never the key back)."""
    _require(ctx)
    ok, msg = remote_transcription.probe()
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 422)
