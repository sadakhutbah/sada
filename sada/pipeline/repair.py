"""Passes de reprise d'une transcription : boucles, arabe translittéré, hallucinations écartées.

La décision — quel passage reprendre, et quand remplacer le texte — ne dépend pas du moteur.
Un `Engine` sait trois choses : extraire une plage d'audio, la retranscrire, et dire si elle est
muette. Le local passe un faster-whisper et un tableau numpy ; le distant, ffmpeg et une API
compatible OpenAI. Tout le reste est ici, partagé : c'est la même politique des deux côtés,
seules les plages concernées changent de main.

Un moteur expose :

    chunk(start, end)                 -> plage d'audio, ou None si elle est trop courte
    transcribe(chunk, *, language, temperatures, compression_ratio_threshold)
                                      -> [{"text", "avg_logprob", "start", "end", "words"}]
                                         (temps relatifs à la plage, mots `{"w", "s", "e"}`)
    detect_language(chunk)            -> ("ar", 0.9) ou None si le moteur ne sait pas
    silent(start, end)                -> True (plage muette), False (parole), None (inconnu)

Les deux passes sont indépendantes et s'appliquent dans cet ordre : `redo_loops` (une boucle
recouvre souvent de la parole française : rien n'est imposé), puis `recover_arabic` (arabe forcé,
avec vérification que le résultat est bien de l'arabe).

Sur du silence, Whisper n'écrit pas du vide : il recopie des génériques de sous-titres
(« ترجمة نانسي قنقر », « Sous-titres réalisés par… »). `without_hallucinations` les écarte de la
transcription initiale, et aucune reprise ne peut les réintroduire (`invented`) : une reprise
hallucinée n'est jamais acceptée, le segment d'origine est gardé.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Protocol

from ..i18n import _
from ..text import arabic_ratio
from .language import TRANSLIT_SURE, translit_ratio

SUSPECT_LOGPROB = -0.75  # en dessous : le moteur doute, souvent une citation arabe rendue en français
DETECT_MIN_PROB = 0.55  # probabilité minimale pour croire `detect_language` quand il dit « ar »
RETRY_MARGIN = 0.05  # l'arabe doit être plus probable que le français de ce petit écart
TRANSLIT_SUSPECT = 0.35  # part des mots en arabe translittéré : passage à retranscrire
RETRY_MAX = 40  # plafond de plages reprises par session (coût borné : CPU local, minutes d'API)
MERGE_GAP_S = 6.0  # deux passages suspects séparés de moins que ça forment une seule plage
MAX_RANGE_S = 180.0  # une plage plus longue est découpée : un envoi reste court
LOOP_RETRY_MAX = 10  # plafond de boucles reprises (une boucle est rare, mais coûteuse à distance)

NO_SPEECH_MAX = 0.6  # seuil de Whisper : au-delà, la fenêtre est jugée sans parole
COMPRESSION_MAX = 2.4  # au-delà, le texte se répète (boucle) au lieu de transcrire
COMPRESSION_MIN_CHARS = 20  # en dessous, le taux de compression ne veut rien dire
SILENT_DB = -50.0  # seuil de niveau : en dessous, `silencedetect` ne trouve pas de voix


class Engine(Protocol):
    """Ce qu'une passe de reprise demande au moteur de transcription."""

    def chunk(self, start: float, end: float) -> Any: ...

    def transcribe(
        self,
        chunk,
        *,
        language: str | None = None,
        temperatures: tuple[float, ...] = (0.0,),
        compression_ratio_threshold: float | None = None,
    ) -> list[dict]: ...

    def detect_language(self, chunk) -> tuple[str, float] | None: ...

    def silent(self, start: float, end: float) -> bool | None: ...


# --------------------------------------------------------------------------- hallucinations

