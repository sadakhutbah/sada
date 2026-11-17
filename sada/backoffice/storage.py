"""Où partir les rediffusions : un compartiment S3 (AWS) ou compatible (Cloudflare R2, MinIO, Backblaze).

Rien n'est stocké dans le dépôt : l'adresse, le compartiment et les clés se saisissent dans
le back-office ; la clé secrète ne revient jamais en clair (masque, renvoyer le masque = inchangé).
Envoi en HTTPS signé (SigV4) par la bibliothèque aws (boto3), qui gère aussi le découpage des gros
fichiers et les reprises.

Hosted mode (`SADA_MODE=hosted`): the bucket belongs to the platform, not to the mosque. There is
one bucket for the whole hosting, held in `data/instance.json`; each mosque writes under
`<prefix><slug>/`, so the recordings stay apart without a bucket per mosque.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..i18n import _
from . import auth, db, instance, orm
from .models import Mosque, Storage

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION_DEFAULT = "auto"
PREFIX_DEFAULT = "rediffusions/"
PREFIX_MAX = 120


class InvalidStorage(Exception):
    def __init__(self, erreurs: dict[str, str]):
        super().__init__(_("Bucket configuration refused."))
        self.erreurs = erreurs


def _net(v: str) -> str:
    return (v or "").strip()


def check(endpoint: str, bucket: str, region: str, prefix: str, access_key: str) -> dict[str, str]:
    """Valeurs nettoyées ; lève InvalidStorage avec un message par champ fautif."""
    e, b = _net(endpoint), _net(bucket)
    r = _net(region) or REGION_DEFAULT
    p = _net(prefix) or PREFIX_DEFAULT
    k = _net(access_key)
    err: dict[str, str] = {}

    if e:
        u = urlsplit(e)
        if u.scheme not in ("https", "http") or not u.netloc:
            err["endpoint"] = _("Address must start with https:// (the service's address, not the bucket's).")
        elif u.path not in ("", "/"):
            err["endpoint"] = _("Remove the path: only the host counts (https://<account>.r2.cloudflarestorage.com).")
        for interdit in (" ", "\t"):
            if interdit in e:
                err["endpoint"] = _("The address must not contain a space.")
    if not b:
        err["bucket"] = _("Bucket name required.")
    elif not BUCKET_RE.match(b) or ".." in b:
        err["bucket"] = _("Invalid name: lowercase letters, digits, dots and hyphens (3 to 63 characters).")
    if len(p) > PREFIX_MAX:
        err["prefix"] = _("Folder too long ({max} characters at most).", max=PREFIX_MAX)
    if not k:
        err["access_key"] = _("Access key required.")
    if err:
        raise InvalidStorage(err)
    return {
        "endpoint": e.rstrip("/"),
        "region": r,
        "bucket": b,
        "prefix": (p if p.endswith("/") else p + "/"),
        "access_key": k,
    }


def platform_view() -> dict:
    """The platform's bucket as the screen shows it: the secret key stays masked."""
    r = instance.platform_storage()
    secret = str(r.get("secret_key") or "")
    return {
        # One bucket for every mosque: the screen adds that the folder is completed by the slug.
        "scope": "platform",
        "endpoint": str(r.get("endpoint") or ""),
        "region": str(r.get("region") or REGION_DEFAULT),
        "bucket": str(r.get("bucket") or ""),
        "access_key": str(r.get("access_key") or ""),
        "secret_key": db.MASK if secret else "",
        "prefix": str(r.get("prefix") or PREFIX_DEFAULT),
        "enabled": bool(r.get("enabled")),
        "configured": bool(r.get("bucket") and r.get("access_key") and secret),
    }


def view(conn, mosque_id: int) -> dict:
    """Ce que montre l'écran : la clé secrète est masquée."""
    if auth.hosted():
        return platform_view()
    r = orm.first(conn, Storage, mosque_id=mosque_id) or {}
    secret = r.get("secret_key") or ""
    return {
        "scope": "mosque",
        "endpoint": str(r.get("endpoint") or ""),
        "region": str(r.get("region") or REGION_DEFAULT),
        "bucket": str(r.get("bucket") or ""),
        "access_key": str(r.get("access_key") or ""),
        "secret_key": db.MASK if secret else "",
        "prefix": str(r.get("prefix") or PREFIX_DEFAULT),
        "enabled": bool(r.get("enabled")),
        "configured": bool(r.get("bucket") and r.get("access_key") and secret),
    }


