"""Où partir les rediffusions : un compartiment S3 (AWS) ou compatible (Cloudflare R2, MinIO, Backblaze).

Rien n'est stocké dans le dépôt : l'adresse, le compartiment et les clés se saisissent dans
le back-office ; la clé secrète ne revient jamais en clair (masque, renvoyer le masque = inchangé).
Envoi en HTTPS signé (SigV4) par la bibliothèque aws (boto3), qui gère aussi le découpage des gros
fichiers et les reprises.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from . import db

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION_DEFAULT = "auto"
PREFIX_DEFAULT = "rediffusions/"
PREFIX_MAX = 120


class InvalidStorage(Exception):
    def __init__(self, erreurs: dict[str, str]):
        super().__init__("Configuration du compartiment refusée.")
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
            err["endpoint"] = "Adresse commençant par https:// (celle du service, pas du compartiment)."
        elif u.path not in ("", "/"):
            err["endpoint"] = "Retirez le chemin : seul l'hôte compte (https://<compte>.r2.cloudflarestorage.com)."
        for interdit in (" ", "\t"):
            if interdit in e:
                err["endpoint"] = "L'adresse ne doit pas contenir d'espace."
    if not b:
        err["bucket"] = "Nom du compartiment requis."
    elif not BUCKET_RE.match(b) or ".." in b:
        err["bucket"] = "Nom invalide : minuscules, chiffres, points et tirets (3 à 63 caractères)."
    if len(p) > PREFIX_MAX:
        err["prefix"] = f"Dossier trop long ({PREFIX_MAX} caractères au plus)."
    if not k:
        err["access_key"] = "Clé d'accès requise."
    if err:
        raise InvalidStorage(err)
    return {"endpoint": e.rstrip("/"), "region": r, "bucket": b,
            "prefix": (p if p.endswith("/") else p + "/"), "access_key": k}


def view(conn, mosque_id: int) -> dict:
    """Ce que montre l'écran : la clé secrète est masquée."""
    r = db.one(conn, "SELECT * FROM storage WHERE mosque_id = ?", mosque_id) or {}
    secret = r.get("secret_key") or ""
    return {
        "endpoint": r.get("endpoint") or "",
        "region": r.get("region") or REGION_DEFAULT,
        "bucket": r.get("bucket") or "",
        "access_key": r.get("access_key") or "",
        "secret_key": db.MASK if secret else "",
        "prefix": r.get("prefix") or PREFIX_DEFAULT,
        "enabled": bool(r.get("enabled")),
        "configured": bool(r.get("bucket") and r.get("access_key") and secret),
    }


def save(conn, mosque_id: int, payload: dict) -> dict:
    """Enregistre ; un champ secret renvoyé sous sa forme masquée garde la valeur précédente."""
    avant = db.one(conn, "SELECT * FROM storage WHERE mosque_id = ?", mosque_id) or {}
    propre = check(payload.get("endpoint", ""), payload.get("bucket", ""),
                   payload.get("region", ""), payload.get("prefix", ""),
                   payload.get("access_key", ""))
    secret = _net(payload.get("secret_key", ""))
    if secret in ("", db.MASK):
        secret = avant.get("secret_key") or ""
    if not secret:
        raise InvalidStorage({"secret_key": "Clé secrète requise."})
    # Opt-in: without an explicit "on", the replays stay on the machine.
    actif = str(payload.get("enabled", "0")).strip().lower() in ("1", "true", "on", "oui", "yes")
    conn.execute(
        """INSERT INTO storage (mosque_id, endpoint, region, bucket, access_key, secret_key, prefix, enabled, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(mosque_id) DO UPDATE SET endpoint=excluded.endpoint, region=excluded.region,
             bucket=excluded.bucket, access_key=excluded.access_key, secret_key=excluded.secret_key,
             prefix=excluded.prefix, enabled=excluded.enabled, updated_at=excluded.updated_at""",
        (mosque_id, propre["endpoint"], propre["region"], propre["bucket"], propre["access_key"],
         secret, propre["prefix"], 1 if actif else 0, db.now_iso()))
    conn.commit()
    return view(conn, mosque_id)


def effective(conn, mosque_id: int) -> dict | None:
    """Réglage complet, clés en clair : réservé au moteur, par l'endpoint interne."""
    r = db.one(conn, "SELECT * FROM storage WHERE mosque_id = ?", mosque_id) or {}
    if not (r.get("enabled") and r.get("bucket") and r.get("access_key") and r.get("secret_key")):
        return None
    return {"endpoint": r.get("endpoint") or "", "region": r.get("region") or REGION_DEFAULT,
            "bucket": r["bucket"], "access_key": r["access_key"], "secret_key": r["secret_key"],
            "prefix": r.get("prefix") or PREFIX_DEFAULT}


def probe(target: dict) -> tuple[bool, str]:
    """Dépose un objet minuscule puis le retire : dit si le compartiment accepte vraiment l'écriture."""
    from minbar import upload

    try:
        client = upload.client(target)
        cle = target["prefix"] + "minbar-test.txt"
        client.put_object(Bucket=target["bucket"], Key=cle, Body=b"minbar\n",
                          ContentType="text/plain")
        client.delete_object(Bucket=target["bucket"], Key=cle)
    except Exception as e:                                  # noqa: BLE001 — toute erreur se raconte
        return False, upload.human(e)
    return True, f"Écriture acceptée dans « {target['bucket']} » ({target['prefix']})."