# Formules que Whisper recopie quand il n'entend rien (silence, musique, générique de fin) :
# elles viennent du corpus d'entraînement, pas de la khutba. Comparées après normalisation
# (diacritiques, casse, ponctuation), « اشتركوا في القناة » et « اشتركوا في القناه » sont la
# même formule. On n'y met que des formules entières : « abonnez-vous » ou « merci » se disent
# aussi dans un discours.
BOILERPLATE = (
    # arabe : génériques de sous-titres et appels à s'abonner
    "ترجمة نانسي قنقر",
    "ترجمة نانسي",
    "اشتركوا في القناة",
    "اشترك في القناة",
    "اشتركوا في قناتنا",
    "لا تنسوا الاشتراك في القناة",
    "شكرا على المشاهدة",
    "شكرا لكم على المشاهدة",
    # français : Amara et formules de fin de vidéo
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titres réalisés par amara.org",
    "sous-titres par amara.org",
    "amara.org",
    "merci d'avoir regardé",
    "merci d'avoir regardé cette vidéo",
    "abonnez-vous à la chaîne",
    # anglais
    "thanks for watching",
    "thank you for watching",
    "please subscribe to the channel",
    "subscribe to the channel",
    "subscribe to my channel",
    "subtitles by the amara.org community",
)

_AR_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_NOT_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalise(text: str) -> str:
    """Texte comparable : sans diacritiques, sans ponctuation, en minuscules.

    Les variantes d'écriture sont ramenées à une forme unique (`أ` -> `ا`, `ة` -> `ه`), ce qui fait
    tomber les graphies d'une même formule de générique sur la même chaîne.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = _AR_MARKS.sub("", plain).translate(_FOLD).casefold()
    return _NOT_WORD.sub(" ", plain).strip()


_BOILERPLATE = tuple(normalise(phrase) for phrase in BOILERPLATE)


def is_boilerplate(text: str) -> bool:
    """Le texte est (ou contient) une formule de générique connue de Whisper."""
    normalised = normalise(text)
    if not normalised:
        return False
    return any(phrase in normalised for phrase in _BOILERPLATE)


def _no_speech(seg: dict) -> bool:
    """`no_speech_prob` renvoyé par l'API : Whisper n'entend pas de parole dans la fenêtre."""
    probability = seg.get("no_speech_prob")
    return probability is not None and float(probability) >= NO_SPEECH_MAX


def _compressed(seg: dict) -> bool:
    """`compression_ratio` : le texte se répète, c'est une boucle et non une phrase."""
    ratio = seg.get("compression_ratio")
    text = (seg.get("text") or "").strip()
    return ratio is not None and len(text) >= COMPRESSION_MIN_CHARS and float(ratio) >= COMPRESSION_MAX


def invented(seg: dict, engine: Engine | None = None) -> bool:
    """Le texte de ce segment a-t-il été inventé plutôt que transcrit ?

    Trois témoins : une formule de générique, une fenêtre que la mesure dit muette, ou les
    scores du fournisseur (`no_speech_prob`, `compression_ratio`). Un moteur qui ne sait pas
    mesurer une plage (`silent` -> None) ne fait pas pencher la décision.
    """
    if is_boilerplate(seg.get("text") or "") or _no_speech(seg) or _compressed(seg):
        return True
    if engine is None:
        return False
    try:
        return engine.silent(float(seg.get("start", 0.0)), float(seg.get("end", 0.0))) is True
    except Exception:  # noqa: BLE001 — une mesure ratée ne doit pas éliminer un segment
        return False


def without_hallucinations(segs: list[dict], *, engine: Engine | None = None, log=print) -> list[dict]:
    """Écarte de la transcription ce que le moteur a inventé (voir `invented`).

    Un générique sur 30 s de silence ne doit ni partir en sous-titres ni être proposé à une
    reprise : le retirer est la seule façon de ne pas le voir revenir.
    """
    kept: list[dict] = []
    for seg in segs:
        if invented(seg, engine):
            log(
                _(
                    "  hallucination discarded at {seconds:.1f}s: {heard}",
                    seconds=float(seg.get("start", 0.0)),
                    heard=(seg.get("text") or "")[:60],
                )
            )
            continue
        kept.append(seg)
    return kept


# --------------------------------------------------------------------------- signaux


def is_loop(text: str) -> bool:
    """Boucle de répétition (« al-kufr, al-kufr, al-kufr… ») : un mot recopié sur tout le segment."""
    words = [w.strip('.,;:!?«»"()').lower() for w in (text or "").split()]
    words = [w for w in words if w]
    return len(words) >= 5 and len(set(words)) / len(words) <= 0.3


