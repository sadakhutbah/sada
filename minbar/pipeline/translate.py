"""Traduction française des segments ARABES pour les sous-titres (les clips sont en français seul).

- Versets détectés (Coran) : jamais traduits par le LLM ; on affiche la traduction Hamidullah (verses.json).
  Le LLM choisit seulement, dans la traduction Hamidullah, l'EXTRAIT correspondant au passage récité :
  il doit être une sous-chaîne littérale (vérifié en code), sinon on garde la traduction complète.
- Formules rituelles (ta'awwudh, basmala, shahada…) : traductions fixes, pas de « carte verset ».
- Autres segments arabes : `claude -p` (même LLM que notes.py), traduction sobre, fidèle, courte.
  Un segment que le LLM signale comme citation coranique NON appariée n'est pas traduit (rien à l'écran
  plutôt qu'une traduction du Coran improvisée).
Cache : translations.json (clé = texte du segment) ; relancer ne rappelle pas le LLM.
"""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from rapidfuzz import fuzz

from .arabic import arabic_ratio, normalize

# Formules courantes : ne pas les présenter comme un verset (la détection floue les rapproche de 16:98, 64:1…).
FORMULAS = {
    "اعوذ بالله من الشيطان الرجيم": "Je cherche protection auprès d'Allah contre Satan le banni.",
    "بسم الله الرحمن الرحيم": "Au nom d'Allah, le Tout Miséricordieux, le Très Miséricordieux.",
    "اشهد ان لا اله الا الله": "J'atteste qu'il n'y a de divinité qu'Allah,",
    "وحده لا شريك له": "Seul, sans associé.",
    "له الملك وله الحمد": "À Lui la royauté, à Lui la louange,",
    "وهو علي كل شيء قدير": "et Il est capable de toute chose.",
    "الحمد لله رب العالمين": "Louange à Allah, Seigneur des mondes.",
    "صلي الله عليه وسلم": "que la paix et les bénédictions d'Allah soient sur lui.",
}
_FORM_NORM = {normalize(k): v for k, v in FORMULAS.items()}


def is_formula(heard: str) -> bool:
    """Vrai si le texte entendu est (presque) entièrement fait de formules rituelles."""
    words = normalize(heard).split()
    if not words:
        return False
    hn = " ".join(words)
    used: set[str] = set()
    for f in _FORM_NORM:
        if fuzz.partial_ratio(f, hn) >= 85:
            used |= set(f.split())
    rest = [w for w in words if w not in used and not any(fuzz.ratio(w, u) >= 80 for u in used)]
    return len(rest) < 3


def verse_matches(matches: list[dict], ambiguous: bool = False) -> list[dict]:
    """Versets à montrer comme versets : confiance sûre/courte et pas une formule rituelle.

    ambiguous=True ajoute les passages coraniques certains mais communs à plusieurs versets
    (ex. « والله يعلم وأنتم لا تعلمون ») : carte « CORAN » sans référence (champ `ambiguous`).
    """
    out = []
    for m in matches:
        if is_formula(m.get("heard", "")):
            continue
        if m.get("confidence", "sûr") in ("sûr", "court"):
            out.append(m)
        elif ambiguous and m.get("confidence") == "ambigu" and m.get("score", 0) >= 95 and len(m.get("heard", "").split()) >= 3:
            out.append({**m, "ambiguous": True})
    return out


def match_for(seg: dict, vms: list[dict]) -> dict | None:
    mid = (seg["start"] + seg["end"]) / 2
    return next((m for m in vms if m["start"] - 0.3 <= mid <= m["end"] + 0.3), None)


def strip_quran_marks(t: str) -> str:
    """Retire les signes de pause / annotations coraniques (U+06D6–U+06ED) et normalise les espaces."""
    return re.sub(r"\s+", " ", re.sub(r"[\u06D6-\u06ED]", "", t)).strip()


