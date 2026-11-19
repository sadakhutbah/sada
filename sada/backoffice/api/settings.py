"""Mosque, live, screens, notifications and logo settings.

The screen shows one tab per section the server announces: the same tabs for every account, no
branch by role. What belongs to the installation rather than to a mosque (replays bucket, folders
of the machine, public address, model, transcription, orchestrator) lives in `platform.py`, behind
the operator's screen.
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from sqlalchemy import update

from ... import notify
from ...i18n import _
from ...pipeline import settings as pipeline_settings
from .. import (
    auth,
    branding,
    db,
    destinations,
    instance,
    live_input,
    orm,
    publish,
    tarawih,
    tenant_config,
    variables_ecran,
)
from .. import gabarit as gabarit_mod
from .. import screens as screens_mod
from ..context import CtxDep, Invalid, NotFound
from ..models import Mosque, Notification
from .common import _mosque, _notification_settings, _ok

router = APIRouter()

SECTIONS = (
    "mosque",
    "logo",
    "screens",
    "live",
    "processing",
    "tarawih",
)

#: Fields the mosque's own form carries: nothing else is written through it.
MOSQUE_FIELDS = ("name", "timezone", "mawaqit_url")
#: Settings of the installation (operator's screen). Sent to the mosque form, they are refused.
PLATFORM_FIELDS = (
    "orchestrator_url",
    "orchestrator_token",
    "state_dir",
    "output_dir",
    "publish_dir",
    "public_url",
)


def _host(ctx) -> str:
    """Host the browser reached us on: the address to give OBS, so it works over the network too."""
    return ctx.request.url.hostname or "127.0.0.1"


def _seed(mosque: dict, name: str) -> None:
    """First save of a mosque: start from the shared file, so the keys the form does not show
    (API key, arabic_retry) are kept rather than dropped."""
    own = tenant_config.write_path(mosque, name)
    src = tenant_config.read_path(mosque, name)
    if own != src and src.exists():
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_bytes(src.read_bytes())
        own.chmod(src.stat().st_mode & 0o777)


def _processing_view(mosque: dict) -> dict:
    """Traitement d'après-direct: what is saved, the bounds, and what the pipeline would use."""
    path = tenant_config.read_path(mosque, "pipeline")
    saved = pipeline_settings.config(path).get("clips")
    return {
        "clips": pipeline_settings.borne(saved if saved not in (None, "") else pipeline_settings.DEFAULT_CLIPS),
        "enabled": pipeline_settings.clips_enabled(path),
        "effective": pipeline_settings.clip_count(path=path),
        "default": pipeline_settings.DEFAULT_CLIPS,
        "min": pipeline_settings.MIN_CLIPS,
        "max": pipeline_settings.MAX_CLIPS,
    }


@router.get("/settings")
def settings(ctx: CtxDep):
    """Everything the mosque's Settings screen shows: the interface splits it into tabs itself."""
    ctx.require("admin")
    m = _mosque(ctx)
    m.update({k: ctx.mosque.get(k) or "" for k in MOSQUE_FIELDS})
    m["has_logo"] = bool((ctx.mosque.get("logo_path") or "").strip())
    # L'adresse affichée doit être celle par laquelle la mosquée nous atteint : réglage s'il existe,
    # sinon celle de la requête (juste en local, fausse derrière un proxy).
    base = instance.base(str(ctx.request.base_url))
    return {
        "mosque": m,
        "sections": list(SECTIONS),
        "guide": {
            "url": base + "/docs/agent-gabarit-ecrans.md",
            "human": base + "/docs/ecrans-mode-emploi",
            "docs": base + "/docs",
            "url_en": base + "/docs/en/screen-template-prompt.md",
            "human_en": base + "/docs/en/screens",
            "docs_en": base + "/docs?langue=en",
        },
        "imams": auth.imams(ctx.conn, ctx.mosque["id"]),
        "connectors": publish.connector_rows(ctx.conn, ctx.mosque),
        "targets": destinations.rows(ctx.conn, ctx.mosque),
        "input": live_input.view(ctx.conn, ctx.mosque, _host(ctx)),
        "screens": screens_mod.view(ctx.conn, ctx.mosque),
        # Lignes arabes du gabarit : saisies par la mosquée, jamais fournies par le code.
        "screens_variables": variables_ecran.lire(ctx.conn, ctx.mosque["id"]),
        "gabarit": gabarit_mod.view(ctx.mosque),
        "processing": _processing_view(ctx.mosque),
        "tarawih": tarawih.view(ctx.conn, ctx.mosque["id"]),
    }


