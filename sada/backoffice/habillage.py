"""Habillage d'une vidéo de session, côté back-office : réglages, aperçu, rendu en tâche de fond.

Le rendu lui-même est dans `sada.pipeline.habillage`. Ici : d'où vient la source, où va le résultat
(`<dossier de la session>/habillage/`, jamais à côté de l'original), quels champs on préremplit
(titre, intervenant, série, date de la session) et ce que la mosquée garde d'une fois sur l'autre
(positions, durées, message de fin : son identité, pas le contenu d'un dars précis).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import traceback
from pathlib import Path

from ..i18n import _
from ..pipeline import clips as clipmod
from ..pipeline import habillage as rendu
from ..pipeline import subtitles as sous_titres
from ..pipeline import verses
from . import db

#: réglages qui décrivent la mosquée et pas une vidéo : retenus pour la prochaine fois
IDENTITE = (
    "intro_on",
    "lower_on",
    "logo_on",
    "bar_on",
    "outro_on",
    "intro_s",
    "lower_at",
    "lower_s",
    "outro_s",
    "logo_pos",
    "logo_size",
    "logo_opacity",
    "outro_text",
    "outro_link",
)

MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


# ------------------------------------------------------------------ source et fichiers


def source(k: dict) -> tuple[str, float, float | None] | None:
    """(fichier, début, fin) de la vidéo à habiller : la vidéo de la session si elle a été extraite,
    sinon l'enregistrement complet coupé aux bornes de la session. None : rien à habiller."""
    if k.get("video_path") and Path(k["video_path"]).exists():
        return k["video_path"], 0.0, None
    rec = k.get("recording")
    if rec and Path(rec).exists():
        return rec, float(k.get("start_pos") or 0.0), (float(k["end_pos"]) if k.get("end_pos") else None)
    return None


def dossier(k: dict) -> Path:
    return Path(k["output_dir"]) / "habillage"


def fichier_video(k: dict) -> Path:
    return dossier(k) / "video-habillee.mp4"


def fichier_rendu_en_cours(k: dict) -> Path:
    """Le rendu s'écrit à part : la vidéo habillée précédente reste affichée jusqu'à ce qu'il aboutisse."""
    return dossier(k) / "video-habillee.en-cours.mp4"


def fichier_options_rendues(k: dict) -> Path:
    """Options de la vidéo habillée en place (celles du formulaire peuvent déjà avoir changé)."""
    return dossier(k) / "video-habillee.json"


def fichier_apercu(k: dict) -> Path:
    return dossier(k) / "apercu.jpg"


def nom_telechargement(k: dict) -> str:
    """Nom proposé au téléchargement : la date et le genre, jamais le chemin sur la machine."""
    return f"sada-{k.get('kind') or 'session'}-{(k.get('date') or '')[:10] or k['id']}-habillee.mp4"


def nom_sous_titres(k: dict, langue: str, brut: bool = False) -> str:
    """Nom du .srt téléchargé : date, genre, langue ; `-original` pour l'alignement sur la vidéo source."""
    base = f"sada-{k.get('kind') or 'session'}-{(k.get('date') or '')[:10] or k['id']}"
    return f"{base}-sous-titres-{langue}{'-original' if brut else ''}.srt"


def marque(m: dict) -> tuple[dict | None, str | None]:
    """Couleurs et logo de la mosquée, comme pour les clips (vides : charte des clips, sans logo)."""
    brand = {c: m[f"brand_{c}"] for c in ("green", "gold") if (m.get(f"brand_{c}") or "").strip()}
    logo = None
    if (m.get("logo_path") or "").strip():
        from . import branding

        p = branding.path_for(m)
        if p.exists() and p.suffix.lower() in clipmod.LOGO_EXT:
            logo = str(p)
    return brand or None, logo


# ------------------------------------------------------------------ préremplissage


def _date_fr(iso: str | None) -> str:
    try:
        d = dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return ""
    return f"{d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"