def looks_suspect(seg: dict) -> bool:
    """Segment français sur le papier, mais probablement un passage arabe mal transcrit.

    Un Whisper multilingue décide de la langue de chaque fenêtre de 30 s : une citation du Coran
    dans une khutba française s'écrit en français incohérent (« ce qu'on appelle aran »). Trois
    signaux peu coûteux : texte déjà arabe (rien à faire), probabilité moyenne basse, ou longue
    plage avec très peu de mots. Seuls les segments qui passent ce filtre paient une détection.
    """
    text = (seg.get("text") or "").strip()
    if not text or arabic_ratio(text) > 0.3 or is_loop(text):
        return False
    if (seg.get("end", 0.0) - seg.get("start", 0.0)) < 0.6:
        return False
    if len(text.split()) >= 3 and translit_ratio(text) >= TRANSLIT_SUSPECT:
        return True
    lp = seg.get("avg_logprob")
    if lp is None or lp < SUSPECT_LOGPROB:
        return True
    words = text.split()
    return (float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))) >= 3.0 and len(words) <= 4


def _sure(seg: dict) -> bool:
    """Arabe écrit en lettres latines : la langue est connue, ni détection ni écart de probabilité."""
    text = (seg.get("text") or "").strip()
    return len(text.split()) >= 3 and arabic_ratio(text) <= 0.5 and translit_ratio(text) >= TRANSLIT_SURE


def _silent(engine: Engine, seg: dict) -> bool:
    """Plage mesurée muette par le moteur (`None` : il ne sait pas, la reprise reste possible)."""
    try:
        return engine.silent(float(seg.get("start", 0.0)), float(seg.get("end", 0.0))) is True
    except Exception:  # noqa: BLE001 — une mesure ratée ne doit pas empêcher la reprise
        return False


def _thin(seg: dict) -> bool:
    """Longue plage presque sans mots : le moteur a perdu la parole (ou hallucine un générique)."""
    text = (seg.get("text") or "").strip()
    return arabic_ratio(text) <= 0.3 and (seg.get("end", 0.0) - seg.get("start", 0.0)) >= 3.0 and len(text.split()) <= 4


def _candidate(seg: dict, *, arabic_sermon: bool) -> bool:
    """Ce segment mérite-t-il une reprise en arabe forcé ?

    Dans un discours arabe, seuls les passages qui ne sont pas déjà en écriture arabe sont
    candidats : le reste est correct. Dans un discours français, tous les passages suspects le
    sont (citations du Coran rendues en français incohérent).
    """
    text = (seg.get("text") or "").strip()
    if not text or is_loop(text) or arabic_ratio(text) > 0.95:
        return False
    words = text.split()
    if len(words) >= 3 and arabic_ratio(text) <= 0.9 and translit_ratio(text) >= TRANSLIT_SURE:
        return True
    # deux écritures dans le même segment : la coupure a mélangé l'arabe et sa translittération
    if len(words) >= 4 and 0.3 < arabic_ratio(text) < 0.95 and translit_ratio(text) >= 0.15:
        return True
    if looks_suspect(seg):
        return True
    return arabic_sermon and _thin(seg)


# --------------------------------------------------------------------------- plages


def ranges(segs: list[dict], *, gap: float = MERGE_GAP_S, longest: float = MAX_RANGE_S) -> list[tuple]:
    """Regroupe des passages en plages d'audio : (début, fin, membres), dans l'ordre du temps.

    Une plage est un seul envoi au moteur : deux passages voisins sont transcrits ensemble, avec
    le contexte qui manquait au segment isolé (c'est ce qui remet « al-talaq, haytu yaqul » à sa
    place), et une plage trop longue est coupée pour que l'envoi reste court.
    """
    out: list[tuple[float, float, list[dict]]] = []
    for seg in sorted(segs, key=lambda s: float(s["start"])):
        start, end = float(seg["start"]), float(seg["end"])
        if out and start - out[-1][1] <= gap and max(end, out[-1][1]) - out[-1][0] <= longest:
            previous_start, previous_end, members = out[-1]
            members.append(seg)
            out[-1] = (previous_start, max(previous_end, end), members)
        else:
            out.append((start, end, [seg]))
    return out


