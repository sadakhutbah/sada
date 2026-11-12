"""Sélection des extraits (clips verticaux) : le code génère les candidats, un petit modèle les note,
le code retient les N meilleurs.

Conception (décision 11/2026, remplace « un appel modèle pour tout le transcript qui rend des bornes ») :

1. Candidats générés par le code : fenêtres de 30 à 75 s glissant sur toute la séance, ouvertes sur
   un début de phrase propre (jamais « et / donc / parce que / mais », jamais en pleine phrase) et
   fermées sur une ponctuation forte, sans se terminer dans une récitation arabe. Filtres : français
   lisible (l'arabe phonétique est pénalisé via drop_translit_runs, les boucles via is_loop, les
   segments vides ou trop courts écartés). Dédoublonnage des chevauchements. Visée : 20 à 40 candidats.
2. Notation par le modèle : UN petit prompt par lot de 5 candidats au maximum, texte nettoyé du
   candidat seul (lignes arabes et suites phonétiques retirées), grille simple (autonome, idée forte
   ou récit avec une chute, émotion ou utilité, propreté), réponse JSON minuscule. Sortie malformée :
   repli sur la note heuristique.
3. Sélection finale par le code : les N meilleures notes, espacement minimal sur la durée, sans
   chevauchement.
4. Sans modèle (ou modèle indisponible) : la note heuristique seule décide.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from wordfreq import zipf_frequency

from ..i18n import _
from ..text import duration as fmt_t
from ..text import is_arabic, is_translit_word
from . import clips as clipmod
from . import llm
from .transcribe import is_loop

CAND_MIN, CAND_MAX = 30.0, 75.0
CAND_MAX_HARD = 90.0     # tolérance pour finir la phrase en cours (on ne coupe jamais un mot)
MAX_CANDIDATES = 40
BATCH = 5
MIN_SEP = 45.0            # espacement minimal entre deux extraits retenus (secondes)
SCORE_TIMEOUT = 240
END_PUNCT = (".", "!", "?", "؟", "»", "…")

# Mots qui ne doivent pas ouvrir un extrait : le clip doit se comprendre sans ce qui précède.
# Seules les conjonctions/connecteurs comptent : un pronom (« On le plonge. ») ou un article
# (« La patience… ») peut ouvrir une phrase autonome. Les continuations en minuscules sont déjà
# écartées par la règle de la majuscule dans _anchor_indexes.
CONNECTORS = frozenset(
    "et donc parce mais puis car or alors ensuite enfin bref voilà puisque lorsque quand comme sinon "
    "soit sauf cependant pourtant néanmoins aussi ou".split()
)

_WORD = re.compile(r"[a-zàâçéèêëîïôûùüœ'’-]+")


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\S+", text or "")]


def _ends_strong(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t[-1] in END_PUNCT


def _is_foreign(word: str) -> bool:
    """Mot ni français ni glose : phonétique arabe ou hors lexique (même règle que les sous-titres)."""
    bare = word.strip(".,;:!?«»\"()")
    if not bare:
        return False
    return is_translit_word(bare) or zipf_frequency(bare, "fr") < 2.3


def _starts_with_connector(text: str) -> bool:
    m = _WORD.search((text or "").lower())
    return bool(m) and m.group(0) in CONNECTORS


def clean_fr_text(text: str) -> str:
    """Français nettoyé : les suites d'arabe phonétique (>= 2 mots) sont retirées, les espaces réglés.

    Même règle que les sous-titres (clips.drop_translit_runs) : les mots isolés (« dunya », « fitna »)
    restent, une citation arabe recopiée en lettres latines disparaît (l'orateur la traduit juste après).
    """
    kept = clipmod.drop_translit_runs([{"w": w} for w in _words(text)], run=2)
    txt = " ".join(x["w"] for x in kept)
    txt = re.sub(r"\s+", " ", txt).strip()
    return re.sub(r"\s+([,.;:!?…])", r"\1", txt)


def lighten_segments(segments: list[dict]) -> list[str]:
    """Transcription allégée pour le résumé : lignes arabes retirées, phonétique retirée, horodatées."""
    out = []
    for s in segments:
        t = (s.get("text") or "").strip()
        if not t or is_arabic(t):
            continue
        t = clean_fr_text(t)
        if t:
            out.append(f"[{fmt_t(s['start'])}] {t}")
    return out


def _readable(text: str) -> bool:
    """Segment majoritairement du français lisible (ni boucle, ni suite phonétique dominante)."""
    if is_loop(text):
        return False
    ws = _words(text)
    if not ws:
        return False
    foreign = sum(1 for w in ws if _is_foreign(w))
    return foreign / len(ws) < 0.5


def _anchor_indexes(segments: list[dict]) -> list[int]:
    """Index des segments qui peuvent ouvrir un extrait : début de phrase propre, français lisible.

    Whisper met une majuscule en début de phrase (et coupe parfois le mot précédent en plein milieu) :
    un segment qui ouvre sur une majuscule est un début de phrase exploitable, même si le segment
    précédent ne se termine pas par une ponctuation. Un segment en minuscule (« ponger », « apri »)
    continue le mot ou la phrase précédente : on l'écarte. Après une récitation arabe, on tolère même
    une conjonction (« Et on ramène… ») : la récitation a refermé le propos.
    """
    idxs = []
    for i, s in enumerate(segments):
        t = (s.get("text") or "").strip()
        if not t or is_arabic(t) or len(t) < 12:
            continue
        if not _readable(t):
            continue
        prev = segments[i - 1] if i > 0 else None
        prev_ar = prev is not None and is_arabic((prev.get("text") or "").strip())
        c = t[0]
        if not (i == 0 or prev_ar or (c.isupper() and c.isascii() and c.isalpha())):
            continue
        if _starts_with_connector(t) and not prev_ar:
            continue
        idxs.append(i)
    return idxs


def _window_from(segments: list[dict], i: int, speech_end: float) -> tuple[float, float] | None:
    """Fenêtre [a, b] ouverte sur le segment i, fermée sur la première phrase française terminée
    à 30 s ou plus. On coupe aussi juste avant une récitation arabe (jamais en pleine récitation,
    même si la phrase française se termine par une virgule)."""
    a = segments[i]["start"]
    for j in range(i, len(segments)):
        s = segments[j]
        if s["start"] > speech_end:
            break
        t = (s.get("text") or "").strip()
        if is_arabic(t):
            continue
        dur = s["end"] - a
        if dur < CAND_MIN:
            continue
        if dur > CAND_MAX_HARD:
            return None
        if _ends_strong(t):
            return a, min(s["end"] + 0.3, speech_end)
        nxt = segments[j + 1] if j + 1 < len(segments) else None
        if nxt is not None and is_arabic((nxt.get("text") or "").strip()):
            return a, min(s["end"] + 0.3, speech_end)
    return None


def _french_words(segments: list[dict], a: float, b: float) -> list[str]:
    out = []
    for s in segments:
        if s["end"] <= a or s["start"] >= b:
            continue
        t = (s.get("text") or "").strip()
        if is_arabic(t):
            continue
        out.extend(_words(t))
    return out


def clean_window_text(segments: list[dict], a: float, b: float) -> str:
    """Texte du candidat seul, nettoyé (lignes arabes et suites phonétiques retirées)."""
    kept = clipmod.drop_translit_runs([{"w": w} for w in _french_words(segments, a, b)], run=2)
    txt = " ".join(x["w"] for x in kept)
    txt = re.sub(r"\s+", " ", txt).strip()
    return re.sub(r"\s+([,.;:!?…])", r"\1", txt)


def heuristic_note(cand: dict, segments: list[dict]) -> float:
    """Note 0-10 sans modèle : lisibilité du français APRÈS nettoyage (c'est ce que le modèle voit),
    durée dans la cible, pas de récitation qui noie l'extrait, prime à une question posée en ouverture.
    La suite phonétique est retirée par le nettoyage : elle ne doit donc pas faire baisser la note,
    seuls les mots étrangers isolés (que le nettoyage laisse passer) comptent en défaveur."""
    a, b = cand["start"], cand["end"]
    txt = clean_window_text(segments, a, b)
    cw = _words(txt)
    if not cw:
        return 0.0
    foreign = sum(1 for w in cw if _is_foreign(w))
    readability = 1.0 - foreign / len(cw)
    arabic_t, loops, nseg = 0.0, 0, 0
    for s in segments:
        if s["end"] <= a or s["start"] >= b:
            continue
        t = (s.get("text") or "").strip()
        ov = max(0.0, min(s["end"], b) - max(s["start"], a))
        nseg += 1
        if is_arabic(t):
            arabic_t += ov
        elif is_loop(t):
            loops += 1
    arabic_share = arabic_t / (b - a) if b > a else 1.0
    loop_share = loops / nseg if nseg else 0.0
    dur = b - a
    length_ok = max(0.0, 1.0 - abs(dur - 50.0) / 50.0)
    hook = 1.0 if any("?" in w for w in cw[:12]) else 0.0
    note = 10.0 * (0.45 * readability + 0.20 * length_ok + 0.15 * (1.0 - arabic_share)
                   + 0.05 * (1.0 - loop_share) + 0.15 * hook)
    return round(min(10.0, max(0.0, note)), 2)


def candidate_windows(segments: list[dict], duration: float, log=print) -> list[dict]:
    """Fenêtres candidates (30-75 s), dédoublonnées, 20 à 40, note heuristique calculée."""
    if not segments:
        return []
    speech_end = min(duration, segments[-1]["end"] + 0.8)
    anchors = _anchor_indexes(segments)
    wins: list[dict] = []
    for i in anchors:
        w = _window_from(segments, i, speech_end)
        if w:
            wins.append({"kind": "extrait", "start": round(w[0], 2), "end": round(w[1], 2),
                         "note": 0.0, "titre": "", "method": ""})
    # Dédoublonner les chevauchements : glissement chronologique, la première fenêtre d'une zone
    # garde le début du propos (le « pourquoi » d'introduction) plutôt qu'un morceau déjà engagé.
    wins.sort(key=lambda w: w["start"])
    dedup: list[dict] = []
    for w in wins:
        if dedup and w["start"] < dedup[-1]["end"] - 1.0:
            continue
        dedup.append(w)
    for w in dedup:
        w["note"] = heuristic_note(w, segments)
    if len(dedup) > MAX_CANDIDATES:
        dedup = sorted(dedup, key=lambda w: -w["note"])[:MAX_CANDIDATES]
        dedup.sort(key=lambda w: w["start"])
    log(_("clips: {generated} windows generated, {kept} candidates after deduplication",
          generated=len(wins), kept=len(dedup)))
    return dedup


def _score_prompt(texts: list[str]) -> str:
    blocks = "\n\n".join(f"EXTRAIT {k} :\n{t}" for k, t in enumerate(texts, 1))
    return (
        "Tu évalues des extraits candidats d'une khutba (transcription automatique, donc erreurs possibles).\n"
        "Pour CHAQUE extrait, juge sur cette grille :\n"
        " - autonome : compréhensible sans ce qui précède ;\n"
        " - idée forte, ou récit qui va au bout avec une chute ;\n"
        " - émotion ou utilité pratique pour l'auditeur ;\n"
        " - propreté : pas de charabia, pas de suite de mots sans sens.\n"
        "Note de 0 à 10 en étalant tes notes (la moyenne de la fournée doit être autour de 5) :\n"
        "  8-10 : récit mémorable ou idée forte avec une chute, émotion, utile ;\n"
        "  5-7 : correct et propre mais sans relief ;\n"
        "  <5 : confus, générique, répétitif ou charabia.\n"
        "Donne aussi un titre court (5 mots au plus) pour chaque extrait.\n"
        'Réponds UNIQUEMENT un objet JSON : {"extraits": [{"note": 7, "titre": "..."}, ...]}, '
        "un objet par extrait, dans le même ordre.\n\n" + blocks
    )


def _parse_scores(d: dict) -> list[tuple[float, str] | None] | None:
    items = d.get("extraits") if isinstance(d, dict) else None
    if not isinstance(items, list):
        return None
    out: list[tuple[float, str] | None] = []
    for e in items:
        if not isinstance(e, dict):
            out.append(None)
            continue
        raw = e.get("note")
        if raw is None:
            out.append(None)
            continue
        try:
            note = float(raw)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append((max(0.0, min(10.0, note)), str(e.get("titre") or "").strip()[:80]))
    return out


def _score_batch(texts: list[str], log) -> list[tuple[float, str] | None] | None:
    d = llm.ask_json(_score_prompt(texts), SCORE_TIMEOUT, log)
    return _parse_scores(d) if d else None


def score_candidates(cands: list[dict], segments: list[dict], use_llm: bool = True, log=print) -> list[dict]:
    """Note chaque candidat (modèle par lots de 5, sinon heuristique). Repli heuristique par candidat."""
    if not cands:
        return cands
    if not use_llm:
        for c in cands:
            c["note"] = heuristic_note(c, segments)
            c["titre"] = ""
            c["method"] = "heuristique"
        return cands
    batches = [cands[i:i + BATCH] for i in range(0, len(cands), BATCH)]
    texts = [[clean_window_text(segments, c["start"], c["end"]) for c in b] for b in batches]

    def run(batch_texts):
        try:
            return _score_batch(batch_texts, log)
        except Exception as e:  # noqa: BLE001 — un modèle lent/indisponible ne bloque pas le pipeline
            log(_("clips: batch not scored ({error})", error=e))
            return None

    results = []
    if len(batches) > 1:
        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(ex.map(run, texts))
    else:
        results = [run(t) for t in texts]

    scored = 0
    for batch_cands, batch_texts, res in zip(batches, texts, results, strict=False):
        parsed = res if isinstance(res, list) else [None] * len(batch_cands)
        for c, _t, pr in zip(batch_cands, batch_texts, parsed, strict=False):
            if pr is not None:
                c["note"], c["titre"] = pr
                c["method"] = llm.settings().label()
                scored += 1
            else:
                c["note"] = heuristic_note(c, segments)
                c["titre"] = ""
                c["method"] = "heuristique"
    log(_("clips: {scored}/{total} candidates scored by the model, the rest stays heuristic",
          scored=scored, total=len(cands)))
    return cands


def pick_clips(scored: list[dict], n: int, min_sep: float = MIN_SEP) -> list[dict]:
    """Les N meilleures notes, espacement minimal, sans chevauchement ; ordre chronologique."""
    chosen: list[dict] = []
    for c in sorted(scored, key=lambda c: -c["note"]):
        if len(chosen) >= n:
            break
        if any(c["start"] < o["end"] - 1.0 and c["end"] > o["start"] + 1.0 for o in chosen):
            continue
        if any(abs(c["start"] - o["start"]) < min_sep for o in chosen):
            continue
        chosen.append(c)
    chosen.sort(key=lambda c: c["start"])
    for c in chosen:
        c["why"] = c.get("titre") or f"extrait noté {c['note']:.0f}/10"
    return chosen


def select_clips(segments: list[dict], duration: float, n: int, use_llm: bool = True, log=print) -> list[dict]:
    """Chaîne complète : candidats -> notation -> sélection. Rend les fenêtres prêtes à monter."""
    cands = candidate_windows(segments, duration, log=log)
    if not cands:
        log(_("clips: no publishable candidate"))
        return []
    scored = score_candidates(cands, segments, use_llm=use_llm, log=log)
    wins = pick_clips(scored, n)
    for w in wins:
        log(_("clips: kept {start}→{end} score {note:.1f} “{why}”",
              start=fmt_t(w["start"]), end=fmt_t(w["end"]), note=w["note"], why=w["why"]))
    return wins
