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
import shutil
import subprocess
from pathlib import Path

from .. import runs
from . import db, importer, traitement

EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".webm"}
KINDS = ("dars", "conference", "khutba")
MAX_OCTETS = 50 * 1024 ** 3


class ImportRefuse(ValueError):
    pass


def _nom_sur(nom: str) -> str:
    base = Path(nom or "").name
    ext = Path(base).suffix.lower()
    if ext not in EXTENSIONS:
        raise ImportRefuse("Format non reconnu : envoyez une vidéo (mp4, mov, mkv, m4v, avi, mts, webm).")
    tige = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(base).stem).strip("_")[:60] or "video"
    return tige + ext


def preparer(meta: dict, now: dt.datetime | None = None) -> dict:
    """Valide les champs ; renvoie la session décrite (sans toucher au disque)."""
    now = now or dt.datetime.now()
    kind = (meta.get("kind") or "dars").strip().lower()
    if kind not in KINDS:
        raise ImportRefuse("Type inconnu : dars, conférence ou khutba.")
    date = (meta.get("date") or "").strip() or now.strftime("%Y-%m-%d")
    try:
        jour = dt.datetime.strptime(date, "%Y-%m-%d")
    except ValueError as e:
        raise ImportRefuse("Date invalide.") from e
    if jour.date() > now.date():
        raise ImportRefuse("La date de l'enregistrement est dans le futur.")
    no = (str(meta.get("session_no") or "")).strip()
    if no and not no.isdigit():
        raise ImportRefuse("Le numéro de séance doit être un nombre.")
    return {"nom": _nom_sur(meta.get("filename") or ""), "kind": kind, "jour": jour,
            "title": (meta.get("title") or "").strip()[:200], "speaker": (meta.get("speaker") or "").strip()[:120],
            "series": (meta.get("series") or "").strip()[:120], "session_no": int(no) if no else None}


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
        return None                      # pas de sonde : le pipeline jugera
    p = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        info = json.loads(p.stdout or "{}")
    except ValueError:
        info = {}
    if p.returncode != 0 or not info.get("format"):
        raise ImportRefuse("Fichier illisible : ce n'est pas une vidéo valide.")
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ImportRefuse("La vidéo n'a pas de son : rien à transcrire.")
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
                    raise ImportRefuse("Fichier trop volumineux (50 Go au plus).")
                f.write(bloc)
        if total == 0:
            raise ImportRefuse("Fichier vide.")
        duree = sonder(dest)
    except BaseException:
        shutil.rmtree(dirs["output"] / rid, ignore_errors=True)
        raise
    rd = dirs["state"] / rid
    rd.mkdir(parents=True)
    session = {"index": 0, "planned": "", "status": "done", "recording": 0, "start_pos": 0.0, "end_pos": duree,
               "start_clock": None, "end_clock": None, "end_reason": "import", "kind": s["kind"], "title": s["title"],
               "speaker": s["speaker"], "series": s["series"], "session_no": s["session_no"], "source": "import"}
    runs.write(rd, {"run_id": rid, "recordings": [str(dest.resolve())], "recordings_clean": [str(dest.resolve())],
                    "clock_sim": None, "sessions": [session], "mosque": mosque.get("name"), "source": "import"})
    traitement.mettre_en_file(rd, 0)
    log(f"import vidéo {rid} : {total // (1024 * 1024)} Mo reçus, mis en file")
    return rid