def recited_arabic(m: dict) -> str:
    """Portion du texte coranique effectivement récitée (fenêtre de mots du verset la plus proche de l'entendu)."""
    vw = strip_quran_marks(m["verse_text"]).split()
    hn = normalize(m.get("heard", ""))
    k = len(hn.split())
    if not vw or k == 0 or k >= len(vw):
        return " ".join(vw)
    best = (0.0, 0, len(vw))
    for n in range(max(1, k - 2), min(len(vw), k + 2) + 1):
        for i in range(0, len(vw) - n + 1):
            sc = fuzz.ratio(normalize(" ".join(vw[i:i + n])), hn)
            if sc > best[0]:
                best = (sc, i, i + n)
    return " ".join(vw[best[1]:best[2]]) if best[0] >= 70 else " ".join(vw)


def _key(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:16]


def _claude(prompt: str, timeout: int, log) -> dict | None:
    exe = shutil.which("claude")
    if not exe:
        log("traduction : claude introuvable")
        return None
    try:
        r = subprocess.run([exe, "-p", "--output-format", "text"], input=prompt, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            log(f"traduction : claude -p a échoué ({r.returncode}) {r.stderr[:200]}")
            return None
        m = re.search(r"\{.*\}", r.stdout, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:  # noqa
        log(f"traduction indisponible : {e}")
        return None


def translate_session(segments: list[dict], matches: list[dict], cache_path: str | Path, use_llm: bool = True,
                      timeout: int = 600, log=print) -> dict:
    """-> {"seg": {clé_segment: {"fr": str, "coran": bool}}, "verse_fr": {ref@start: extrait}, "method": str}"""
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {"seg": {}, "verse_fr": {}, "method": ""}
    vms = verse_matches(matches, ambiguous=True)
    ar = [(i, s) for i, s in enumerate(segments) if arabic_ratio(s["text"]) > 0.5]
    todo = [(i, s) for i, s in ar if _key(s["text"]) not in cache["seg"]]
    vtodo = [m for m in vms if f"{m['ref']}@{m['start']}" not in cache["verse_fr"]]
    if (todo or vtodo) and use_llm:
        lines = "\n".join(f"{i}\t[{s['start']:.0f}s]\t{s['text']}" for i, s in ar)
        vlines = "\n".join(f"{m['ref']}@{m['start']}\tentendu : {m['heard']}\tHamidullah : {m['verse_fr']}" for m in vtodo) or "(aucun)"
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
        d = _claude(prompt, timeout, log)
        if d:
            for i, s in ar:
                e = (d.get("seg") or {}).get(str(i))
                if isinstance(e, str):
                    e = {"fr": e, "coran": False}
                if e and e.get("fr"):
                    cache["seg"][_key(s["text"])] = {"fr": e["fr"].strip(), "coran": bool(e.get("coran")), "ar": s["text"]}
            for m in vtodo:
                k = f"{m['ref']}@{m['start']}"
                ex = ((d.get("verse_fr") or {}).get(k) or "").strip().strip("«»\" ")
                ok = len(ex) >= 12 and ex.rstrip(".") in m["verse_fr"]
                cache["verse_fr"][k] = ex if ok else m["verse_fr"]
                if not ok:
                    log(f"traduction : extrait Hamidullah non littéral pour {k}, traduction complète gardée")
            cache["method"] = "claude -p"
    # formules rituelles connues : traduction fixe si le LLM est indisponible
    for i, s in ar:
        k = _key(s["text"])
        if k not in cache["seg"]:
            hn = normalize(s["text"])
            parts = [v for f, v in _FORM_NORM.items() if fuzz.partial_ratio(f, hn) >= 88]
            if parts and is_formula(s["text"]):
                cache["seg"][k] = {"fr": " ".join(parts), "coran": False, "ar": s["text"], "fixe": True}
    for m in vms:
        cache["verse_fr"].setdefault(f"{m['ref']}@{m['start']}", m["verse_fr"])
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    missing = sum(1 for _, s in ar if _key(s["text"]) not in cache["seg"])
    if missing:
        log(f"traduction : {missing} segment(s) arabes sans traduction (pas de sous-titre pour eux)")
    return cache


def fr_for(seg: dict, tr: dict) -> dict | None:
    return (tr.get("seg") or {}).get(_key(seg["text"]))