@router.patch("/settings/mosque")
async def mosque_save(ctx: CtxDep):
    """The mosque's own form: its name, its time zone, its Mawaqit address.

    The installation's settings are not fields of this route: a request that carries one gets a
    readable refusal instead of a silent write, whatever the account.
    """
    f = await ctx.body()
    ctx.require("admin")
    refuse = [k for k in PLATFORM_FIELDS if k in f]
    if refuse:
        raise Invalid(_("These settings belong to the platform screen: {fields}.", fields=", ".join(refuse)))
    vals = {
        **{k: ctx.mosque.get(k) or "" for k in MOSQUE_FIELDS},
        **{k: (f.get(k) or "").strip() for k in MOSQUE_FIELDS if k in f},
    }
    if not vals["name"]:
        raise Invalid(_("The name is required."))
    di = auth.imam_id(ctx.conn, ctx.mosque["id"], f.get("default_imam_id", ctx.mosque["default_imam_id"]))
    auto = f.get("auto_import")
    auto_import = (
        (ctx.mosque.get("auto_import") or 0) if auto in ("", None) else (1 if str(auto) in ("1", "true", "on") else 0)
    )
    db.update_mosque(ctx.conn, ctx.mosque["id"], **vals, default_imam_id=di, auto_import=auto_import)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "mosquee.configuree",
        "mosquee",
        ctx.mosque["id"],
        ip=ctx.ip,
        champs=[k for k in vals if str(vals[k]) != str(ctx.mosque.get(k))],
    )
    return _ok(_("Configuration saved."))


@router.put("/settings/processing")
async def processing_save(ctx: CtxDep):
    """Nombre d'extraits par khutba: écrit dans le fichier que le pipeline lit à son démarrage."""
    f = await ctx.body()  # body() flattens every value to text
    ctx.require("admin")
    actif = str(f.get("enabled", "1")).strip().lower() not in ("0", "off", "false", "non", "")
    try:
        _seed(ctx.mosque, "pipeline")
        garde = pipeline_settings.save_config(
            f.get("clips"), enabled=actif, path=tenant_config.write_path(ctx.mosque, "pipeline")
        )
    except (TypeError, ValueError) as e:
        raise Invalid(
            _(
                "Invalid clip count (from {min_clips} to {max_clips}).",
                min_clips=pipeline_settings.MIN_CLIPS,
                max_clips=pipeline_settings.MAX_CLIPS,
            )
        ) from e
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "traitement.configure",
        "mosque",
        ctx.mosque["id"],
        ip=ctx.ip,
        extraits=garde["clips"],
        actif=garde["enabled"],
    )
    return _ok(_("Processing saved: the pipeline applies it at the next run."), **garde)


@router.put("/settings/tarawih")
async def tarawih_save(ctx: CtxDep):
    """Tarawih broadcast: off, audio (default) or video, the latter only with an explicit confirmation.
    Raw body: the roster of reciters and the per-night overrides are lists."""
    f = await ctx.raw_body()
    ctx.require("admin")
    try:
        vue = tarawih.save(ctx.conn, ctx.mosque, f, ctx.user, ip=ctx.ip)
    except tarawih.InvalidTarawih as e:
        raise Invalid(str(e)) from e
    return _ok(_("Tarawih saved: the engine applies it from the next night."), tarawih=vue)


@router.put("/settings/live")
async def live_save(ctx: CtxDep):
    """Live destinations: the whole list at once, keys masked as everywhere else."""
    f = await ctx.raw_body()  # a list of destinations, not one string per field
    ctx.require("admin")
    try:
        rows = destinations.save(ctx.conn, ctx.mosque, f.get("targets"), ctx.user)
    except destinations.InvalidTarget as e:
        raise Invalid(str(e)) from e
    return _ok(_("Live destinations saved. They are picked up at the next live start."), targets=rows)


@router.put("/settings/input")
async def input_save(ctx: CtxDep):
    """Video input: a camera address, or "a computer pushes its picture" (relay)."""
    f = await ctx.body()
    ctx.require("admin")
    try:
        live_input.save(ctx.conn, ctx.mosque, f.get("mode", ""), f.get("url", ""), ctx.user)
    except live_input.InvalidInput as e:
        raise Invalid(str(e)) from e
    return _ok(
        _("Video input saved. The engine reads it at the next live start."),
        **{"input": live_input.view(ctx.conn, ctx.mosque, _host(ctx))},
    )


