"""Import d'une vidéo enregistrée hors direct (dars filmé par une caméra externe, par exemple).

La vidéo devient un run d'une seule session terminée : data/state/<run>/sessions.json, la vidéo dans
data/output/<run>/source/. Elle est marquée « en attente » dans traitement.json, et le fil de
traitement automatique la prend comme une session de direct (transcription, extraits, import),
quelle que soit la date de l'enregistrement.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from .. import runs
from ..i18n import _
from . import db, importer, traitement

EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".webm"}
KINDS = ("dars", "conference", "khutba")
MAX_OCTETS = 50 * 1024**3


class ImportRefuse(ValueError):
    pass


def _nom_sur(nom: str) -> str:
    base = Path(nom or "").name
    ext = Path(base).suffix.lower()
    if ext not in EXTENSIONS:
        raise ImportRefuse(_("Unrecognised format: send a video (mp4, mov, mkv, m4v, avi, mts, webm)."))
    tige = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(base).stem).strip("_")[:60] or "video"
    return tige + ext


def preparer(meta: dict, now: dt.datetime | None = None) -> dict:
    """Valide les champs ; renvoie la session décrite (sans toucher au disque)."""
    now = now or dt.datetime.now()
    kind = (meta.get("kind") or "dars").strip().lower()
    if kind not in KINDS:
        raise ImportRefuse(_("Unknown type: dars, conference or khutba."))
    date = (meta.get("date") or "").strip() or now.strftime("%Y-%m-%d")
    try:
        jour = dt.datetime.strptime(date, "%Y-%m-%d")
    except ValueError as e:
        raise ImportRefuse(_("Invalid date.")) from e
    if jour.date() > now.date():
        raise ImportRefuse(_("The recording date is in the future."))
    no = (str(meta.get("session_no") or "")).strip()
    if no and not no.isdigit():
        raise ImportRefuse(_("The session number must be a number."))
    return {
        "nom": _nom_sur(meta.get("filename") or ""),
        "kind": kind,
        "jour": jour,
        "title": (meta.get("title") or "").strip()[:200],
        "speaker": (meta.get("speaker") or "").strip()[:120],
        "series": (meta.get("series") or "").strip()[:120],
        "session_no": int(no) if no else None,
    }


def _run_id(dirs: dict, jour: dt.datetime, now: dt.datetime) -> str:
    """Run daté du jour de l'enregistrement (la khutba porte cette date), unique."""
    t = now
    while True:
        rid = jour.strftime("%Y%m%d") + "_" + t.strftime("%H%M%S")
        if not (dirs["state"] / rid).exists() and not (dirs["output"] / rid).exists():
            return rid
        t += dt.timedelta(seconds=1)


def _ffprobe() -> str | None:
    ff = importer._ffmpeg()
    if ff:
        p = Path(ff).with_name("ffprobe" + Path(ff).suffix)
        if p.exists():
            return str(p)
    return shutil.which("ffprobe")


def sonder(path: Path) -> float | None:
    """Durée en secondes si le fichier contient une piste audio ; sinon ImportRefuse."""
    exe = _ffprobe()
    if not exe:
        return None  # pas de sonde : le pipeline jugera
    p = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        info = json.loads(p.stdout or "{}")
    except ValueError:
        info = {}
    if p.returncode != 0 or not info.get("format"):
        raise ImportRefuse(_("Unreadable file: this is not a valid video."))
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ImportRefuse(_("The video has no sound: nothing to transcribe."))
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


async def recevoir(mosque: dict, meta: dict, flux, log=print) -> str:
    """Écrit le flux sur le disque, crée le run, le met en file. Renvoie le run_id."""
    s = preparer(meta)
    now = dt.datetime.now()
    dirs = db.mosque_dirs(mosque)
    rid = _run_id(dirs, s["jour"], now)
    src_dir = dirs["output"] / rid / "source"
    src_dir.mkdir(parents=True)
    dest = src_dir / s["nom"]
    total = 0
    try:
        with open(dest, "wb") as f:
            async for bloc in flux:
                total += len(bloc)
                if total > MAX_OCTETS:
                    raise ImportRefuse(_("File too large (50 GB at most)."))
                f.write(bloc)
        if total == 0:
            raise ImportRefuse(_("Empty file."))
        duree = sonder(dest)
    except BaseException:
        shutil.rmtree(dirs["output"] / rid, ignore_errors=True)
        raise
    return _creer_run(mosque, s, dirs, rid, dest, total, duree, log)