def _cleaned(payload: dict, before: dict) -> tuple[dict, str, int]:
    """Fields checked, secret kept when it comes back masked or empty, and the on/off switch."""
    propre = check(
        payload.get("endpoint", ""),
        payload.get("bucket", ""),
        payload.get("region", ""),
        payload.get("prefix", ""),
        payload.get("access_key", ""),
    )
    secret = _net(payload.get("secret_key", ""))
    if secret in ("", db.MASK):
        secret = str(before.get("secret_key") or "")
    if not secret:
        raise InvalidStorage({"secret_key": _("Secret key required.")})
    # Opt-in: without an explicit "on", the replays stay on the machine.
    actif = str(payload.get("enabled", "0")).strip().lower() in ("1", "true", "on", "oui", "yes")
    return propre, secret, 1 if actif else 0


def save(conn, mosque_id: int, payload: dict) -> dict:
    """Enregistre ; un champ secret renvoyé sous sa forme masquée garde la valeur précédente."""
    if auth.hosted():
        return platform_save(payload)
    avant = orm.first(conn, Storage, mosque_id=mosque_id) or {}
    propre, secret, actif = _cleaned(payload, avant)
    orm.upsert(
        conn,
        Storage,
        ("mosque_id",),
        mosque_id=mosque_id,
        endpoint=propre["endpoint"],
        region=propre["region"],
        bucket=propre["bucket"],
        access_key=propre["access_key"],
        secret_key=secret,
        prefix=propre["prefix"],
        enabled=actif,
        updated_at=db.now_iso(),
    )
    conn.commit()
    return view(conn, mosque_id)


def platform_save(payload: dict) -> dict:
    """Hosted: the one bucket of the platform, stored with the installation (no mosque row)."""
    propre, secret, actif = _cleaned(payload, instance.platform_storage())
    instance.save_platform_storage({**propre, "secret_key": secret, "enabled": actif})
    return platform_view()


def platform_effective(conn, mosque_id: int) -> dict | None:
    """Hosted: the platform bucket, with the mosque's slug as folder (`<prefix><slug>/`)."""
    r = instance.platform_storage()
    if not (r.get("enabled") and r.get("bucket") and r.get("access_key") and r.get("secret_key")):
        return None
    prefix = str(r.get("prefix") or PREFIX_DEFAULT)
    prefix = prefix if prefix.endswith("/") else prefix + "/"
    slug = (orm.first(conn, Mosque, id=mosque_id) or {}).get("slug") or ""
    return {
        "endpoint": str(r.get("endpoint") or ""),
        "region": str(r.get("region") or REGION_DEFAULT),
        "bucket": r["bucket"],
        "access_key": r["access_key"],
        "secret_key": r["secret_key"],
        "prefix": f"{prefix}{slug}/" if slug else prefix,
    }


def effective(conn, mosque_id: int) -> dict | None:
    """Réglage complet, clés en clair : réservé au moteur, par l'endpoint interne."""
    if auth.hosted():
        return platform_effective(conn, mosque_id)
    r = orm.first(conn, Storage, mosque_id=mosque_id) or {}
    if not (r.get("enabled") and r.get("bucket") and r.get("access_key") and r.get("secret_key")):
        return None
    return {
        "endpoint": r.get("endpoint") or "",
        "region": r.get("region") or REGION_DEFAULT,
        "bucket": r["bucket"],
        "access_key": r["access_key"],
        "secret_key": r["secret_key"],
        "prefix": r.get("prefix") or PREFIX_DEFAULT,
    }


def probe(target: dict) -> tuple[bool, str]:
    """Dépose un objet minuscule puis le retire : dit si le compartiment accepte vraiment l'écriture."""
    from sada import upload

    try:
        client = upload.client(target)
        cle = target["prefix"] + "sada-test.txt"
        client.put_object(Bucket=target["bucket"], Key=cle, Body=b"sada\n", ContentType="text/plain")
        client.delete_object(Bucket=target["bucket"], Key=cle)
    except Exception as e:  # noqa: BLE001 — toute erreur se raconte
        return False, upload.human(e)
    return True, _('Write accepted in "{bucket}" ({prefix}).', bucket=target["bucket"], prefix=target["prefix"])
