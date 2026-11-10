"""Publication of validated clips, behind a connector abstraction.

Absolute rule: nothing is published without human validation (status "validated"); the check is done here,
server side, whatever the caller.

Connectors:
  - local      : copies the clip and a JSON record into the mosque's publication folder (demo, archiving,
                 or a synchronised folder).
  - facebook   : video on the mosque's page (Graph API).
  - youtube    : upload to the mosque's channel (Data API, resumable, OAuth refresh token).
  - instagram  : Reel on the professional account, fetched by the API from a public address.

The three networks are real: they call the platform (`minbar.backoffice.social`) and publish nothing
without credentials. The publication is always launched by a person, on a validated clip.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..events import kind_label
from . import db, social


@dataclass
class Item:
    clip: dict
    khutba: dict
    mosque: dict
    caption: str
    validated_by: str
    published_by: str


@dataclass
class Result:
    ok: bool
    location: str = ""
    message: str = ""


class Connector:
    key = ""
    label = ""
    description = ""
    fields: tuple[str, ...] = ()         # settings entered by the administrator (stored in the local database, never in the code)
    required: tuple[str, ...] = ()       # among fields: credentials without which the connector is unavailable
    secret: tuple[str, ...] = ()         # among fields: hidden on display
    labels: dict[str, str] = {}          # label shown per field (default: its name)
    enabled_by_default = False           # enabled as long as the administrator has saved nothing
    implemented = True                   # real publication (the screen says so when it is not)

    def defaults(self, mosque: dict) -> dict:
        """Value used for a field left empty."""
        return {}

    def availability(self, row: dict | None) -> tuple[bool, str]:
        """(usable, reason) from the mosque's configuration."""
        settings = json.loads(row["settings"]) if row else {}
        missing = [f for f in self.required if not str(settings.get(f, "")).strip()]
        if missing:
            return False, "désactivé : identifiants absents (" + ", ".join(missing) + ")"
        if not (row["enabled"] if row else self.enabled_by_default):
            return False, "désactivé par l'administrateur"
        return True, ""

    def publish(self, item: Item, settings: dict) -> Result:   # pragma: no cover - interface
        raise NotImplementedError


class LocalFolder(Connector):
    key = "local"
    label = "Dossier local"
    description = "Copie le clip validé et sa fiche (légende, versets, validation) dans un dossier de la machine."
    fields = ("path",)
    labels = {"path": "Dossier"}
    enabled_by_default = True

    def defaults(self, mosque):
        """Saved setting (Settings pre-fills it), otherwise data/published/<mosque>."""
        return {"path": db.local_folder_default(mosque)}

    def publish(self, item: Item, settings: dict) -> Result:
        out = social.archive(item, settings.get("path") or db.mosque_dirs(item.mosque)["publish"])
        return Result(True, str(out), "copié dans le dossier de publication")


class FacebookPage(Connector):
    key, label = "facebook", "Page Facebook"
    description = "Publie le clip en vidéo sur la page de la mosquée (API Graph)."
    fields = required = ("page_id", "page_token")
    secret = ("page_token",)
    labels = {"page_id": "Identifiant de la page", "page_token": "Jeton d'accès de la page"}

    def publish(self, item: Item, settings: dict) -> Result:
        where = social.facebook(item, settings)
        return Result(True, where, "vidéo publiée sur la page")


class YouTube(Connector):
    key, label = "youtube", "YouTube"
    description = "Envoie le clip sur la chaîne de la mosquée (API YouTube Data, jeton d'actualisation OAuth)."
    fields = ("channel_id", "client_id", "client_secret", "refresh_token", "privacy_status", "category_id")
    required = ("client_id", "client_secret", "refresh_token")
    secret = ("client_secret", "refresh_token")
    labels = {"channel_id": "Identifiant de la chaîne", "client_id": "Identifiant client OAuth",
              "client_secret": "Secret client OAuth", "refresh_token": "Jeton d'actualisation",
              "privacy_status": "Visibilité (public, unlisted, private)", "category_id": "Catégorie (27 = éducation)"}

    def defaults(self, mosque):
        return {"privacy_status": "public", "category_id": "27"}

    def publish(self, item: Item, settings: dict) -> Result:
        where = social.youtube(item, settings)
        return Result(True, where, "vidéo publiée sur la chaîne")


class Instagram(Connector):
    key, label = "instagram", "Instagram"
    description = ("Publie le clip en Reel sur le compte professionnel ; l'API récupère la vidéo depuis"
                   " l'adresse publique du dossier de publication.")
    fields = ("ig_user_id", "access_token", "public_base_url", "folder")
    required = ("ig_user_id", "access_token", "public_base_url")
    secret = ("access_token",)
    labels = {"ig_user_id": "Identifiant du compte professionnel", "access_token": "Jeton d'accès",
              "public_base_url": "Adresse publique du dossier de publication",
              "folder": "Dossier de publication (archivage avant envoi)"}

    def defaults(self, mosque):
        return {"folder": db.local_folder_default(mosque)}

    def availability(self, row):
        ok, why = super().availability(row)
        if not ok:
            return ok, why
        settings = json.loads(row["settings"]) if row else {}
        base = str(settings.get("public_base_url") or "").strip()
        if not base.startswith(("http://", "https://")):
            return False, "désactivé : l'adresse publique du dossier doit être une URL http(s)"
        return True, ""

    def publish(self, item: Item, settings: dict) -> Result:
        where = social.instagram(item, settings)
        return Result(True, where, "Reel publié sur le compte")