def prerempli(conn, k: dict, m: dict) -> dict:
    """Options d'une première ouverture : réglages retenus par la mosquée + métadonnées de la session."""
    base = _defauts_mosquee(m)
    imam = db.one(conn, "SELECT display_name FROM users WHERE id = ?", k["imam_id"]) if k.get("imam_id") else None
    speaker = (k.get("speaker") or "").strip() or ((imam or {}).get("display_name") or "").strip()
    titre = (k.get("title") or "").strip() or (k.get("theme") or "").strip() or (k.get("label") or "").strip()
    morceaux = []
    if (k.get("series") or "").strip():
        morceaux.append(k["series"].strip())
    if k.get("session_no"):
        morceaux.append(f"séance {k['session_no']}")
    if _date_fr(k.get("date")):
        morceaux.append(_date_fr(k.get("date")))
    champs = {
        "title": titre,
        "speaker": speaker,
        "subtitle": " · ".join(morceaux),
        "role": "Imam de la mosquée" if imam and not (k.get("speaker") or "").strip() else "",
    }
    o = rendu.normaliser(champs, base)
    if not o["speaker"]:
        o["lower_on"] = False
    if not o["title"]:
        o["intro_on"] = False
    return o


def _defauts_mosquee(m: dict) -> dict:
    try:
        d = json.loads(m.get("habillage_defaults") or "{}")
    except ValueError:
        d = {}
    try:
        return rendu.normaliser({k: v for k, v in d.items() if k in IDENTITE} if isinstance(d, dict) else {})
    except ValueError:
        return dict(rendu.DEFAUTS)


def retenir(conn, m: dict, o: dict) -> None:
    conn.execute(
        "UPDATE mosques SET habillage_defaults = ? WHERE id = ?",
        (json.dumps({k: o[k] for k in IDENTITE}, ensure_ascii=False), m["id"]),
    )


# ------------------------------------------------------------------ état


def ligne(conn, kid: int) -> dict | None:
    return db.one(conn, "SELECT * FROM habillages WHERE khutba_id = ?", kid)


def decalage_carton(conn, k: dict) -> float:
    """Durée du carton d'ouverture de la vidéo habillée en place (0 s sans rendu, ou sans carton)."""
    h = ligne(conn, k["id"])
    rendues = fichier_options_rendues(k)
    try:
        brut = rendues.read_text(encoding="utf-8") if rendues.exists() else ((h or {}).get("options") or "{}")
        return rendu.chrono(rendu.normaliser(json.loads(brut), rendu.DEFAUTS), 0.0).i
    except (ValueError, TypeError, OSError):
        return 0.0


def version_habillee(conn, k: dict) -> dict | None:
    """La vidéo habillée prête, celle qu'on affiche : son décalage d'intro recale la transcription."""
    if not (ligne(conn, k["id"]) and fichier_video(k).exists()):
        return None
    return {"offset_s": decalage_carton(conn, k), "v": int(fichier_video(k).stat().st_mtime)}


def etat(conn, k: dict, m: dict) -> dict:
    """Ce que l'écran affiche : options (enregistrées ou préremplies), statut, fichiers prêts."""
    h = ligne(conn, k["id"])
    try:
        options = (
            rendu.normaliser(json.loads(h["options"]), rendu.DEFAUTS)
            if h and h["options"] not in ("", "{}")
            else prerempli(conn, k, m)
        )
    except ValueError:
        options = prerempli(conn, k, m)
    video = fichier_video(k)
    # Vidéo habillée en place : elle reste disponible pendant un nouveau rendu, ou après un échec.
    pret = bool(h and video.exists())
    apercu = fichier_apercu(k)
    brand, logo = marque(m)
    return {
        "options": options,
        "status": (h or {}).get("status") or "",
        "progress": round(float((h or {}).get("progress") or 0), 3),
        "error": (h or {}).get("error") or "",
        "started_at": (h or {}).get("started_at") or "",
        "finished_at": (h or {}).get("finished_at") or "",
        "duration_s": (h or {}).get("duration_s"),
        "video_ready": pret,
        "subtitles": langues_disponibles(conn, k) if pret else [],
        "size_mb": round(video.stat().st_size / 1048576, 1) if pret else None,
        "video_v": int(video.stat().st_mtime) if pret else 0,
        "preview_ready": apercu.exists(),
        "preview_v": int(apercu.stat().st_mtime) if apercu.exists() else 0,
        "has_source": source(k) is not None,
        "has_logo": logo is not None,
        "mosque": m.get("name") or "",
        "colors": {
            "green": "#{:02X}{:02X}{:02X}".format(*rendu.couleurs(brand)["green"]),
            "gold": "#{:02X}{:02X}{:02X}".format(*rendu.couleurs(brand)["gold"]),
        },
    }


