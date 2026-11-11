"""Markdown notes: summary (language model when available, extractive otherwise), EXACT quotes only.

Strict rule: a quote is accepted only if it is a literal substring of a transcript segment
(checked in code, no blind trust in the LLM). The summary is always labelled « generated, to be validated ».
"""
from __future__ import annotations
import re
from collections import Counter

from ..text import duration as fmt_t
from ..text import is_arabic
from . import llm
from .select import lighten_segments
from .verses import shown

STOP = set("le la les de des du un une et en à a au aux que qui qu ce cet cette ces pour par sur dans avec son sa ses leur leurs nous vous ils elles je tu il elle on ne pas plus se est sont être été avoir fait comme mais ou où donc car si très tout tous toute toutes aussi cela ça c d l n s j m y mon ma mes notre nos votre vos dit dire entre sans sous faire peut peuvent mes vos allah".split())
THEME = "## Thème (résumé généré)"


def theme_from_markdown(md: str) -> str:
    """Theme of a notes.md written by build_notes."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line == THEME:
            return next((x.strip()[:300] for x in lines[i + 1:] if x.strip()), "")
    return ""


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def find_quote(quote: str, segments: list[dict]) -> dict | None:
    """Return the segment literally containing the quote (whitespace/case normalised), otherwise None."""
    q = _norm_ws(quote).strip("«»\"' .…")
    if len(q) < 12:
        return None
    for s in segments:
        if q in _norm_ws(s["text"]):
            return s
    # quote spanning two consecutive segments
    for a, b in zip(segments, segments[1:]):
        if q in _norm_ws(a["text"] + " " + b["text"]):
            return a
    return None


def extractive_summary(segments: list[dict], n_points: int = 6) -> dict:
    """Extractive summary: the best-scored transcript sentences (frequency of meaning-bearing words)."""
    sents = []
    for s in segments:
        if is_arabic(s["text"]):
            continue
        for p in re.split(r"(?<=[.!?])\s+", s["text"]):
            if len(p.split()) >= 8:
                sents.append((s, p.strip()))
    words = Counter(w for _, p in sents for w in re.findall(r"[a-zàâçéèêëîïôûùüœ'-]{4,}", p.lower()) if w not in STOP)
    scored = sorted(sents, key=lambda sp: -sum(words[w] for w in set(re.findall(r"[a-zàâçéèêëîïôûùüœ'-]{4,}", sp[1].lower())) if w not in STOP) / (len(sp[1].split()) ** 0.7))
    pick = sorted(scored[:n_points], key=lambda sp: sp[0]["start"])
    return {"method": "extractif", "theme": "", "points": [p for _, p in pick], "rappels": []}


def llm_summary(segments: list[dict], matches: list[dict], timeout: int = 420, log=print) -> dict | None:
    # Les extraits ne passent plus par ce prompt : ils sont choisis séparément (sada.pipeline.select).
    # La transcription allégée (sans lignes arabes ni phonétique) réduit les tokens envoyés au modèle.
    tr = "\n".join(lighten_segments(segments))
    vs = "\n".join(f"- {m['ref']} ({m['surah_name']}) vers {fmt_t(m['start'])}" for m in matches) or "(aucun)"
    prompt = (
        "Voici la transcription automatique (Whisper, avec erreurs possibles) d'une khutba du vendredi, avec horodatage.\n"
        "Les passages en arabe et les citations phonétiques ont été retirés.\n"
        "Produis UNIQUEMENT un objet JSON (sans texte autour) de la forme :\n"
        '{"theme": "une phrase", "points": ["4 à 7 points clés, reformulés brièvement"], '
        '"rappels": ["0 à 4 rappels pratiques pour les fidèles"], '
        '"citations": ["3 à 6 phrases marquantes de l\'imam, recopiées MOT POUR MOT depuis la transcription, en français, sans modifier un seul mot"]}\n'
        "Règles : ne rien inventer ; ne pas attribuer à l'imam ce qu'il n'a pas dit ; ne cite aucun verset ni hadith de mémoire ; "
        "les citations doivent être des extraits littéraux (ils seront vérifiés mécaniquement et supprimés sinon).\n"
        f"Versets repérés automatiquement (indicatif) :\n{vs}\n\nTRANSCRIPTION :\n{tr}\n"
    )
    d = llm.ask_json(prompt, timeout, log)
    if d:
        d["method"] = llm.settings().label()
    return d


def build_notes(session_label: str, segments: list[dict], matches: list[dict], meta: dict, use_llm: bool = True, log=print) -> tuple[str, dict]:
    summ = llm_summary(segments, matches, log=log) if use_llm else None
    fallback = False
    if not summ:
        summ, fallback = extractive_summary(segments), True
    # quotes checked mechanically
    quotes = []
    rejected = 0
    for q in summ.get("citations", []) or []:
        seg = find_quote(q, segments)
        if seg:
            quotes.append((seg["start"], q.strip()))
        else:
            rejected += 1
    if not quotes:  # fallback: literal sentences from the transcript
        ex = extractive_summary(segments, 4)
        for p in ex["points"]:
            seg = find_quote(p, segments)
            if seg:
                quotes.append((seg["start"], p))
    quotes.sort()
    L = [f"# Notes de khutba — {session_label}", "",
         f"> ⚠️ **Brouillon généré automatiquement — À VALIDER** par l'imam ou un responsable avant toute diffusion.",
         f"> Résumé : méthode **{summ.get('method')}**{' (repli : modèle indisponible)' if fallback and use_llm else ''}. "
         f"Les citations ci-dessous sont des extraits littéraux du transcript ({len(quotes)} vérifiées, {rejected} rejetées car non littérales).", ""]
    firm = shown(matches)
    weak = [m for m in matches if m not in firm]
    L += ["## Informations", f"- Durée transcrite : {fmt_t(meta.get('duration', 0))}", f"- Transcription : {meta.get('model')} ({meta.get('device')}, {meta.get('compute_type')}), langue détectée : {meta.get('language')}",
          f"- Versets repérés : {len(firm)} (+ {len(weak)} formules/fragments ambigus)", ""]
    if summ.get("theme"):
        L += [THEME, summ["theme"], ""]
    L += ["## Points clés (résumé généré)"] + [f"- {p}" for p in summ.get("points", [])] + [""]
    if summ.get("rappels"):
        L += ["## Rappels pratiques (résumé généré)"] + [f"- {p}" for p in summ["rappels"]] + [""]
    L += ["## Citations exactes de l'imam (transcription automatique, horodatées)"]
    L += [f"- **[{fmt_t(t)}]** « {q} »" for t, q in quotes] or ["- (aucune citation vérifiable)"]
    L += ["", "## Versets détectés (correspondance floue — à vérifier)", "",
          "| Horodatage | Réf. | Sourate | Score | Couverture | Confiance |", "|---|---|---|---|---|---|"]
    for m in firm:
        L.append(f"| {fmt_t(m['start'])}–{fmt_t(m['end'])} | **{m['ref']}** | {m['surah_name']} | {m['score']:.0f} | {int(m['coverage'] * 100)} % | {m.get('confidence')} |")
    if weak:
        L += ["", "### Formules / fragments ambigus (ne PAS considérer comme des versets identifiés)", "",
              "| Horodatage | Entendu | Versets possibles | Type |", "|---|---|---|---|"]
        for m in weak:
            L.append(f"| {fmt_t(m['start'])} | {m['heard'][:60]} | {', '.join(m.get('candidates') or [m['ref']])} | {m.get('confidence')} |")
    L.append("")
    for m in firm:
        L += [f"### {m['ref']} — {m['surah_name']} ({fmt_t(m['start'])})", f"- Entendu (Whisper) : {m['heard']}", f"- Texte du Coran (Tanzil/alquran.cloud, simple-clean) : {m['verse_text']}",
              f"- Traduction (Hamidullah) : {m['verse_fr']}", ""]
    return "\n".join(L), {"summary": summ, "quotes": quotes, "rejected_quotes": rejected}
