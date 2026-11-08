"""Traduction française des segments ARABES pour les sous-titres (les clips sont en français seul).

- Versets détectés (Coran) : jamais traduits par le LLM ; on affiche la traduction Hamidullah (verses.json).
  Le LLM choisit seulement, dans la traduction Hamidullah, l'EXTRAIT correspondant au passage récité :
  il doit être une sous-chaîne littérale (vérifié en code), sinon on garde la traduction complète.
- Formules rituelles (ta'awwudh, basmala, shahada…) : traductions fixes, pas de « carte verset ».
- Autres segments arabes : `claude -p` (même LLM que notes.py), traduction sobre, fidèle, courte.
  Un segment que le LLM signale comme citation coranique NON appariée n'est pas traduit (rien à l'écran
  plutôt qu'une traduction du Coran improvisée).
Cache : translations.json ; relancer ne rappelle pas le LLM. Lire et modifier ce cache : segment_fr, verse_fr, with_correction.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

from ..text import is_arabic
from . import llm
from .verses import formula_fr, shown


def _seg_key(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:16]


def verse_key(m: dict) -> str:
    return f"{m['ref']}@{m['start']}"


def empty() -> dict:
    return {"seg": {}, "verse_fr": {}, "method": ""}


def segment_fr(tr: dict, seg: dict) -> dict | None:
    """{"fr": traduction, "coran": récitation non appariée} du segment arabe, ou None."""
    return (tr.get("seg") or {}).get(_seg_key(seg["text"]))


def verse_fr(tr: dict, m: dict) -> str:
    """Traduction Hamidullah à afficher pour le verset : l'extrait récité s'il est connu, sinon le verset entier."""
    return (tr.get("verse_fr") or {}).get(verse_key(m)) or m.get("verse_fr", "")


def with_correction(tr: dict, seg: dict, fr: str) -> dict:
    """Copie des traductions où le segment a la traduction corrigée par un humain."""
    out = copy.deepcopy(tr)
    out.setdefault("seg", {})[_seg_key(seg["text"])] = {"fr": fr, "coran": False}
    return out


def translate_session(segments: list[dict], matches: list[dict], cache_path: str | Path, use_llm: bool = True,
                      timeout: int = 600, log=print) -> dict:
    """-> {"seg": {clé_segment: {"fr": str, "coran": bool}}, "verse_fr": {ref@start: extrait}, "method": str}"""
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else empty()
    vms = shown(matches, ambiguous=True)
    ar = [(i, s) for i, s in enumerate(segments) if is_arabic(s["text"])]
    todo = [(i, s) for i, s in ar if _seg_key(s["text"]) not in cache["seg"]]
    vtodo = [m for m in vms if verse_key(m) not in cache["verse_fr"]]
    if (todo or vtodo) and use_llm:
        lines = "\n".join(f"{i}\t[{s['start']:.0f}s]\t{s['text']}" for i, s in ar)
        vlines = "\n".join(f"{verse_key(m)}\tentendu : {m['heard']}\tHamidullah : {m['verse_fr']}" for m in vtodo) or "(aucun)"
        prompt = (
            "Tu prépares les sous-titres FRANÇAIS d'un court extrait vidéo d'une khutba du vendredi (sermon). "
            "Voici les segments en arabe transcrits automatiquement par Whisper (des erreurs de transcription sont possibles : "
            "corrige-les mentalement d'après le contexte, ex. « صورة الطلاق » = « سورة الطلاق »).\n"
            "Rends UNIQUEMENT un objet JSON, sans texte autour :\n"
            '{"seg": {"<numéro>": {"fr": "traduction", "coran": false}}, "verse_fr": {"<clé>": "extrait"}}\n'
            "Règles pour seg :\n"
            "- une entrée pour CHAQUE numéro ; traduction française fidèle, naturelle, sobre et respectueuse, sans rien ajouter ;\n"
            "- concise (c'est un sous-titre) : garde le sens, évite les tournures lourdes ; pas d'emoji ; « Allah » pour الله ;\n"
            "- formules rituelles : traductions usuelles (ex. « que la paix et les bénédictions d'Allah soient sur lui ») ;\n"
            "- si le segment est une RÉCITATION du Coran (verset), mets \"coran\": true et une traduction quand même ;\n"
            "- un segment peut commencer ou finir au milieu d'une phrase : traduis seulement ce qu'il contient, pour que les traductions se suivent.\n"
            "Règles pour verse_fr : pour chaque clé ci-dessous, recopie MOT POUR MOT le passage de la traduction Hamidullah "
            "qui correspond à la partie récitée (entendue) ; ce doit être une sous-chaîne exacte de la traduction fournie "
            "(sinon recopie-la entière).\n\n"
            f"VERSETS :\n{vlines}\n\nSEGMENTS (numéro, temps, texte) :\n{lines}\n"
        )
        log(f"traduction : {len(todo)} segment(s) arabes, {len(vtodo)} verset(s) via claude -p")
        d = llm.ask_json(prompt, timeout, log)
        if d:
            for i, s in ar:
                e = (d.get("seg") or {}).get(str(i))
                if isinstance(e, str):
                    e = {"fr": e, "coran": False}
                if e and e.get("fr"):
                    cache["seg"][_seg_key(s["text"])] = {"fr": e["fr"].strip(), "coran": bool(e.get("coran")), "ar": s["text"]}
            for m in vtodo:
                k = verse_key(m)
                ex = ((d.get("verse_fr") or {}).get(k) or "").strip().strip("«»\" ")
                ok = len(ex) >= 12 and ex.rstrip(".") in m["verse_fr"]
                cache["verse_fr"][k] = ex if ok else m["verse_fr"]
                if not ok:
                    log(f"traduction : extrait Hamidullah non littéral pour {k}, traduction complète gardée")
            cache["method"] = "claude -p"
    # formules rituelles connues : traduction fixe si le LLM est indisponible
    for i, s in ar:
        k = _seg_key(s["text"])
        fr = formula_fr(s["text"]) if k not in cache["seg"] else None
        if fr:
            cache["seg"][k] = {"fr": fr, "coran": False, "ar": s["text"], "fixe": True}
    for m in vms:
        cache["verse_fr"].setdefault(verse_key(m), m["verse_fr"])
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    missing = sum(1 for _, s in ar if _seg_key(s["text"]) not in cache["seg"])
    if missing:
        log(f"traduction : {missing} segment(s) arabes sans traduction (pas de sous-titre pour eux)")
    return cache