def _enregistrer(conn, k: dict, o: dict, **champs) -> None:
    now = db.now_iso()
    if ligne(conn, k["id"]) is None:
        conn.execute(
            "INSERT INTO habillages (mosque_id, khutba_id, options, updated_at) VALUES (?, ?, ?, ?)",
            (k["mosque_id"], k["id"], "{}", now),
        )
    sets = ", ".join(f"{c} = ?" for c in ("options", *champs, "updated_at"))
    conn.execute(
        f"UPDATE habillages SET {sets} WHERE khutba_id = ?",
        (json.dumps(o, ensure_ascii=False), *champs.values(), now, k["id"]),
    )


def sans_chemin(message: str) -> str:
    """Message d'erreur montrable : un chemin de la machine n'en garde que le nom du fichier."""
    return re.sub(r"(?:[A-Za-z]:)?[/\\][^\s:'\"]+", lambda x: Path(x.group(0)).name, message)


# ------------------------------------------------------------------ sous-titres (.srt, à part sur YouTube)


def pistes(conn, k: dict) -> dict[str, list[dict]]:
    """Passages par langue de la vidéo (corrections et sous-titres ajoutés compris), dans le temps de la session."""
    from . import content

    data = content.load(conn, k)
    return sous_titres.tracks(data["segments"], verses.shown(data["verses"], ambiguous=True), data["translations"])


def langues_disponibles(conn, k: dict) -> list[str]:
    """Langues de la transcription : les .srt (téléchargés) comme les .vtt (lus par le lecteur) en découlent."""
    return list(pistes(conn, k))


def alignements(conn, k: dict) -> dict[str, float]:
    """Décalage des temps de la session : vidéo habillée (carton + coupe), vidéo d'origine (coupe seule)."""
    src = source(k)
    debut = float(src[1]) if src else 0.0
    return {"habille": decalage_carton(conn, k) - debut, "brut": -debut}


def sous_titres_srt(conn, k: dict, langue: str, brut: bool = False) -> tuple[str, bytes]:
    """(nom du fichier, contenu UTF-8). Une langue sans sous-titre lève ValueError, avec un message montrable."""
    disponibles = pistes(conn, k)
    if langue not in disponibles:
        raise ValueError(_("No subtitles in this language for this video."))
    texte = sous_titres.srt(sous_titres.cues(disponibles[langue], alignements(conn, k)["brut" if brut else "habille"]))
    if not texte.strip():
        raise ValueError(_("No subtitles in this language for this video."))
    return nom_sous_titres(k, langue, brut), texte.encode("utf-8")


def sous_titres_vtt(conn, k: dict, langue: str, brut: bool = False) -> bytes:
    """Le WebVTT d'une langue, lu sur place par le `<track>` du lecteur.

    Il ne dépend pas de la vidéo habillée : dès que la transcription existe, le lecteur peut afficher
    les sous-titres (`brut` : alignés sur la vidéo d'origine, sinon sur la vidéo habillée).
    Une langue sans sous-titre lève ValueError ; une langue connue dont plus rien ne tombe dans la
    vidéo rend un fichier vide valide, qu'un lecteur accepte sans erreur."""
    disponibles = pistes(conn, k)
    if langue not in disponibles:
        raise ValueError(_("No subtitles in this language for this video."))
    return sous_titres.vtt(
        sous_titres.cues(disponibles[langue], alignements(conn, k)["brut" if brut else "habille"])
    ).encode("utf-8")


def recover_interrupted(conn) -> None:
    """Rendus en cours quand le serveur s'est arrêté : ils ne finiront jamais."""
    conn.execute(
        "UPDATE habillages SET status = 'echec', error = ?, updated_at = ? WHERE status = 'en_cours'",
        (_("interrupted (server stopped)"), db.now_iso()),
    )


