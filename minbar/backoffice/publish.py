"""Publication des clips validés, derrière une abstraction de connecteurs.

Règle absolue : rien ne se publie sans validation humaine (statut « validé ») ; la vérification est faite ici,
côté serveur, quel que soit l'appelant.

Connecteurs V1 :
  - local      : copie le clip et une fiche JSON dans le dossier de publication de la mosquée (démo, archivage,
                 ou dossier synchronisé). Fonctionnel.
  - facebook, youtube, instagram : interfaces prêtes, DÉSACTIVÉES. Sans identifiants : indisponibles. Même avec des
                 identifiants, la V1 n'implémente aucun appel externe (aucune publication réelle).
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import runs
from ..events import kind_label
from . import db


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
    fields: tuple[str, ...] = ()         # réglages saisis par l'administrateur (stockés en base locale, jamais dans le code)
    required: tuple[str, ...] = ()       # parmi fields : identifiants sans lesquels le connecteur est indisponible
    secret: tuple[str, ...] = ()         # parmi fields : masqués à l'affichage
    labels: dict[str, str] = {}          # libellé affiché par champ (défaut : son nom)
    enabled_by_default = False           # actif tant que l'administrateur n'a rien enregistré

    def defaults(self, mosque: dict) -> dict:
        """Valeur utilisée pour un champ laissé vide."""
        return {}

    def availability(self, row: dict | None) -> tuple[bool, str]:
        """(utilisable, raison) d'après la configuration de la mosquée."""
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
        """Réglage enregistré (les Réglages le préremplissent), sinon data/published/<mosquée>."""
        return {"path": db.local_folder_default(mosque)}

    def publish(self, item: Item, settings: dict) -> Result:
        k, c, m = item.khutba, item.clip, item.mosque
        dest = Path(settings.get("path") or db.mosque_dirs(m)["publish"]) / (k["date"] or "sans-date")
        dest.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{m['slug']}_{k['run_id']}_s{runs.session_number(k['session_index'])}_clip{c['idx']}_{c['kind']}_v{c['version']}")
        src = Path(c["file_path"])
        out = dest / f"{base}.mp4"
        shutil.copy2(src, out)
        meta = {
            "mosquee": m["name"], "khutba": k["label"], "date": k["date"], "run": k["run_id"],
            "clip": {"numero": c["idx"], "type": c["kind"], "version": c["version"], "debut_s": c["start"], "fin_s": c["end"],
                     "pourquoi": c["why"], "source": src.name},
            "legende": item.caption, "valide_par": item.validated_by, "publie_par": item.published_by,
            "publie_le": db.now_iso(), "musique": False, "sous_titres": "français seul",
        }
        (dest / f"{base}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return Result(True, str(out), "copié dans le dossier de publication")


class _Disabled(Connector):
    """Connecteur réseau non activé en V1 : ne fait AUCUN appel externe."""

    def availability(self, row):
        ok, why = super().availability(row)
        if not ok:
            return ok, why
        return False, "désactivé : publication réelle non implémentée en V1 (aucun appel externe)"

    def publish(self, item, settings):
        return Result(False, "", "connecteur désactivé en V1 : aucune publication réelle")


class FacebookPage(_Disabled):
    key, label = "facebook", "Page Facebook"
    description = "Vidéo (Reel) sur la page de la mosquée, via l'API Graph."
    fields = required = ("page_id", "page_token")
    secret = ("page_token",)


class YouTube(_Disabled):
    key, label = "youtube", "YouTube"
    description = "Short sur la chaîne de la mosquée, via l'API YouTube Data (OAuth)."
    fields = required = ("channel_id", "client_id", "client_secret", "refresh_token")
    secret = ("client_secret", "refresh_token")


class Instagram(_Disabled):
    key, label = "instagram", "Instagram"
    description = "Reel sur le compte professionnel lié à la page Facebook."
    fields = required = ("ig_user_id", "access_token")
    secret = ("access_token",)


CONNECTORS: dict[str, Connector] = {c.key: c for c in (LocalFolder(), FacebookPage(), YouTube(), Instagram())}


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
                                "placeholder": c.defaults(mosque).get(f, "")} for f in c.fields]})
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
    """Règle absolue : seul un clip validé par un humain, dans sa version validée, se publie."""
    if clip["status"] not in ("valide", "publie") or clip["needs_regen"] or not clip["decided_by"]:
        return False, "clip non validé : publication refusée"
    return True, ""


def publish(conn, clip: dict, connector_key: str, user: dict, caption: str, ip=None) -> Result:
    """Publie un clip VALIDÉ. Toute tentative (réussie ou refusée) est journalisée."""
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
            continue                       # champ secret laissé vide = inchangé
        settings[f] = v
    conn.execute("INSERT INTO connectors (mosque_id, key, enabled, settings) VALUES (?,?,?,?) ON CONFLICT (mosque_id, key) "
                 "DO UPDATE SET enabled = excluded.enabled, settings = excluded.settings",
                 (mosque_id, key, int(enabled), json.dumps(settings)))
    db.audit(conn, mosque_id, user["id"], "connecteur.configure", "connecteur", None, ip=ip, connecteur=key, actif=enabled,
             champs=sorted(f for f in settings if settings[f]))
