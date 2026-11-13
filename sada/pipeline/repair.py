"""Passes de reprise d'une transcription : boucles de répétition, arabe rendu en lettres latines.

La décision — quel passage reprendre, et quand remplacer le texte — ne dépend pas du moteur.
Un `Engine` sait deux choses : extraire une plage d'audio et la retranscrire. Le local passe un
faster-whisper et un tableau numpy ; le distant, ffmpeg et une API compatible OpenAI. Tout le
reste est ici, partagé : c'est la même politique des deux côtés, seules les plages concernées
changent de main.

Un moteur expose :

    chunk(start, end)                 -> plage d'audio, ou None si elle est trop courte
    transcribe(chunk, *, language, temperatures, compression_ratio_threshold)
                                      -> [{"text", "avg_logprob", "start", "end", "words"}]
                                         (temps relatifs à la plage, mots `{"w", "s", "e"}`)
    detect_language(chunk)            -> ("ar", 0.9) ou None si le moteur ne sait pas

Les deux passes sont indépendantes et s'appliquent dans cet ordre : `redo_loops` (une boucle
recouvre souvent de la parole française : rien n'est imposé), puis `recover_arabic` (arabe forcé,
avec vérification que le résultat est bien de l'arabe).
"""

from __future__ import annotations

from typing import Protocol

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


class Engine(Protocol):
    """Ce qu'une passe de reprise demande au moteur de transcription."""

    def chunk(self, start: float, end: float): ...

    def transcribe(
        self,
        chunk,
        *,
        language: str | None = None,
        temperatures: tuple[float, ...] = (0.0,),
        compression_ratio_threshold: float | None = None,
    ) -> list[dict]: ...

    def detect_language(self, chunk) -> tuple[str, float] | None: ...


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
    seul écart de vraisemblance. Un segment n'est remplacé que si le texte obtenu est bien arabe et
    si l'arabe est plus probable que ce qui était écrit (`avg_logprob` supérieur de `margin`) —
    sauf translittération manifeste, où la langue ne fait aucun doute. Les bornes du segment sont
    conservées : le montage ne bouge pas.
    """
    arabic_sermon = language == "ar"
    ranges_ = ranges([seg for seg in segs if _candidate(seg, arabic_sermon=arabic_sermon)])
    replaced = tried = 0
    for start, end, members in ranges_:
        if tried >= max_ranges:
            break
        chunk = engine.chunk(start, end)
        if chunk is None:
            continue
        tried += 1
        if not any(_sure(seg) for seg in members):
            probe = engine.detect_language(chunk)
            if probe is not None and (probe[0] != "ar" or probe[1] < min_prob):
                continue
        try:
            pieces = engine.transcribe(chunk, language="ar", temperatures=(0.0,))
        except Exception as e:  # noqa: BLE001
            log(_("  Arabic retranscription impossible at {seconds:.1f}s: {error}", seconds=start, error=e))
            continue
        for seg, own in _split(pieces, members, start):
            text = pieces_text(own)
            if arabic_ratio(text) < 0.5:
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
                "Arabic passages retranscribed: {replaced} (out of {tried} passages examined)",
                replaced=replaced,
                tried=tried,
            )
        )
    return replaced


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
