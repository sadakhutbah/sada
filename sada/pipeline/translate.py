"""Translations behind the subtitles: Arabic speech→French, French speech→Arabic.

The clips are French only, but a subtitle track covers the WHOLE sermon in one language: the `fr`
track needs every Arabic passage in French, the `ar` track every French passage in Arabic. Both
directions share the same model, the same sober subtitle style and a cache on disk.

Rules (same in both directions):

- Detected verses (Quran): never translated by the LLM. The Arabic side uses the exact Quranic text
  (`verses.recited_arabic`), the French side the Hamidullah translation (`verses.json` /
  `verses_fr.py`). For the Arabic→French direction the LLM only picks, inside the Hamidullah
  translation, the EXCERPT matching the recited passage: it must be a literal substring (checked in
  code), otherwise the full translation is kept.
- Ritual formulas (ta'awwudh, basmala, shahada…): fixed French translations, no « verse card ».
- Other Arabic segments: language model, sober, faithful, short translation. A segment the LLM flags
  as an UNMATCHED Quranic quotation is not translated (nothing on screen rather than an improvised
  Quran translation).
- French segments: language model, standard Arabic, same style; the house glossary gives the model
  the Islamic terms it already knows.

Caches, on disk next to the session (never translated twice):

- `translations.json` (`{"seg": {segment_key: {"fr", "coran"}}, "verse_fr": …}`), Arabic→French;
- `translations_ar.json` (`{"seg": {segment_key: {"ar", "fr"}}}`), French→Arabic.

Read and edit these caches rather than the model's output: `segment_fr`, `verse_fr`, `segment_ar`,
`with_correction`.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ..i18n import _
from ..text import has_translit, is_arabic
from . import glossary as glossmod
from . import llm
from .repair import is_loop
from .verses import formula_fr, match_for, shown

#: segments per model call: a long sermon would otherwise overflow the context and the answer
BATCH = 40
#: segments per call for French→Arabic (Arabic is more verbose than French: shorter batches)
BATCH_AR = 24


def _seg_key(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:16]


def verse_key(m: dict) -> str:
    return f"{m['ref']}@{m['start']}"


def empty() -> dict:
    return {"seg": {}, "verse_fr": {}}


def empty_ar() -> dict:
    """French→Arabic cache, keyed on the French text as it was said."""
    return {"seg": {}}


def segment_fr(tr: dict, seg: dict) -> dict | None:
    """{"fr": translation, "coran": unmatched recitation} of the Arabic segment, or None."""
    return (tr.get("seg") or {}).get(_seg_key(seg["text"]))


def segment_ar(tr: dict, seg: dict) -> dict | None:
    """{"ar": translation} of the French segment, or None while it is not translated."""
    return (tr.get("seg") or {}).get(_seg_key(seg["text"]))


def verse_fr(tr: dict, m: dict) -> str:
    """Hamidullah translation to display for the verse: the recited excerpt when known, otherwise the whole verse."""
    return (tr.get("verse_fr") or {}).get(verse_key(m)) or m.get("verse_fr", "")


def with_correction(tr: dict, seg: dict, fr: str) -> dict:
    """Copy of the translations in which the segment has a human-corrected translation."""
    out = copy.deepcopy(tr)
    out.setdefault("seg", {})[_seg_key(seg["text"])] = {"fr": fr, "coran": False}
    return out


def needs_fr(text: str) -> bool:
    """Segment à faire traduire en français : arabe écrit, ou français mêlé d'arabe phonétique.

    Whisper écrit souvent une citation arabe en lettres latines (« dhahabu 'l-ladda ») : sans
    traduction elle partirait telle quelle dans la piste française. Une boucle de répétition
    (« shukr, shukr, shukr… ») est une hallucination : elle n'a rien à traduire.
    """
    text = str(text or "").strip()
    if not text or is_loop(text):
        return False
    return is_arabic(text) or has_translit(text)


def _chunks(todo: list, size: int) -> list[list]:
    """Model calls of `size` segments; one empty call when only the verses are left to do."""
    if not todo:
        return [[]]
    return [todo[i : i + size] for i in range(0, len(todo), size)]


def _prompt_fr(rows: list[tuple[int, dict]], verses: list[dict]) -> str:
    """Prompt: French subtitles of Arabic segments + the Hamidullah excerpt of each verse."""
    lines = "\n".join(f"{i}\t[{s['start']:.0f}s]\t{s['text']}" for i, s in rows)
    vlines = (
        "\n".join(f"{verse_key(m)}\tentendu : {m['heard']}\tHamidullah : {m['verse_fr']}" for m in verses) or "(aucun)"
    )
    return (
        "Tu prépares les sous-titres FRANÇAIS d'un court extrait vidéo d'une khutba du vendredi (sermon). "
        "Voici les segments en arabe transcrits automatiquement par Whisper (des erreurs de transcription sont possibles : "
        "corrige-les mentalement d'après le contexte, ex. « صورة الطلاق » = « سورة الطلاق »). Certains sont écrits en "
        "lettres latines (arabe phonétique, souvent mêlé de français, ex. « dhahabu 'l-ladda, wa baqiya 'l-ithm ») : "
        "rends-les ENTIÈREMENT en français, en traduisant l'arabe et en gardant le français tel qu'il a été dit.\n"
        "Rends UNIQUEMENT un objet JSON, sans texte autour :\n"
        '{"seg": {"<numéro>": {"fr": "traduction", "coran": false}}, "verse_fr": {"<clé>": "extrait"}}\n'
        "Règles pour seg :\n"
        "- une entrée pour CHAQUE numéro ; traduction française fidèle, naturelle, sobre et respectueuse, sans rien ajouter ;\n"
        "- concise (c'est un sous-titre) : garde le sens, évite les tournures lourdes ; pas d'emoji ; « Allah » pour الله ;\n"
        "- formules rituelles : traductions usuelles (ex. « que la paix et les bénédictions d'Allah soient sur lui ») ;\n"
        '- si le segment est une RÉCITATION du Coran (verset), mets "coran": true et une traduction quand même ;\n'
        "- un segment peut commencer ou finir au milieu d'une phrase : traduis seulement ce qu'il contient, pour que les traductions se suivent ;\n"
        "- jamais de commentaire sur la traduction ni de translittération dans la réponse : seulement le sous-titre.\n"
        "Règles pour verse_fr : pour chaque clé ci-dessous, recopie MOT POUR MOT le passage de la traduction Hamidullah "
        "qui correspond à la partie récitée (entendue) ; ce doit être une sous-chaîne exacte de la traduction fournie "
        "(sinon recopie-la entière).\n\n"
        f"VERSETS :\n{vlines}\n\nSEGMENTS (numéro, temps, texte) :\n{lines}\n"
    )


def _glossary_hint(texts: list[str]) -> str:
    """Islamic terms of the house glossary that this batch talks about, as a vocabulary for the model."""
    terms = glossmod.load()
    blob = f" {glossmod.normalize(' '.join(texts))} "
    picked = []
    for entry in terms:
        fr_words = [w for w in glossmod.normalize(entry.get("fr") or "").split() if len(w) >= 5]
        if f" {glossmod.normalize(entry['term'])} " in blob or any(f" {w} " in blob for w in fr_words):
            picked.append(f"- {entry['term']} : {entry['fr']}")
    return (
        "VOCABULAIRE (translittération, sens) — rends ces termes en arabe usuel plutôt qu'en translittération :\n"
        + "\n".join(picked)
        + "\n\n"
        if picked
        else ""
    )


def _prompt_ar(rows: list[tuple[int, dict]], hint: str) -> str:
    """Prompt: Arabic subtitles of French segments, standard and sober, same style as the French side."""
    lines = "\n".join(f"{i}\t[{s['start']:.0f}s]\t{s['text']}" for i, s in rows)
    return (
        "Tu prépares les sous-titres ARABES d'une khoutba (sermon) dont la transcription française est fournie.\n"
        "Rends UNIQUEMENT un objet JSON, sans texte autour :\n"
        '{"seg": {"<numéro>": {"ar": "traduction"}}}\n'
        "Règles :\n"
        "- une entrée pour CHAQUE numéro ; arabe standard simple, fidèle, naturel, sobre et respectueux, sans rien ajouter ;\n"
        "- concise (c'est un sous-titre) : garde le sens, évite les tournures lourdes ; pas d'emoji ;\n"
        "- « Allah » s'écrit الله ; les termes islamiques s'écrivent en arabe usuel, pas en translittération ;\n"
        "- garde les noms propres (personnes, lieux, savants) tels qu'ils sont connus en arabe ;\n"
        "- un segment peut commencer ou finir au milieu d'une phrase : traduis seulement ce qu'il contient, "
        "pour que les traductions se suivent.\n\n"
        f"{hint}"
        f"SEGMENTS (numéro, temps, texte) :\n{lines}\n"
    )


def _store_fr(cache: dict, rows: list[tuple[int, dict]], d: dict, log) -> None:
    """Keep what the model answered for this batch; a missing entry stays « to translate »."""
    for i, s in rows:
        e = (d.get("seg") or {}).get(str(i))
        if isinstance(e, str):
            e = {"fr": e, "coran": False}
        if e and e.get("fr"):
            cache["seg"][_seg_key(s["text"])] = {"fr": e["fr"].strip(), "coran": bool(e.get("coran")), "ar": s["text"]}


def _store_ar(cache: dict, rows: list[tuple[int, dict]], d: dict) -> None:
    """Keep what the model answered for this batch; a missing entry stays « to translate »."""
    for i, s in rows:
        e = (d.get("seg") or {}).get(str(i))
        if isinstance(e, str):
            e = {"ar": e}
        ar = (e or {}).get("ar") or ""
        if ar.strip():
            cache["seg"][_seg_key(s["text"])] = {"ar": ar.strip(), "fr": s["text"]}


def _store_verse_fr(cache: dict, verses: list[dict], d: dict, log) -> None:
    """Hamidullah excerpt for each verse: a literal substring of the reference, otherwise the whole verse."""
    for m in verses:
        key = verse_key(m)
        ex = ((d.get("verse_fr") or {}).get(key) or "").strip().strip('«»" ')
        ok = len(ex) >= 12 and ex.rstrip(".") in m["verse_fr"]
        cache["verse_fr"][key] = ex if ok else m["verse_fr"]
        if not ok:
            log(_("translation: Hamidullah excerpt not literal for {key}, full translation kept", key=key))


def translate_session(
    segments: list[dict],
    matches: list[dict],
    cache_path: str | Path,
    use_llm: bool = True,
    timeout: int = 600,
    log=print,
    batch: int = BATCH,
    progress=None,
) -> dict:
    """-> {"seg": {segment_key: {"fr": str, "coran": bool}}, "verse_fr": {ref@start: excerpt}}

    Every Arabic segment of the session is translated: a subtitle track covers the whole sermon,
    not only the windows kept for the clips. `batch` segments per model call; `progress(fraction)`
    is called after each call so a background task can show where it stands.
    """
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else empty()
    vms = shown(matches, ambiguous=True)
    ar = [(i, s) for i, s in enumerate(segments) if needs_fr(s["text"])]
    todo = [(i, s) for i, s in ar if _seg_key(s["text"]) not in cache["seg"]]
    vtodo = [m for m in vms if verse_key(m) not in cache["verse_fr"]]
    if (todo or vtodo) and use_llm:
        log(
            _(
                "translation: {segments} Arabic segment(s), {verses} verse(s) via {model}",
                segments=len(todo),
                verses=len(vtodo),
                model=llm.settings().label(),
            )
        )
        chunks = _chunks(todo, batch)
        for n, chunk in enumerate(chunks):
            d = llm.ask_json(_prompt_fr(chunk, vtodo if n == 0 else []), timeout, log)
            if not d:
                break  # model unavailable: what is known stays, the rest is retried next time
            _store_fr(cache, chunk, d, log)
            if n == 0 and vtodo:
                _store_verse_fr(cache, vtodo, d, log)
            if progress:
                progress((n + 1) / len(chunks))
        # Un index oublié par le modèle laisserait un trou dans la piste : un second appel, une
        # seule fois, sur les seuls passages restés sans traduction.
        reste = [(i, s) for i, s in todo if _seg_key(s["text"]) not in cache["seg"]]
        vreste = [m for m in vtodo if verse_key(m) not in cache["verse_fr"]]
        if reste or vreste:
            d = llm.ask_json(_prompt_fr(reste, vreste), timeout, log)
            if d:
                _store_fr(cache, reste, d, log)
                _store_verse_fr(cache, vreste, d, log)
    # known ritual formulas: fixed translation when the LLM is unavailable
    for _i, s in ar:
        key = _seg_key(s["text"])
        fr = formula_fr(s["text"]) if key not in cache["seg"] else None
        if fr:
            cache["seg"][key] = {"fr": fr, "coran": False, "ar": s["text"], "fixe": True}
    for m in vms:
        cache["verse_fr"].setdefault(verse_key(m), m["verse_fr"])
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    missing = sum(1 for _, s in ar if _seg_key(s["text"]) not in cache["seg"])
    if missing:
        log(_("translation: {count} Arabic segment(s) without translation (no subtitle for them)", count=missing))
    return cache


def translate_french(
    segments: list[dict],
    matches: list[dict],
    cache_path: str | Path,
    use_llm: bool = True,
    timeout: int = 600,
    log=print,
    batch: int = BATCH_AR,
    progress=None,
) -> dict:
    """-> {"seg": {segment_key: {"ar": str, "fr": str}}}: Arabic subtitles of the French speech.

    Verses are left to the exact Quranic text (`subtitles.units`): a model never translates them.
    Only the segments missing from the cache are sent, so running twice costs nothing.
    """
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else empty_ar()
    cache.setdefault("seg", {})
    vms = shown(matches, ambiguous=True)
    fr = [
        (i, s)
        for i, s in enumerate(segments)
        if str(s.get("text") or "").strip() and not is_arabic(s["text"]) and not match_for(s, vms)
    ]
    todo = [(i, s) for i, s in fr if _seg_key(s["text"]) not in cache["seg"]]
    if todo and use_llm:
        log(
            _(
                "translation: {segments} French segment(s) into Arabic via {model}",
                segments=len(todo),
                model=llm.settings().label(),
            )
        )
        chunks = _chunks(todo, batch)
        for n, chunk in enumerate(chunks):
            d = llm.ask_json(_prompt_ar(chunk, _glossary_hint([s["text"] for _, s in chunk])), timeout, log)
            if not d:
                break  # model unavailable: what is known stays, the rest is retried next time
            _store_ar(cache, chunk, d)
            if progress:
                progress((n + 1) / len(chunks))
        # Un index oublié par le modèle laisserait un trou dans la piste arabe : un second appel,
        # une seule fois, sur les seuls passages restés sans traduction.
        reste = [(i, s) for i, s in todo if _seg_key(s["text"]) not in cache["seg"]]
        if reste:
            d = llm.ask_json(_prompt_ar(reste, _glossary_hint([s["text"] for _, s in reste])), timeout, log)
            if d:
                _store_ar(cache, reste, d)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    missing = sum(1 for _, s in fr if _seg_key(s["text"]) not in cache["seg"])
    if missing:
        log(_("translation: {count} French segment(s) without Arabic translation", count=missing))
    return cache


def translate_tracks(
    segments: list[dict],
    matches: list[dict],
    fr_cache: str | Path,
    ar_cache: str | Path,
    use_llm: bool = True,
    timeout: int = 600,
    log=print,
    progress=None,
) -> tuple[dict, dict]:
    """Fill both caches so every track covers the whole sermon: (Arabic→French, French→Arabic).

    The pipeline runs this right after the transcription; the back-office runs it again for a session
    whose track is still incomplete (the caches make the second run cheap). `progress(fraction)` goes
    0 → 1 over the two directions.
    """

    def half(done: float):
        if not progress:
            return None
        callback = progress  # local alias: the closure below stays callable for the type checker
        return lambda frac: callback(round(done + frac / 2, 3))

    fr = translate_session(segments, matches, fr_cache, use_llm, timeout, log, progress=half(0.0))
    ar = translate_french(segments, matches, ar_cache, use_llm, timeout, log, progress=half(0.5))
    if progress:
        progress(1.0)
    return fr, ar