# --------------------------------------------------------------------------- reprises


def redo_loops(engine: Engine, segs: list[dict], *, log=print, max_segments: int | None = None) -> int:
    """Retranscrit sans langue imposée les segments réduits à une boucle de répétition.

    Ces boucles recouvrent souvent de la parole française : les forcer en arabe ferait traduire le
    français en arabe. Le segment est remplacé seulement si la nouvelle version n'est plus une boucle.
    """
    done = 0
    for seg in segs:
        if max_segments is not None and done >= max_segments:
            break
        if not is_loop(seg.get("text") or ""):
            continue
        chunk = engine.chunk(float(seg["start"]), float(seg["end"]))
        if chunk is None:
            continue
        try:
            pieces = engine.transcribe(
                chunk, language=None, temperatures=(0.2, 0.4, 0.6), compression_ratio_threshold=2.0
            )
        except Exception as e:  # noqa: BLE001 — une reprise ratée ne doit pas perdre la transcription
            log(_("  retry impossible at {seconds:.1f}s: {error}", seconds=seg["start"], error=e))
            continue
        text = pieces_text(pieces)
        if not text or is_loop(text):
            continue
        seg.update(text=text, avg_logprob=round(pieces_logprob(pieces), 3), recovered="loop")
        words = pieces_words(pieces, float(seg["start"]))
        if words:
            seg["words"] = words
        done += 1
        log(_("  loop recovered at {seconds:.1f}s: {heard}", seconds=seg["start"], heard=text[:60]))
    return done


def recover_arabic(
    engine: Engine,
    segs: list[dict],
    *,
    log=print,
    max_ranges: int = RETRY_MAX,
    min_prob: float = DETECT_MIN_PROB,
    margin: float = RETRY_MARGIN,
    language: str = "",
) -> int:
    """Retranscrit en arabe forcé les passages que le moteur a rendus autrement.

    Les passages candidats sont groupés en plages (`ranges`) : un seul envoi par plage, avec le
    contexte. Un moteur qui sait détecter la langue (`detect_language`) filtre d'abord les plages
    dont il n'est pas sûr qu'elles soient arabes ; une API qui ne sait pas (Groq) s'en remet au
    seul écart de vraisemblance. Un segment n'est remplacé que si le texte obtenu est bien arabe,
    s'il n'est pas une hallucination (`invented`) et si l'arabe est plus probable que ce qui était
    écrit (`avg_logprob` supérieur de `margin`) — sauf translittération manifeste, où la langue ne
    fait aucun doute. Les bornes du segment sont conservées : le montage ne bouge pas.

    Un passage dont l'audio est muet n'est jamais envoyé : c'est là que Whisper invente des
    génériques, et les forcer en arabe ne rend que d'autres génériques. Le journal compte les
    segments examinés et les plages, dans la même unité que les remplacements.
    """
    arabic_sermon = language == "ar"
    candidates: list[dict] = []
    for seg in segs:
        if invented(seg) or not _candidate(seg, arabic_sermon=arabic_sermon):
            continue
        if _silent(engine, seg):
            log(_("  silence: no retry at {seconds:.1f}s", seconds=float(seg["start"])))
            continue
        candidates.append(seg)
    ranges_ = [(start, end, _spanning(segs, start, end, members)) for start, end, members in ranges(candidates)]
    replaced = examined = sent = 0
    for start, end, members in ranges_:
        if sent >= max_ranges:
            break
        chunk = engine.chunk(start, end)
        if chunk is None:
            continue
        sent += 1
        if not any(_sure(seg) for seg in members):
            probe = engine.detect_language(chunk)
            if probe is not None and (probe[0] != "ar" or probe[1] < min_prob):
                continue
        try:
            pieces = engine.transcribe(chunk, language="ar", temperatures=(0.0,))
        except Exception as e:  # noqa: BLE001
            log(_("  Arabic retranscription impossible at {seconds:.1f}s: {error}", seconds=start, error=e))
            continue
        examined += len(members)
        for seg, own in _split(pieces, members, start):
            text = pieces_text(own)
            if arabic_ratio(text) < 0.5:
                continue
            if any(invented(piece) for piece in own):
                log(
                    _(
                        "  hallucination discarded at {seconds:.1f}s: {heard}",
                        seconds=float(seg["start"]),
                        heard=text[:60],
                    )
                )
                continue
            heard = seg.get("avg_logprob")
            heard = -9.0 if heard is None else float(heard)
            if not _sure(seg) and pieces_logprob(own) <= heard + margin:
                continue
            seg.update(text=text, avg_logprob=round(pieces_logprob(own), 3), recovered="ar")
            words = pieces_words(own, start)
            if words:
                seg["words"] = words
            replaced += 1
            log(_("  Arabic passage recovered at {seconds:.1f}s: {heard}", seconds=seg["start"], heard=text[:60]))
    if replaced:
        log(
            _(
                "Arabic passages retranscribed: {replaced} of {examined} segments, in {ranges} range(s)",
                replaced=replaced,
                examined=examined,
                ranges=sent,
            )
        )
    return replaced


