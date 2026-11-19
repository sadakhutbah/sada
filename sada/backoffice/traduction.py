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
import traceback

from ..i18n import _
from ..pipeline import llm, translate, verses
from ..pipeline import subtitles as tracks
from ..pipeline.output import SessionOutput
from . import db, orm
from .jobs import Job, JobStatus, JsonFileStore
from .models import Khutba

STATUSES = ("", *(s.value for s in (JobStatus.RUNNING, JobStatus.READY, JobStatus.FAILED)))


def fichier(k: dict):
    return SessionOutput(k["output_dir"]).translation_job


#: one run per session in this process (the state file covers other processes)
JOB = Job(
    JsonFileStore(fichier),
    busy_message=lambda: _("subtitles are already being translated for this video"),
    thread_name="soustitres",
)


def etat(k: dict) -> dict:
    """État montrable : `{"status", "progress", "error", "started_at", "finished_at"}`.

    Un « en cours » dont le processus a disparu (serveur arrêté) est un échec, pas une attente
    éternelle : l'écran peut alors proposer de relancer.
    """
    e = JOB.read(None, k)
    status = str(e.get("status") or "")
    return {
        "status": status if status in STATUSES else "",
        "progress": round(float(e.get("progress") or 0.0), 3),
        "error": str(e.get("error") or ""),
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
    JOB.start(
        None,
        k,
        _traduire,
        (db_path, k["id"]),
        fields={"progress": 0.0, "error": "", "started_at": db.now_iso(), "finished_at": ""},
        before_spawn=lambda: db.audit(
            conn, k["mosque_id"], user["id"], "soustitres.traduire", "khutba", k["id"], ip=ip
        ),
        background=background,
    )
    return etat(k)


def _traduire(db_path: str, kid: int, log=print) -> None:
    conn = db.connect(db_path)
    k = None
    try:
        k = orm.first(conn, Khutba, id=kid)
        assert k is not None
        from . import content

        data = content.load(conn, k)
        o = SessionOutput(k["output_dir"])
        vms = verses.shown(data["verses"], ambiguous=True)
        dernier = [0.0]

        def progres(f: float) -> None:
            if f - dernier[0] >= 0.02 or f >= 1.0:
                dernier[0] = f
                JOB.update(None, k, progress=round(f, 3))

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
            JOB.mark_failed(None, k, why, progress=1.0, finished_at=db.now_iso())
            log("[sous-titres]", why)
        else:
            JOB.mark_ready(None, k, progress=1.0, finished_at=db.now_iso())
            db.audit(conn, k["mosque_id"], None, "soustitres.pret", "khutba", kid)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        if k:
            JOB.mark_failed(None, k, str(e)[:400], finished_at=db.now_iso())
    finally:
        conn.close()
