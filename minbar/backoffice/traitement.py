"""Traitement d'après-direct, automatique : session terminée -> pipeline -> import.

Le fil du back-office parcourt les runs récents de chaque mosquée en import automatique. Toute
session terminée qui produit du contenu (tout sauf la Janaza) et n'a pas encore de transcription
part dans le pipeline, une à la fois (le GPU est partagé). Le résultat est importé aussitôt.

L'état se lit dans data/state/<run>/traitement.json : {"<index>": {etat, debut, fin, erreur}}.
etat : attente | en_cours | termine | echec. Un échec n'est pas relancé seul : le bouton « Relancer »
du back-office remet la session en attente.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .. import paths, runs
from ..pipeline.output import SessionOutput
from . import db, importer

FICHIER = "traitement.json"
FENETRE_H = 48            # runs plus anciens : historique, jamais traités d'office
_verrou = threading.Lock()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def lire(run_dir: Path) -> dict:
    try:
        d = json.loads((run_dir / FICHIER).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _ecrire(run_dir: Path, etat: dict) -> None:
    tmp = run_dir / (FICHIER + ".tmp")
    tmp.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(run_dir / FICHIER)


def _maj(run_dir: Path, idx: int, **champs) -> None:
    with _verrou:
        etat = lire(run_dir)
        etat[str(idx)] = {**etat.get(str(idx), {}), **champs}
        _ecrire(run_dir, etat)


def _recent(run_id: str, now: dt.datetime) -> bool:
    try:
        return now - dt.datetime.strptime(run_id, "%Y%m%d_%H%M%S") < dt.timedelta(hours=FENETRE_H)
    except ValueError:
        return False


def _runs(mosque: dict):
    state = db.mosque_dirs(mosque)["state"]
    if not state.is_dir():
        return []
    return sorted((d for d in state.iterdir() if runs.RUN_RE.match(d.name) and (d / runs.SESSIONS).exists()),
                  key=lambda d: d.name, reverse=True)


def a_traiter(mosque: dict, now: dt.datetime | None = None) -> list[tuple[Path, int]]:
    """(dossier du run, index) des sessions à passer dans le pipeline, la plus ancienne d'abord."""
    now = now or dt.datetime.now()
    out = db.mosque_dirs(mosque)["output"]
    todo = []
    for rd in _runs(mosque):
        etat = lire(rd)
        try:
            data = runs.load(rd / runs.SESSIONS)
        except (OSError, ValueError):
            continue
        for s in data.get("sessions", []):
            idx = s.get("index")
            if idx is None or not runs.is_done(s) or not runs.produces_content(s):
                continue
            e = etat.get(str(idx), {}).get("etat")
            if e == "attente":
                todo.append((rd, idx))
            elif e is None and _recent(rd.name, now) \
                    and not SessionOutput(out / rd.name / runs.session_dir(idx)).is_transcribed():
                todo.append((rd, idx))
    return sorted(todo, key=lambda t: (t[0].name, t[1]))