def _creer_run(mosque: dict, s: dict, dirs: dict, rid: str, dest: Path, total: int, duree, log) -> str:
    """Écrit l'état du run pour une vidéo déjà sur le disque et la met en file."""
    rd = dirs["state"] / rid
    rd.mkdir(parents=True)
    session = {
        "index": 0,
        "planned": "",
        "status": "done",
        "recording": 0,
        "start_pos": 0.0,
        "end_pos": duree,
        "start_clock": None,
        "end_clock": None,
        "end_reason": "import",
        "kind": s["kind"],
        "title": s["title"],
        "speaker": s["speaker"],
        "series": s["series"],
        "session_no": s["session_no"],
        "source": "import",
    }
    runs.write(
        rd,
        {
            "run_id": rid,
            "recordings": [str(dest.resolve())],
            "recordings_clean": [str(dest.resolve())],
            "clock_sim": None,
            "sessions": [session],
            "mosque": mosque.get("name"),
            "source": "import",
        },
    )
    traitement.mettre_en_file(rd, 0)
    log(_("video import {run}: {size} MB received, queued", run=rid, size=total // (1024 * 1024)))
    return rid


# --- Envoi par morceaux -------------------------------------------------------------------------------
# Derrière Cloudflare le corps d'une requête est limité à 100 Mo et sa durée à 100 s : la vidéo part en
# morceaux de CHUNK_OCTETS au plus, chacun à l'offset exact déjà reçu (reprise possible : on relit
# l'offset au serveur après une coupure). Les envois en cours vivent dans <sortie>/.envois/<id>/.

CHUNK_OCTETS = 80 * 1024**2
MAX_CHUNK_OCTETS = 90 * 1024**2
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class EnvoiInconnu(LookupError):
    pass


class EnvoiDecale(ValueError):
    """Le morceau n'est pas à l'offset attendu ; `recu` dit où reprendre."""

    def __init__(self, recu: int):
        super().__init__(str(recu))
        self.recu = recu


def _envoi_dir(dirs: dict, upload_id: str) -> Path:
    if not _ID_RE.match(upload_id or ""):
        raise EnvoiInconnu(upload_id)
    return dirs["output"] / ".envois" / upload_id


def _envoi_meta(dirs: dict, upload_id: str, user_id: int) -> tuple[Path, dict]:
    d = _envoi_dir(dirs, upload_id)
    try:
        meta = json.loads((d / "meta.json").read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise EnvoiInconnu(upload_id) from e
    if meta.get("user") != user_id:
        raise EnvoiInconnu(upload_id)
    return d, meta


def envoi_ouvrir(mosque: dict, user_id: int, meta: dict, taille: int) -> dict:
    """Valide les champs, réserve un envoi. Renvoie {id, chunk, received}."""
    preparer(meta)
    if taille <= 0:
        raise ImportRefuse(_("Empty file."))
    if taille > MAX_OCTETS:
        raise ImportRefuse(_("File too large (50 GB at most)."))
    dirs = db.mosque_dirs(mosque)
    uid = secrets.token_hex(16)
    d = _envoi_dir(dirs, uid)
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps({"user": user_id, "size": taille, "meta": {k: str(v) for k, v in meta.items()}}), "utf-8"
    )
    (d / "data").touch()
    return {"id": uid, "chunk": CHUNK_OCTETS, "received": 0}


def envoi_etat(mosque: dict, user_id: int, upload_id: str) -> dict:
    d, m = _envoi_meta(db.mosque_dirs(mosque), upload_id, user_id)
    return {"id": upload_id, "chunk": CHUNK_OCTETS, "received": (d / "data").stat().st_size, "size": m["size"]}


async def envoi_morceau(mosque: dict, user_id: int, upload_id: str, offset: int, flux) -> int:
    """Ajoute un morceau à l'offset exact reçu ; renvoie le total reçu. Un morceau refusé ou coupé
    n'écrit rien de définitif : le fichier est ramené à l'offset d'avant."""
    d, m = _envoi_meta(db.mosque_dirs(mosque), upload_id, user_id)
    data = d / "data"
    deja = data.stat().st_size
    if offset != deja:
        raise EnvoiDecale(deja)
    n = 0
    try:
        with open(data, "ab") as f:
            async for bloc in flux:
                n += len(bloc)
                if n > MAX_CHUNK_OCTETS or deja + n > m["size"]:
                    raise ImportRefuse(_("Chunk too large."))
                f.write(bloc)
    except BaseException:
        with open(data, "r+b") as f:
            f.truncate(deja)
        raise
    return deja + n


def envoi_annuler(mosque: dict, user_id: int, upload_id: str) -> None:
    d, _m = _envoi_meta(db.mosque_dirs(mosque), upload_id, user_id)
    shutil.rmtree(d, ignore_errors=True)


def envoi_finir(mosque: dict, user_id: int, upload_id: str, log=print) -> str:
    """Tous les octets sont là : le fichier devient un run en file, comme un envoi d'un seul tenant."""
    dirs = db.mosque_dirs(mosque)
    d, m = _envoi_meta(dirs, upload_id, user_id)
    data = d / "data"
    total = data.stat().st_size
    if total != m["size"]:
        raise EnvoiDecale(total)
    s = preparer(m["meta"])
    rid = _run_id(dirs, s["jour"], dt.datetime.now())
    src_dir = dirs["output"] / rid / "source"
    src_dir.mkdir(parents=True)
    dest = src_dir / s["nom"]
    try:
        data.replace(dest)
        duree = sonder(dest)
    except BaseException:
        shutil.rmtree(dirs["output"] / rid, ignore_errors=True)
        raise
    shutil.rmtree(d, ignore_errors=True)
    return _creer_run(mosque, s, dirs, rid, dest, total, duree, log)
