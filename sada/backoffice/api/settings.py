"""Mosque, model, folders, connectors, notifications and logo settings."""

from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ... import notify
from ...i18n import _
from ...pipeline import llm
from ...pipeline import settings as pipeline_settings
from .. import auth, branding, db, destinations, instance, live_input, publish, storage, variables_ecran
from .. import dirs as dirs_mod
from .. import gabarit as gabarit_mod
from .. import screens as screens_mod
from ..context import CtxDep, Invalid, NotFound
from .common import _mosque, _notification_settings, _ok

router = APIRouter()

SECTIONS = ("mosque", "logo", "screens", "live", "replays", "folders", "model", "processing", "connectors", "access")


def _host(ctx) -> str:
    """Host the browser reached us on: the address to give OBS, so it works over the network too."""
    return ctx.request.url.hostname or "127.0.0.1"


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


def _processing_view() -> dict:
    """Traitement d'après-direct: what is saved, the bounds, and what the pipeline would use."""
    saved = pipeline_settings.config().get("clips")
    return {
        "clips": pipeline_settings.borne(saved if saved not in (None, "") else pipeline_settings.DEFAULT_CLIPS),
        "enabled": pipeline_settings.clips_enabled(),
        "effective": pipeline_settings.clip_count(),
        "default": pipeline_settings.DEFAULT_CLIPS,
        "min": pipeline_settings.MIN_CLIPS,
        "max": pipeline_settings.MAX_CLIPS,
    }


@router.get("/settings")
def settings(ctx: CtxDep):
    """Everything the Settings screen shows: the interface splits it into tabs itself."""
    ctx.require("admin")
    m = _mosque(ctx)
    m.update({k: ctx.mosque.get(k) or "" for k in db.MOSQUE_SETTINGS if k != "logo_path"})
    # a stored secret never comes back in clear: the mask returned as is means "unchanged"
    m["orchestrator_token"] = db.MASK if m["orchestrator_token"] else ""
    m["has_logo"] = bool((ctx.mosque.get("logo_path") or "").strip())
    # "default: data/state" — never a path of the machine.
    defauts = {k: dirs_mod.label(v) for k, v in db.mosque_dirs(ctx.mosque).items()}
    # L'adresse affichée doit être celle par laquelle la mosquée nous atteint : réglage s'il existe,
    # sinon celle de la requête (juste en local, fausse derrière un proxy).
    base = instance.base(str(ctx.request.base_url))
    return {
        "mosque": m,
        "defaults": defauts,
        "sections": SECTIONS,
        "guide": {
            "url": base + "/docs/agent-gabarit-ecrans.md",
            "human": base + "/docs/ecrans-mode-emploi",
            "docs": base + "/docs",
            "url_en": base + "/docs/en/screen-template-prompt.md",
            "human_en": base + "/docs/en/screens",
            "docs_en": base + "/docs?langue=en",
            "public_url": instance.public_url(),
        },
        "imams": auth.imams(ctx.conn, ctx.mosque["id"]),
        "connectors": publish.connector_rows(ctx.conn, ctx.mosque),
        "targets": destinations.rows(ctx.conn, ctx.mosque),
        "input": live_input.view(ctx.conn, ctx.mosque, _host(ctx)),
        "storage": storage.view(ctx.conn, ctx.mosque["id"]),
        "screens": screens_mod.view(ctx.conn, ctx.mosque),
        # Lignes arabes du gabarit : saisies par la mosquée, jamais fournies par le code.
        "screens_variables": variables_ecran.lire(ctx.conn, ctx.mosque["id"]),
        "gabarit": gabarit_mod.view(ctx.mosque),
        "model": _model_view(),
        "processing": _processing_view(),
    }


@router.put("/settings/instance")
async def settings_instance(ctx: CtxDep):
    """Adresse publique de cette installation : elle sert dans les liens de documentation affichés."""
    ctx.require("admin")
    corps = await ctx.raw_body()
    try:
        valeur = instance.enregistrer(str((corps or {}).get("public_url") or ""))
    except ValueError as e:
        raise Invalid(str(e)) from e
    return {"ok": True, "public_url": valeur, "docs": (valeur or str(ctx.request.base_url).rstrip("/")) + "/docs"}


@router.get("/folders")
def folders(ctx: CtxDep, path: str = ""):
    """Tree of the machine hosting the application, for the "Browse" button."""
    ctx.require("admin")
    try:
        return {"roots": dirs_mod.roots(), **dirs_mod.listing(path or None)}
    except ValueError as e:
        raise Invalid(str(e)) from e


