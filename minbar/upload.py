"""Les rediffusions quittent la machine : envoi des enregistrements du direct vers un compartiment S3 / R2.

Le moteur demande le réglage au back-office au démarrage (comme l'entrée vidéo et les destinations) ;
il envoie les fichiers après l'arrêt du direct, en tâche de fond, sans jamais bloquer le direct lui-même.
Un index local (`data/uploads.json`) évite de renvoyer deux fois le même fichier, y compris après une
coupure : tout fichier non envoyé est repris à la tentative suivante.
"""
from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path
from typing import Callable, Iterable

from . import paths

CONTENT_TYPES = {".mkv": "video/x-matroska", ".mp4": "video/mp4", ".webm": "video/webm"}


def client(target: dict):                       # pragma: no cover (réseau)
    """Client S3 : l'endpoint vide vise AWS, sinon R2 / MinIO / Backblaze."""
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError  # noqa: F401 — importé pour l'appelant

    conf = BotoConfig(retries={"max_attempts": 5, "mode": "standard"},
                      signature_version="s3v4",
                      connect_timeout=15, read_timeout=120)
    return boto3.client("s3", endpoint_url=target.get("endpoint") or None,
                        region_name=target.get("region") or "auto",
                        aws_access_key_id=target["access_key"],
                        aws_secret_access_key=target["secret_key"],
                        config=conf)


def human(e: Exception) -> str:
    """Message lisible pour un écran de réglages : la cause, pas la trace."""
    texte = str(e)
    for cause, dit in (("NoSuchBucket", "Le compartiment n'existe pas (nom ou compte)."),
                       ("InvalidAccessKeyId", "Clé d'accès inconnue du service."),
                       ("SignatureDoesNotMatch", "Clé secrète refusée (signature invalide)."),
                       ("AccessDenied", "Le service refuse l'écriture : droits du jeton insuffisants."),
                       ("EndpointConnectionError", "Service injoignable : vérifiez l'adresse et le réseau."),
                       ("ConnectTimeoutError", "Délai dépassé en joignant le service."),
                       ("InvalidBucketName", "Nom de compartiment invalide pour ce service."),
                       ("SSLError", "Certificat refusé : vérifiez l'adresse (https).")):
        if cause in texte:
            return dit
    return f"Échec : {texte[:180]}"


def key_for(prefix: str, chemin: str | Path) -> str:
    return (prefix or "") + Path(chemin).name


def content_type(chemin: str | Path) -> str:
    return CONTENT_TYPES.get(Path(chemin).suffix.lower(), "application/octet-stream")


class Index:
    """Fichier local : ce qui est déjà parti, et avec quelle empreinte."""

    def __init__(self, fichier: Path | None = None):
        self.fichier = Path(fichier or (paths.DATA / "uploads.json"))
        self.donnees: dict[str, dict] = {}
        try:
            self.donnees = json.loads(self.fichier.read_text("utf-8"))
        except (OSError, ValueError):
            self.donnees = {}

    def deja(self, chemin: str | Path) -> bool:
        f = self.donnees.get(str(chemin)) or {}
        p = Path(chemin)
        return bool(f) and f.get("taille") == p.stat().st_size and f.get("mtime") == int(p.stat().st_mtime)

    def noter(self, chemin: str | Path, cle: str, etag: str = "") -> None:
        p = Path(chemin)
        self.donnees[str(p)] = {"taille": p.stat().st_size, "mtime": int(p.stat().st_mtime),
                                "cle": cle, "etag": etag, "quand": datetime.datetime.now().isoformat(timespec="seconds")}
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.donnees, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.fichier)


def _envoyer(client, target: dict, index: Index, chemins: Iterable[str | Path],
             log: Callable[[str], None] = lambda _m: None) -> list[str]:
    envoyes = []
    for chemin in chemins:
        p = Path(chemin)
        if not p.is_file() or p.stat().st_size == 0:
            log(f"Rediffusion : {p.name} absente ou vide, ignorée")
            continue
        if index.deja(p):
            log(f"Rediffusion : {p.name} déjà envoyée")
            continue
        cle = key_for(target.get("prefix", ""), p)
        mb = p.stat().st_size / 1e6
        log(f"Rediffusion : envoi de {p.name} ({mb:.0f} Mo) vers {target['bucket']}/{cle}")
        try:
            client.upload_file(str(p), target["bucket"], cle,
                               ExtraArgs={"ContentType": content_type(p)})
            index.noter(p, cle)
            envoyes.append(cle)
            log(f"Rediffusion : {p.name} envoyée ({target['bucket']}/{cle})")
        except Exception as e:                    # noqa: BLE001 — une rediffusion ne fait pas tomber le moteur
            log(f"Rediffusion : échec pour {p.name} — {human(e)}")
    return envoyes


class Uploader:
    """Envoi après le direct, dans un fil séparé : le moteur n'attend jamais le réseau."""

    def __init__(self, target: dict | None, index: Index | None = None,
                 log: Callable[[str], None] = lambda _m: None):
        self.target = target
        self.index = index or Index()
        self.log = log
        self.fil: threading.Thread | None = None
        self.resultat: list[str] = []

    @property
    def actif(self) -> bool:
        return bool(self.target)

    def envoyer(self, chemins: Iterable[str | Path]) -> threading.Thread | None:
        if not self.actif:
            return None
        liste = [str(c) for c in chemins]
        if not liste:
            return None
        self.fil = threading.Thread(target=self._tourner, args=(liste,), name="upload-rediffusions", daemon=True)
        self.fil.start()
        return self.fil

    def _tourner(self, liste: list[str]) -> None:
        target = self.target or {}
        try:
            self.resultat += _envoyer(client(target), target, self.index, liste, self.log)
        except Exception as e:                    # noqa: BLE001
            self.log(f"Rediffusion : envoi impossible — {human(e)}")

    def attendre(self, delai: float = 60.0) -> None:
        if self.fil:
            self.fil.join(delai)


def from_backoffice(backoffice_url: str, api_port: int, fetch: Callable[[str, str], dict] | None = None,
                    log: Callable[[str], None] = lambda _m: None) -> dict | None:
    """Réglage enregistré dans l'interface, ou None (rien d'actif, ou back-office injoignable)."""
    if not backoffice_url:
        return None
    import urllib.request
    from urllib.parse import urlencode

    from .backoffice.internal import HEADER, token

    url = backoffice_url.rstrip("/") + "/api/v1/internal/storage?" + urlencode({"port": api_port or 0})
    try:
        if fetch:
            data = fetch(url, token()) or {}
        else:                                     # pragma: no cover (réseau)
            req = urllib.request.Request(url, headers={HEADER: token()})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read(65536))
    except Exception as e:                        # noqa: BLE001
        log(f"Rediffusion : réglage du compartiment injoignable ({e}) — envoi désactivé")
        return None
    if not data.get("target"):
        return None
    log(f"Rediffusion : envoi vers {data['target']['bucket']}/{data['target']['prefix']}")
    return data["target"]