CONNECTORS: dict[str, Connector] = {c.key: c for c in (LocalFolder(), FacebookPage(), YouTube(), Instagram())}


def _short(value):
    """An example path must not reveal the real location of the server."""
    if not isinstance(value, str) and value is not None:
        value = str(value)                      # db.local_folder_default returns a path
    if value and value.startswith("/"):
        from . import dirs

        return dirs.label(value)
    return value


def connector_rows(conn, mosque: dict) -> list[dict]:
    rows = {r["key"]: r for r in db.all_(conn, "SELECT * FROM connectors WHERE mosque_id = ?", mosque["id"])}
    out = []
    for key, c in CONNECTORS.items():
        r = rows.get(key)
        ok, why = c.availability(r)
        settings = json.loads(r["settings"]) if r else {}
        shown = {f: (db.MASK if f in c.secret and settings.get(f) else settings.get(f, "")) for f in c.fields}
        out.append({"key": key, "label": c.label, "description": c.description, "available": ok, "why": why,
                    "enabled": bool(r["enabled"]) if r else c.enabled_by_default,
                    "fields": [{"name": f, "label": c.labels.get(f, f), "value": shown[f], "secret": f in c.secret,
                                "placeholder": _short(c.defaults(mosque).get(f, ""))} for f in c.fields]})
    return out


def default_caption(khutba: dict, mosque: dict, verses: list[str]) -> str:
    kind = (khutba.get("kind") or "khutba")
    if kind != "khutba" and khutba.get("title"):
        first = f"{kind_label(kind)} : {khutba['title']} — {mosque['name']}"
    else:
        first = f"{mosque['clip_title']} — {mosque['name']}"
    parts = [first, f"{khutba['date'] or ''} · {khutba['label']}".strip(" ·")]
    if verses:
        parts.append("Versets : " + ", ".join(verses))
    parts.append("Extrait validé par la mosquée. Sous-titres générés automatiquement puis relus.")
    return "\n".join(parts)


def can_publish(clip: dict) -> tuple[bool, str]:
    """Absolute rule: only a clip validated by a human, in its validated version, gets published."""
    if clip["status"] not in ("valide", "publie") or clip["needs_regen"] or not clip["decided_by"]:
        return False, "clip non validé : publication refusée"
    return True, ""


def publish(conn, clip: dict, connector_key: str, user: dict, caption: str, ip=None) -> Result:
    """Publishes a VALIDATED clip. Every attempt (successful or refused) is logged."""
    c = CONNECTORS.get(connector_key)

    def record(status: str, res: Result) -> Result:
        conn.execute("INSERT INTO publications (mosque_id, clip_id, clip_version, connector, status, location, caption, message, "
                     "user_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (clip["mosque_id"], clip["id"], clip["version"], connector_key, status, res.location, caption, res.message,
                      user["id"], db.now_iso()))
        db.audit(conn, clip["mosque_id"], user["id"], f"publication.{status}", "clip", clip["id"], ip=ip,
                 connecteur=connector_key, version=clip["version"], message=res.message, emplacement=res.location or None)
        return res

    if c is None:
        return record("refuse", Result(False, "", "connecteur inconnu"))
    ok, why = can_publish(clip)
    if not ok:
        return record("refuse", Result(False, "", why))
    row = db.one(conn, "SELECT * FROM connectors WHERE mosque_id = ? AND key = ?", clip["mosque_id"], connector_key)
    ok, why = c.availability(row)
    if not ok:
        return record("refuse", Result(False, "", why))
    if not Path(clip["file_path"]).exists():
        return record("erreur", Result(False, "", "fichier du clip introuvable"))
    k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", clip["khutba_id"])
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", clip["mosque_id"])
    v = db.one(conn, "SELECT display_name FROM users WHERE id = ?", clip["decided_by"])
    item = Item(clip, k, m, caption.strip(), v["display_name"] if v else "?", user["display_name"])
    try:
        res = c.publish(item, json.loads(row["settings"]) if row else {})
    except Exception as e:  # noqa: BLE001
        res = Result(False, "", f"erreur : {e}")
    if res.ok:
        conn.execute("UPDATE clips SET status = 'publie', updated_at = ? WHERE id = ?", (db.now_iso(), clip["id"]))
        return record("publie", res)
    return record("erreur", res)


def save_connector(conn, mosque_id: int, key: str, enabled: bool, values: dict, user: dict, ip=None) -> None:
    c = CONNECTORS[key]
    row = db.one(conn, "SELECT * FROM connectors WHERE mosque_id = ? AND key = ?", mosque_id, key)
    settings = json.loads(row["settings"]) if row else {}
    for f in c.fields:
        v = (values.get(f) or "").strip()
        if f in c.secret and v in ("", db.MASK):
            continue                       # secret field left empty = unchanged
        settings[f] = v
    conn.execute("INSERT INTO connectors (mosque_id, key, enabled, settings) VALUES (?,?,?,?) ON CONFLICT (mosque_id, key) "
                 "DO UPDATE SET enabled = excluded.enabled, settings = excluded.settings",
                 (mosque_id, key, int(enabled), json.dumps(settings)))
    db.audit(conn, mosque_id, user["id"], "connecteur.configure", "connecteur", None, ip=ip, connecteur=key, actif=enabled,
             champs=sorted(f for f in settings if settings[f]))