@router.put("/settings/onboarding")
async def onboarding_done(ctx: CtxDep):
    """The three-step wizard was completed or skipped: it is not offered again."""
    f = await ctx.body()
    ctx.require("admin")
    done = str(f.get("done", "1")).strip().lower() not in ("0", "false", "off", "non")
    ctx.conn.execute(
        update(Mosque).where(Mosque.id == ctx.mosque["id"]).values(onboarded_at=db.now_iso() if done else "")
    )
    orm.done(ctx.conn)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "demarrage.termine" if done else "demarrage.rouvert",
        "mosque",
        ctx.mosque["id"],
        ip=ctx.ip,
    )
    return _ok(_("Onboarding saved."), needs_onboarding=not done)


@router.put("/settings/screens")
async def screens_save(ctx: CtxDep):
    """Texts of the live screens (slates): the whole set at once, empty field = default."""
    f = await ctx.raw_body()  # nested objects: body() would flatten them to ""
    ctx.require("admin")
    try:
        vue = screens_mod.save(ctx.conn, ctx.mosque, f.get("screens", f), ctx.user["id"], ctx.ip)
    except screens_mod.InvalidScreens as e:
        raise Invalid(str(e)) from e
    return _ok(_("Screen texts saved. The engine picks them up at the next live start."), screens=vue)


@router.put("/settings/screens/variables")
async def screens_variables_save(ctx: CtxDep):
    """Textes communs du gabarit : les lignes arabes saisies par la mosquée.

    Un champ vide efface la ligne ; le gabarit n'affiche alors rien. Aucun texte arabe n'est
    proposé par défaut.
    """
    f = await ctx.raw_body()
    ctx.require("admin")
    try:
        v = variables_ecran.enregistrer(ctx.conn, ctx.mosque["id"], f.get("variables", f), ctx.user["id"], ctx.ip)
    except variables_ecran.InvalidVariables as e:
        raise Invalid(str(e)) from e
    return _ok(
        _("Shared texts and Arabic saved. The template picks them up at the next live start."), screens_variables=v
    )


@router.post("/settings/screens/import")
async def screens_import(ctx: CtxDep):
    """Imports the `ecrans.html` template made by an assistant: checks it, keeps it, applies its texts.

    The template is a visual reference and the source of the texts; the live keeps the engine's
    rendering. A refused file changes nothing.
    """
    ctx.require("admin")
    await ctx.body()  # CSRF token checked; the upload is kept in `ctx.files`
    up = ctx.files.get("file")
    if up is None or not getattr(up, "filename", ""):
        raise Invalid(_("No file chosen."))
    donnees = await up.read()
    try:
        res = screens_mod.import_gabarit(ctx.conn, ctx.mosque, up.filename, donnees, ctx.user["id"], ctx.ip)
    except (screens_mod.InvalidScreens, gabarit_mod.GabaritRefused) as e:
        raise Invalid(str(e)) from e
    return _ok(res.pop("message"), **res)


@router.post("/settings/screens/preview")
async def screens_preview(ctx: CtxDep):
    """PNG of one screen drawn by the engine, with the texts currently typed (before saving)."""
    f = await ctx.raw_body()
    ctx.require("admin")
    try:
        png = screens_mod.preview(ctx.mosque, f.get("screens", {}), str(f.get("screen", "")), conn=ctx.conn)
    except screens_mod.InvalidScreens as e:
        raise Invalid(str(e)) from e
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/settings/input/key")
async def input_key(ctx: CtxDep):
    """New stream key for OBS, shown this once (like Twitch): asking again issues another one
    and the previous key stops working."""
    await ctx.body()
    ctx.require("admin")
    from .. import internal

    key = internal.new_publish_key(ctx.conn, ctx.mosque["id"])
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "cle_diffusion.emise", "mosquee", ctx.mosque["id"])
    s = live_input.slug(ctx.mosque)
    return {
        "key": key,
        # OBS "Custom": server + stream key fields (MediaMTX reads user/pass from the query)
        "obs_server": live_input.push_rtmp(_host(ctx), ctx.mosque).rsplit("/", 1)[0],
        "obs_key": f"{s}?user={s}&pass={key}",
    }