@router.patch("/settings/mosque")
async def mosque_save(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
    vals = {k: (f.get(k) or "").strip() for k in db.MOSQUE_SETTINGS if k != "logo_path" and k in f}
    current = {k: ctx.mosque.get(k) or "" for k in db.MOSQUE_SETTINGS if k != "logo_path"}
    vals = {**current, **vals}  # a partial form does not empty the other settings
    if not vals["name"]:
        raise Invalid(_("The name is required."))
    if vals["orchestrator_url"] and not re.match(r"^https?://", vals["orchestrator_url"]):
        raise Invalid(_("The orchestrator address must start with http:// or https://."))
    if vals["orchestrator_token"] == db.MASK:
        vals["orchestrator_token"] = ctx.mosque["orchestrator_token"]
    for cle in ("state_dir", "output_dir", "publish_dir"):
        if vals[cle]:
            ok, pourquoi = dirs_mod.check(vals[cle], create_if_missing=True)
            if not ok:
                raise Invalid(_("Folder refused ({reason}).", reason=pourquoi))
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
        champs=[k for k in vals if k != "orchestrator_token" and str(vals[k]) != str(ctx.mosque.get(k))],
    )
    return _ok(_("Configuration saved."))


@router.put("/settings/model")
async def model_save(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("admin")
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
        ctx.mosque["id"],
        ctx.user["id"],
        "modele.configure",
        "mosque",
        ctx.mosque["id"],
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


@router.put("/settings/processing")
async def processing_save(ctx: CtxDep):
    """Nombre d'extraits par khutba: écrit dans le fichier que le pipeline lit à son démarrage."""
    f = await ctx.body()  # body() flattens every value to text
    ctx.require("admin")
    actif = str(f.get("enabled", "1")).strip().lower() not in ("0", "off", "false", "non", "")
    try:
        garde = pipeline_settings.save_config(f.get("clips"), enabled=actif)
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
        "UPDATE mosques SET onboarded_at = ? WHERE id = ?", (db.now_iso() if done else "", ctx.mosque["id"])
    )
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


@router.put("/settings/storage")
async def storage_save(ctx: CtxDep):
    """Replays bucket: where the recordings are sent after the live."""
    f = await ctx.body()
    ctx.require("admin")
    try:
        v = storage.save(ctx.conn, ctx.mosque["id"], f)
    except storage.InvalidStorage as e:
        raise Invalid(" ".join(e.erreurs.values())) from e
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "rediffusion.configure",
        "mosque",
        ctx.mosque["id"],
        ip=ctx.ip,
        compartiment=v["bucket"],
        dossier=v["prefix"],
        actif=v["enabled"],
    )
    return _ok(_("Replays bucket saved. The engine uses it from the next live."), storage=v)


@router.post("/settings/storage/test")
async def storage_test(ctx: CtxDep):
    """Writes then removes a tiny object: says whether the bucket really accepts uploads."""
    f = await ctx.body()
    ctx.require("admin")
    secret = str(f.get("secret_key", ""))
    if secret in ("", db.MASK):
        target_secret = storage.effective(ctx.conn, ctx.mosque["id"]) or {}
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
        png = screens_mod.preview(ctx.mosque, f.get("screens", {}), str(f.get("screen", "")))
    except screens_mod.InvalidScreens as e:
        raise Invalid(str(e)) from e
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


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
    return _ok(_("Connector “{name}” saved.", name=publish.CONNECTORS[key].label))


@router.put("/settings/notifications")
async def notifications_save(ctx: CtxDep):
    """Save the notification destination. A secret left empty or masked keeps its stored value."""
    f = await ctx.body()
    ctx.require("admin")
    mode = (f.get("mode") or "").strip()
    if mode not in ("", "webhook", "telegram"):
        raise Invalid(_("Unknown notification channel."))
    old = db.one(ctx.conn, "SELECT * FROM notifications WHERE mosque_id = ?", ctx.mosque["id"]) or {}

    def val(name: str, secret: bool) -> str:
        v = (f.get(name) or "").strip()
        return old.get(name, "") if secret and v in ("", db.MASK) else v

    webhook, token, chat = val("webhook", True), val("telegram_token", True), val("telegram_chat", False)
    if webhook and not re.match(r"^https?://", webhook):
        raise Invalid(_("The webhook address must start with http:// or https://."))
    if mode == "webhook" and not webhook:
        raise Invalid(_("Enter the webhook address."))
    if mode == "telegram" and not (token and chat):
        raise Invalid(_("Telegram requires the bot token and the chat identifier."))
    ctx.conn.execute(
        "INSERT INTO notifications (mosque_id, mode, webhook, telegram_token, telegram_chat, updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT (mosque_id) DO UPDATE SET mode = excluded.mode, "
        "webhook = excluded.webhook, telegram_token = excluded.telegram_token, "
        "telegram_chat = excluded.telegram_chat, updated_at = excluded.updated_at",
        (ctx.mosque["id"], mode, webhook, token, chat, db.now_iso()),
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
    r = db.one(ctx.conn, "SELECT * FROM notifications WHERE mosque_id = ?", ctx.mosque["id"])
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