def traiter(conn, mosque: dict, run_dir: Path, idx: int, log=print) -> bool:
    out = db.mosque_dirs(mosque)["output"]
    _maj(run_dir, idx, etat="en_cours", debut=_now(), fin=None, erreur=None)
    log(f"{run_dir.name} session {runs.session_number(idx)} : traitement lancé")
    cmd = [sys.executable, "-m", "minbar.pipeline.run", str(run_dir / runs.SESSIONS),
           "--only-session", str(idx), "--out", str(out)]
    # Identité de la mosquée dans les clips : logo et couleurs, quand ils sont configurés.
    if (mosque.get("logo_path") or "").strip():
        from . import branding
        logo = branding.path_for(mosque)
        # les clips n'incrustent que les images matricielles : un SVG (sans convertisseur) est ignoré
        if logo.exists() and logo.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            cmd += ["--logo", str(logo)]
    for key, option in (("brand_green", "--brand-green"), ("brand_gold", "--brand-gold")):
        value = (mosque.get(key) or "").strip()
        if value:
            cmd += [option, value]
    try:
        p = subprocess.run(cmd, cwd=paths.ROOT, capture_output=True, text=True, env=_env())
        ok = p.returncode == 0 and SessionOutput(out / run_dir.name / runs.session_dir(idx)).is_transcribed()
        if not ok:
            raise RuntimeError(_resume_erreur(p.stderr or p.stdout, p.returncode))
        importer.import_run(conn, mosque, run_dir.name, log=log)
    except Exception as e:  # noqa: BLE001 — l'échec est montré dans le back-office
        _maj(run_dir, idx, etat="echec", fin=_now(), erreur=str(e)[:300])
        log(f"{run_dir.name} session {runs.session_number(idx)} : échec : {e}")
        return False
    _maj(run_dir, idx, etat="termine", fin=_now(), erreur=None)
    log(f"{run_dir.name} session {runs.session_number(idx)} : extraits prêts")
    return True


def _env() -> dict:
    """Le pipeline appelle ffmpeg par son nom : son dossier rejoint le PATH du service."""
    env = dict(os.environ)
    exe = importer._ffmpeg()
    if exe:
        env["PATH"] = str(Path(exe).parent) + os.pathsep + env.get("PATH", "")
    return env


def _resume_erreur(sortie: str, code: int) -> str:
    lignes = [ligne.strip() for ligne in (sortie or "").splitlines() if ligne.strip()]
    return lignes[-1] if lignes else f"le traitement s'est arrêté (code {code})"


def reprendre(mosque: dict) -> None:
    """Au démarrage : un traitement resté « en cours » a été interrompu, il repart en attente."""
    for rd in _runs(mosque):
        for k, v in lire(rd).items():
            if v.get("etat") == "en_cours":
                _maj(rd, int(k), etat="attente")


def tour(conn, mosque: dict, log=print) -> int:
    """Traite toutes les sessions en attente de la mosquée. Renvoie le nombre traité."""
    n = 0
    for rd, idx in a_traiter(mosque):
        traiter(conn, mosque, rd, idx, log)
        n += 1
    return n


def mettre_en_file(run_dir: Path, idx: int) -> None:
    _maj(run_dir, idx, etat="attente", erreur=None, fin=None)


def relancer(mosque: dict, run_id: str, idx: int) -> None:
    rd = db.mosque_dirs(mosque)["state"] / run_id
    if not runs.RUN_RE.match(run_id) or not (rd / runs.SESSIONS).exists():
        raise FileNotFoundError("direct introuvable")
    data = runs.load(rd / runs.SESSIONS)
    s = next((s for s in data.get("sessions", []) if s.get("index") == idx), None)
    if s is None or not runs.is_done(s) or not runs.produces_content(s):
        raise ValueError("cette session ne se traite pas")
    if lire(rd).get(str(idx), {}).get("etat") == "en_cours":
        raise ValueError("traitement déjà en cours")
    _maj(rd, idx, etat="attente", erreur=None, fin=None)


def liste(mosque: dict, limite: int = 10) -> list[dict]:
    items = []
    for rd in _runs(mosque):
        try:
            data = runs.load(rd / runs.SESSIONS)
        except (OSError, ValueError):
            continue
        sess = {s.get("index"): s for s in data.get("sessions", [])}
        for k, v in sorted(lire(rd).items(), key=lambda kv: int(kv[0]), reverse=True):
            s = sess.get(int(k), {})
            items.append({"run": rd.name, "session": int(k), "label": importer.khutba_label(dict(s, index=int(k))),
                          "date": runs.run_date(rd.name), "etat": v.get("etat"), "debut": v.get("debut"),
                          "fin": v.get("fin"), "erreur": v.get("erreur")})
            if len(items) >= limite:
                return items
    return items