@router.post("/settings/input/test")
async def input_test(ctx: CtxDep):
    """Checks the input right now: what the picture looks like, or why nothing arrives."""
    f = await ctx.body()
    ctx.require("admin")
    mode, url, err = live_input.check(f.get("mode", ""), f.get("url", ""), ctx.mosque)
    if err:
        raise Invalid(err)
    ok, detail = live_input.probe(url)
    return {"ok": ok, "detail": detail, "url": url}


@router.post("/settings/connectors/{key}")
async def connector_save(key: str, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    if key not in publish.CONNECTORS:
        raise NotFound()
    enabled = str(f.get("enabled", "0")) in ("1", "true", "on", "oui")
    publish.save_connector(ctx.conn, ctx.mosque["id"], key, enabled, f, ctx.user, ctx.ip)
    return _ok(_("Connector “{name}” saved.", name=publish.CONNECTORS[key].label))


@router.put("/settings/notifications")
async def notifications_save(ctx: CtxDep):
    """Save the notification destination. A secret left empty or masked keeps its stored value."""
    f = await ctx.body()
    ctx.require("admin")
    mode = (f.get("mode") or "").strip()
    if mode and mode not in notify.CHANNELS:
        raise Invalid(_("Unknown notification channel."))
    old = orm.first(ctx.conn, Notification, mosque_id=ctx.mosque["id"]) or {}

    def val(name: str, secret: bool) -> str:
        v = (f.get(name) or "").strip()
        return old.get(name, "") if secret and v in ("", db.MASK) else v

    webhook, token, chat = val("webhook", True), val("telegram_token", True), val("telegram_chat", False)
    if webhook and not re.match(r"^https?://", webhook):
        raise Invalid(_("The webhook address must start with http:// or https://."))
    channel = notify.CHANNELS.get(mode)
    if channel and not channel.is_configured({"webhook": webhook, "telegram_token": token, "telegram_chat": chat}):
        raise Invalid(channel.missing_message())
    orm.upsert(
        ctx.conn,
        Notification,
        ("mosque_id",),
        mosque_id=ctx.mosque["id"],
        mode=mode,
        webhook=webhook,
        telegram_token=token,
        telegram_chat=chat,
        updated_at=db.now_iso(),
    )
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "notifications.configure",
        "mosque",
        ctx.mosque["id"],
        ip=ctx.ip,
        channel=mode or "none",
    )
    return _ok(
        _("Notifications saved. The orchestrator applies them at its next start."),
        notifications=_notification_settings(ctx),
    )


@router.post("/settings/notifications/test")
def notifications_test(ctx: CtxDep):
    """Send a test message to the saved destination (explicit admin action)."""
    ctx.need_csrf()
    ctx.require("admin")
    r = orm.first(ctx.conn, Notification, mosque_id=ctx.mosque["id"])
    n = notify.from_settings(r, log=lambda _m: None)
    if not n.enabled:
        return JSONResponse({"ok": False, "message": _("No destination saved: nothing was sent.")}, status_code=422)
    if not n.send(_("Notification test - {mosque} (back-office).", mosque=ctx.mosque["name"])):
        return JSONResponse(
            {"ok": False, "message": _("Could not send to {destination}.", destination=n.destination)}, status_code=422
        )
    return {"ok": True, "message": _("Test message sent ({destination}).", destination=n.destination)}


@router.post("/settings/logo")
async def logo_save(ctx: CtxDep):
    ctx.require("admin")
    await ctx.body()  # CSRF token checked; the file is kept in memory (2 MB max)
    up = ctx.files.get("logo")
    if up is None or not getattr(up, "filename", ""):
        raise Invalid(_("No file chosen."))
    donnees = await up.read()
    rel = branding.save(ctx.mosque, up.filename, donnees)
    db.update_mosque(ctx.conn, ctx.mosque["id"], logo_path=rel)
    db.audit(
        ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.logo", "mosque", ctx.mosque["id"], ip=ctx.ip, fichier=rel
    )
    return _ok(_("Logo saved."), logo=branding.url({**ctx.mosque, "logo_path": rel}))


@router.delete("/settings/logo")
def logo_clear(ctx: CtxDep):
    ctx.need_csrf()
    ctx.require("admin")
    branding.clear(ctx.mosque)
    db.update_mosque(ctx.conn, ctx.mosque["id"], logo_path="")
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "mosquee.logo.retire", "mosque", ctx.mosque["id"], ip=ctx.ip)
    return _ok(_("Logo removed: the neutral emblem is back."))
