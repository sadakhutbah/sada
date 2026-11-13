"""Traduction des pistes de sous-titres d'une session (arabe ↔ français), en tâche de fond.

Une piste de langue X couvre tout le sermon : les caches de traduction (`translations.json`,
`translations_ar.json`) peuvent pourtant manquer de passages — session enregistrée avant cette
fonctionnalité, modèle indisponible pendant le pipeline, sous-titre ajouté à la main depuis. Cette
tâche complète les caches sans jamais retraduire un segment déjà connu, et tient à jour un petit
fichier d'état (`translation_job.json`, à côté de la transcription) que l'écran lit pour afficher
« traduction en cours » plutôt qu'un fichier incomplet présenté comme fini.

Les adresses .srt/.vtt ne lancent JAMAIS la tâche : elles servent ce qui existe et annoncent l'état
(en-têtes `X-Sada-Subtitles-*`), pour qu'une requête HTTP ne reste pas suspendue à un modèle.
"""

from __future__ import annotations

import json
import os
import threading
import traceback

from ..i18n import _
from ..pipeline import llm, translate, verses
from ..pipeline import subtitles as tracks
from ..pipeline.output import SessionOutput
from . import db

STATUSES = ("", "en_cours", "pret", "echec")

#: une seule tâche à la fois par session dans ce processus (le fichier d'état couvre les autres)
_VERROU = threading.Lock()
_EN_COURS: set[int] = set()


def fichier(k: dict):
    return SessionOutput(k["output_dir"]).translation_job


def _lire(k: dict) -> dict:
    try:
        data = json.loads(fichier(k).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ecrire(k: dict, **champs) -> dict:
    """État de la tâche, écrit d'un bloc (un lecteur ne voit jamais un fichier à moitié écrit)."""
    etat = _lire(k)
    etat.update(champs)
    p = fichier(k)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(etat, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass
    return etat


def _vivant(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def etat(k: dict) -> dict:
    """État montrable : `{"status", "progress", "error", "started_at", "finished_at"}`.

    Un « en cours » dont le processus a disparu (serveur arrêté) est un échec, pas une attente
    éternelle : l'écran peut alors proposer de relancer.
    """
    e = _lire(k)
    status = str(e.get("status") or "")
    error = str(e.get("error") or "")
    if status == "en_cours" and not _vivant(e.get("pid")):
        status, error = "echec", _("interrupted (server stopped)")
    return {
        "status": status if status in STATUSES else "",
        "progress": round(float(e.get("progress") or 0.0), 3),
        "error": error,
        "started_at": str(e.get("started_at") or ""),
        "finished_at": str(e.get("finished_at") or ""),
    }


def plan(conn, k: dict, data: dict | None = None) -> dict:
    """Passages et complétude des pistes, en une seule lecture de la session (`data` évite de la relire)."""
    from ..pipeline import subtitles as tracks
    from . import content

    data = data if data is not None else content.load(conn, k)
    return tracks.plan(
        data["segments"],
        verses.shown(data["verses"], ambiguous=True),
        data["translations"],
        data["translations_ar"],
    )


def lancer(conn, db_path: str, k: dict, user: dict, ip=None, background: bool = True) -> dict:
    """Démarre la traduction des passages manquants ; rend l'état aussitôt."""
    etat_avant = etat(k)
    if etat_avant["status"] == "en_cours":
        raise ValueError(_("subtitles are already being translated for this video"))
    with _VERROU:
        if k["id"] in _EN_COURS:
            raise ValueError(_("subtitles are already being translated for this video"))
        _EN_COURS.add(k["id"])
    _ecrire(
        k,
        status="en_cours",
        progress=0.0,
        error="",
        started_at=db.now_iso(),
        finished_at="",
        pid=os.getpid(),
    )
    db.audit(conn, k["mosque_id"], user["id"], "soustitres.traduire", "khutba", k["id"], ip=ip)
    if background:
        threading.Thread(target=_traduire, args=(db_path, k["id"]), daemon=True, name=f"soustitres-{k['id']}").start()
    else:
        _traduire(db_path, k["id"])
    return etat(k)


def _traduire(db_path: str, kid: int, log=print) -> None:
    conn = db.connect(db_path)
    k = None
    try:
        k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", kid)
        assert k is not None
        from . import content

        data = content.load(conn, k)
        o = SessionOutput(k["output_dir"])
        vms = verses.shown(data["verses"], ambiguous=True)
        dernier = [0.0]

        def progres(f: float) -> None:
            if f - dernier[0] >= 0.02 or f >= 1.0:
                dernier[0] = f
                _ecrire(k, progress=round(f, 3))

        translate.translate_tracks(
            data["segments"],
            vms,
            o.translations,
            o.translations_ar,
            True,
            log=lambda msg: print("[sous-titres]", msg, flush=True),
            progress=progres,
        )
        # Les caches rendus par la traduction, pas ceux lus avant : sinon la complétude est calculée
        # sur l'état d'avant et le travail qui vient d'aboutir passe pour un échec.
        caches = (
            json.loads(o.translations.read_text(encoding="utf-8")),
            json.loads(o.translations_ar.read_text(encoding="utf-8")),
        )
        langues = tracks.plan(data["segments"], vms, caches[0], caches[1])["languages"]
        incompletes = [code for code, s in langues.items() if not s["complete"]]
        if incompletes:
            why = (
                _("translation incomplete: the model did not answer for every passage")
                if llm.enabled()
                else _("translation incomplete: the language model is not available")
            )
            _ecrire(k, status="echec", progress=1.0, error=why, finished_at=db.now_iso())
            log("[sous-titres]", why)
        else:
            _ecrire(k, status="pret", progress=1.0, error="", finished_at=db.now_iso())
            db.audit(conn, k["mosque_id"], None, "soustitres.pret", "khutba", kid)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        if k:
            _ecrire(k, status="echec", error=str(e)[:400], finished_at=db.now_iso())
    finally:
        with _VERROU:
            _EN_COURS.discard(kid)
        conn.close()