# ------------------------------------------------------------------ actions


def _options(conn, k: dict, brut: dict) -> dict:
    h = ligne(conn, k["id"])
    base = json.loads(h["options"]) if h and h["options"] not in ("", "{}") else rendu.DEFAUTS
    o = rendu.normaliser(brut, base)
    rendu.verifier(o)
    return o


def apercu(conn, k: dict, m: dict, brut: dict) -> dict:
    """Planche d'aperçu (quelques secondes) ; les options sont enregistrées avec."""
    o = _options(conn, k, brut)
    src = source(k)
    if not src:
        raise ValueError(_("source video not found: nothing to dress"))
    brand, logo = marque(m)
    rendu.apercu(o, src[0], str(fichier_apercu(k)), m.get("name") or "", brand, logo, debut=src[1], fin=src[2])
    _enregistrer(conn, k, o, preview_at=db.now_iso())
    retenir(conn, m, o)
    return o


def lancer(conn, db_path: str, k: dict, m: dict, brut: dict, user: dict, ip=None, background: bool = True) -> dict:
    o = _options(conn, k, brut)
    h = ligne(conn, k["id"])
    if h and h["status"] == "en_cours":
        raise ValueError(_("a render is already running for this video"))
    if not source(k):
        raise ValueError(_("source video not found: nothing to dress"))
    _enregistrer(
        conn,
        k,
        o,
        status="en_cours",
        progress=0.0,
        error="",
        started_at=db.now_iso(),
        finished_at="",
        user_id=user["id"],
    )
    retenir(conn, m, o)
    db.audit(
        conn,
        m["id"],
        user["id"],
        "habillage.lance",
        "khutba",
        k["id"],
        ip=ip,
        elements=",".join(e for e in rendu.BOOLEENS if o[e]),
    )
    if background:
        threading.Thread(target=_rendre, args=(db_path, k["id"]), daemon=True, name=f"habillage-{k['id']}").start()
    else:
        _rendre(db_path, k["id"])
    return o


def _rendre(db_path: str, kid: int, log=print) -> None:
    conn = db.connect(db_path)
    k = None
    try:
        h = ligne(conn, kid)
        k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", kid)
        assert k is not None
        m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", k["mosque_id"])
        o = rendu.normaliser(json.loads(h["options"]))
        src = source(k)
        if not src:
            raise RuntimeError(_("source video not found"))
        brand, logo = marque(m)
        dernier = [0.0]

        def progres(f: float) -> None:
            if f - dernier[0] >= 0.01:
                dernier[0] = f
                conn.execute(
                    "UPDATE habillages SET progress = ?, updated_at = ? WHERE khutba_id = ?",
                    (round(f, 3), db.now_iso(), kid),
                )

        tmp = fichier_rendu_en_cours(k)
        out = rendu.rendre(
            o,
            src[0],
            str(tmp),
            m.get("name") or "",
            brand,
            logo,
            progres=progres,
            log=lambda msg: log("[habillage]", msg, flush=True),
            debut=src[1],
            fin=src[2],
        )
        duree = rendu.sonder(out).duree
        # Le nouveau rendu remplace l'ancien d'un coup : l'ancienne vidéo est supprimée à cet instant.
        final = fichier_video(k)
        Path(out).replace(final)
        fichier_options_rendues(k).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        out = str(final)
        conn.execute(
            "UPDATE habillages SET status = 'pret', progress = 1, error = '', file_path = ?, duration_s = ?, "
            "finished_at = ?, updated_at = ? WHERE khutba_id = ?",
            (out, round(duree, 1), db.now_iso(), db.now_iso(), kid),
        )
        db.audit(conn, m["id"], None, "habillage.pret", "khutba", kid, duree=round(duree))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        if k:
            fichier_rendu_en_cours(k).unlink(missing_ok=True)
        conn.execute(
            "UPDATE habillages SET status = 'echec', error = ?, finished_at = ?, updated_at = ? WHERE khutba_id = ?",
            (sans_chemin(str(e))[:400], db.now_iso(), db.now_iso(), kid),
        )
    finally:
        conn.close()