def _spanning(segs: list[dict], start: float, end: float, members: list[dict]) -> list[dict]:
    """Complète une plage avec les segments qu'elle couvre sans qu'ils soient candidats.

    La plage d'audio est continue : le texte rendu la couvre entièrement. Un segment non candidat
    pris dedans (une phrase arabe déjà correcte entre deux passages suspects) doit donc recevoir
    aussi sa part du nouveau texte, sinon le morceau qui lui revient n'a aucun destinataire et le
    texte de la plage ne correspond plus à ce qui est écrit. Les segments inventés par le moteur
    restent dehors : ils ne sont pas de la parole.
    """
    known = {id(seg) for seg in members}
    inside = [
        seg
        for seg in segs
        if id(seg) not in known
        and float(seg["start"]) >= start - 0.01
        and float(seg["end"]) <= end + 0.01
        and not invented(seg)
    ]
    return sorted(members + inside, key=lambda seg: float(seg["start"]))


def _split(pieces: list[dict], members: list[dict], offset: float = 0.0) -> list[tuple[dict, list[dict]]]:
    """Rend à chaque segment d'origine les morceaux de la plage qui lui reviennent (plus grand recouvrement).

    Les morceaux arrivent au temps de la plage (`offset`) ; les segments, au temps de la session.
    """
    own: list[list[dict]] = [[] for _member in members]
    for piece in pieces:
        a, b = float(piece.get("start", 0.0)) + offset, float(piece.get("end", 0.0)) + offset
        best, best_overlap = None, 0.0
        for i, seg in enumerate(members):
            overlap = min(b, float(seg["end"])) - max(a, float(seg["start"]))
            if overlap > best_overlap:
                best, best_overlap = i, overlap
        if best is not None:
            own[best].append(piece)
    return [(seg, group) for seg, group in zip(members, own, strict=True) if group]


# --------------------------------------------------------------------------- morceaux du moteur


def pieces_text(pieces: list[dict]) -> str:
    return " ".join((p.get("text") or "").strip() for p in pieces if (p.get("text") or "").strip())


def pieces_logprob(pieces: list[dict]) -> float:
    """Moyenne pondérée par la durée : une longue phrase arabe pèse plus qu'un mot isolé."""
    total = sum(max(0.01, float(p.get("end", 0.0)) - float(p.get("start", 0.0))) for p in pieces)
    if not total:
        return -9.0
    return (
        sum(
            float(p.get("avg_logprob") or -9.0) * max(0.01, float(p.get("end", 0.0)) - float(p.get("start", 0.0)))
            for p in pieces
        )
        / total
    )


def pieces_words(pieces: list[dict], offset: float) -> list[dict]:
    """Mots horodatés de la plage, ramenés au temps de la session."""
    return [
        {"w": word["w"], "s": round(float(word["s"]) + offset, 2), "e": round(float(word["e"]) + offset, 2)}
        for piece in pieces
        for word in piece.get("words") or []
        if (word.get("w") or "").strip()
    ]
