"""Notes en Markdown : résumé (claude -p si dispo, sinon extractif), citations EXACTES uniquement.

Règle stricte : une citation n'est acceptée que si elle est une sous-chaîne littérale d'un segment du transcript
(vérifié en code, pas de confiance aveugle au LLM). Le résumé est toujours étiqueté « généré, à valider ».
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
from collections import Counter

from .arabic import arabic_ratio

STOP = set("le la les de des du un une et en à a au aux que qui qu ce cet cette ces pour par sur dans avec son sa ses leur leurs nous vous ils elles je tu il elle on ne pas plus se est sont être été avoir fait comme mais ou où donc car si très tout tous toute toutes aussi cela ça c d l n s j m y mon ma mes notre nos votre vos dit dire entre sans sous faire peut peuvent mes vos allah".split())


def fmt_t(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def find_quote(quote: str, segments: list[dict]) -> dict | None:
    """Renvoie le segment contenant littéralement la citation (espaces/casse normalisés), sinon None."""
    q = _norm_ws(quote).strip("«»\"' .…")
    if len(q) < 12:
        return None
    for s in segments:
        if q in _norm_ws(s["text"]):
            return s
    # citation à cheval sur deux segments consécutifs
    for a, b in zip(segments, segments[1:]):
        if q in _norm_ws(a["text"] + " " + b["text"]):
            return a
    return None


def extractive_summary(segments: list[dict], n_points: int = 6) -> dict:
    """Résumé extractif : phrases du transcript les mieux notées (fréquence des mots porteurs de sens)."""
    sents = []
    for s in segments:
        if arabic_ratio(s["text"]) > 0.5:
            continue
        for p in re.split(r"(?<=[.!?])\s+", s["text"]):
            if len(p.split()) >= 8:
                sents.append((s, p.strip()))
    words = Counter(w for _, p in sents for w in re.findall(r"[a-zàâçéèêëîïôûùüœ'-]{4,}", p.lower()) if w not in STOP)
    scored = sorted(sents, key=lambda sp: -sum(words[w] for w in set(re.findall(r"[a-zàâçéèêëîïôûùüœ'-]{4,}", sp[1].lower())) if w not in STOP) / (len(sp[1].split()) ** 0.7))
    pick = sorted(scored[:n_points], key=lambda sp: sp[0]["start"])
    return {"method": "extractif", "theme": "", "points": [p for _, p in pick], "rappels": []}


def llm_summary(segments: list[dict], matches: list[dict], model: str | None = None, timeout: int = 420, log=print) -> dict | None:
    claude = shutil.which("claude")
    if not claude:
        return None
    tr = "\n".join(f"[{fmt_t(s['start'])}] {s['text']}" for s in segments)
    vs = "\n".join(f"- {m['ref']} ({m['surah_name']}) vers {fmt_t(m['start'])}" for m in matches) or "(aucun)"
    prompt = (
        "Voici la transcription automatique (Whisper, avec erreurs possibles) d'une khutba du vendredi, avec horodatage.\n"
        "Produis UNIQUEMENT un objet JSON (sans texte autour) de la forme :\n"
        '{"theme": "une phrase", "points": ["4 à 7 points clés, reformulés brièvement"], '
        '"rappels": ["0 à 4 rappels pratiques pour les fidèles"], '
        '"citations": ["3 à 6 phrases marquantes de l\'imam, recopiées MOT POUR MOT depuis la transcription, en français, sans modifier un seul mot"]}\n'
        "Règles : ne rien inventer ; ne pas attribuer à l'imam ce qu'il n'a pas dit ; ne cite aucun verset ni hadith de mémoire ; "
        "les citations doivent être des extraits littéraux (ils seront vérifiés mécaniquement et supprimés sinon).\n\n"
        f"Versets repérés automatiquement (indicatif) :\n{vs}\n\nTRANSCRIPTION :\n{tr}\n"
    )
    cmd = [claude, "-p", "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            log(f"claude -p a échoué ({r.returncode}) : {r.stderr[:300]}")
            return None
        m = re.search(r"\{.*\}", r.stdout, re.S)
        d = json.loads(m.group(0)) if m else None
        if d:
            d["method"] = "claude -p"
        return d
    except Exception as e:  # noqa
        log(f"résumé LLM indisponible : {e}")
        return None


def build_notes(session_label: str, segments: list[dict], matches: list[dict], meta: dict, use_llm: bool = True, log=print) -> tuple[str, dict]:
    summ = llm_summary(segments, matches, log=log) if use_llm else None
    fallback = False
    if not summ:
        summ, fallback = extractive_summary(segments), True
    # citations vérifiées mécaniquement
    quotes = []
    rejected = 0
    for q in summ.get("citations", []) or []:
        seg = find_quote(q, segments)
        if seg:
            quotes.append((seg["start"], q.strip()))
        else:
            rejected += 1
    if not quotes:  # repli : phrases littérales du transcript
        ex = extractive_summary(segments, 4)
        for p in ex["points"]:
            seg = find_quote(p, segments)
            if seg:
                quotes.append((seg["start"], p))
    quotes.sort()
    L = [f"# Notes de khutba — {session_label}", "",
         f"> ⚠️ **Brouillon généré automatiquement — À VALIDER** par l'imam ou un responsable avant toute diffusion.",
         f"> Résumé : méthode **{summ.get('method')}**{' (repli : claude indisponible)' if fallback and use_llm else ''}. "
         f"Les citations ci-dessous sont des extraits littéraux du transcript ({len(quotes)} vérifiées, {rejected} rejetées car non littérales).", ""]
    L += ["## Informations", f"- Durée transcrite : {fmt_t(meta.get('duration', 0))}", f"- Transcription : {meta.get('model')} ({meta.get('device')}, {meta.get('compute_type')}), langue détectée : {meta.get('language')}",
          f"- Versets repérés : {len(matches)}", ""]
    if summ.get("theme"):
        L += ["## Thème (résumé généré)", summ["theme"], ""]
    L += ["## Points clés (résumé généré)"] + [f"- {p}" for p in summ.get("points", [])] + [""]
    if summ.get("rappels"):
        L += ["## Rappels pratiques (résumé généré)"] + [f"- {p}" for p in summ["rappels"]] + [""]
    L += ["## Citations exactes de l'imam (transcription automatique, horodatées)"]
    L += [f"- **[{fmt_t(t)}]** « {q} »" for t, q in quotes] or ["- (aucune citation vérifiable)"]
    L += ["", "## Versets détectés (correspondance floue — à vérifier)", "",
          "| Horodatage | Réf. | Sourate | Score | Couverture |", "|---|---|---|---|---|"]
    for m in matches:
        L.append(f"| {fmt_t(m['start'])}–{fmt_t(m['end'])} | **{m['ref']}** | {m['surah_name']} | {m['score']:.0f} | {int(m['coverage'] * 100)} % |")
    L.append("")
    for m in matches:
        L += [f"### {m['ref']} — {m['surah_name']} ({fmt_t(m['start'])})", f"- Entendu (Whisper) : {m['heard']}", f"- Texte du Coran (Tanzil/alquran.cloud, simple-clean) : {m['verse_text']}",
              f"- Traduction (Hamidullah) : {m['verse_fr']}", ""]
    return "\n".join(L), {"summary": summ, "quotes": quotes, "rejected_quotes": rejected}
