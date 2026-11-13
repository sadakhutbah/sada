"""SRT and WebVTT subtitles of the long-form video: one file per language, the whole sermon in each.

YouTube takes SRT (or WebVTT), never the ASS burned into the clips: these files are plain text built
from the timestamped segments the pipeline already produced (`transcript.json`, `translations.json`,
`translations_ar.json`, `verses.json`), with the corrections made in the back-office. Nothing is
written into the picture.

A track of language X covers the WHOLE sermon said in X: what was said in X as it was said, and
everything said in the other language translated into X. `ar` = Arabic speech + French speech
translated into Arabic; `fr` = French speech + Arabic speech translated into French. A passage
whose translation is not known yet is left out (never approximated) and the track is reported as
incomplete (`plan`), so the back-office can show « translation running » instead of a finished file.

The same cues are served as SRT (to upload to YouTube) and as WebVTT (read in place by the player's
`<track>`), so both files stay aligned on the same transcript.

Two shifts move the cues from session time to the file's time:

- the cut: when the session was taken out of a longer recording, the video starts at the session's
  first second (`source()` in the back-office);
- the opening card: the branded video prepends `intro_s` seconds before the session (`habillage.py`).

`shift = opening card - cut` gives the file aligned on the branded video, `shift = -cut` the one
aligned on the original video. Negative cues are dropped, never shown at 00:00:00 by mistake.

Readability: 42 characters per line at most, two lines per cue, cues cut on silences and strong
punctuation, no cue longer than 7 s. Text stays UTF-8, as read from the segments.
"""

from __future__ import annotations

import re

from ..text import is_arabic
from . import clips as clipmod
from .translate import segment_ar, verse_fr
from .verses import recited_arabic

#: a subtitle line holds about 42 characters (YouTube's own guidance), two lines per cue
WIDTH = 42
LINES = 2
#: a cue stays on screen long enough to be read, never long enough to linger over a silence
MIN_DURATION = 0.9
MAX_DURATION = 7.0
#: breath between two cues, and the silence that ends a cue on its own
GAP = 0.08
PAUSE = 0.7
#: words that should not end a line when they can move to the next one
WEAK = frozenset(
    "a au aux avec ce ces dans de des du elle en et eux il ils je la le les leur lui ma mais me mes "
    "moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu "
    "un une vos votre vous y".split()
)


# ------------------------------------------------------------------ format


def timestamp(t: float) -> str:
    """SRT timecode `HH:MM:SS,mmm` (hours never wrap: a two-hour lesson keeps 02:…)."""
    ms = int(round(max(0.0, t) * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def timestamp_vtt(t: float) -> str:
    """WebVTT timecode `HH:MM:SS.mmm`: same clock as SRT, a dot instead of the comma."""
    return timestamp(t).replace(",", ".")


def _rtl(line: str) -> str:
    """An Arabic line between bidi embedding marks, so the player lays it out right to left.

    WebVTT has no direction setting: the cue text itself carries it, and browsers that ignore the
    marks still read the text as is (the marks are invisible)."""
    return f"\u202b{line}\u202c" if is_arabic(line) else line


def _clean(text: str) -> str:
    """One line of text: no control character, no subtitle markup (`<i>` is read by the players)."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("<", "").replace(">", "")


def _split_words(text: str, start: float, end: float) -> list[dict]:
    """Words with times interpolated pro rata of their length (no word timestamp for this text)."""
    words = _clean(text).split()
    if not words or end <= start:
        return []
    weights = [len(word) + 1.5 for word in words]
    total = sum(weights)
    out, t = [], start
    for word, weight in zip(words, weights, strict=False):
        length = (end - start) * weight / total
        out.append({"w": word, "s": t, "e": t + length})
        t += length
    return out


def _words(passage: dict) -> list[dict]:
    """Words of a passage: Whisper's own timestamps when they belong to this text, else interpolated."""
    words = passage.get("words")
    if words:
        start, end = float(passage["start"]), float(passage["end"])
        return [
            {
                "w": str(word["w"]).strip(),
                "s": min(max(float(word["s"]), start), end),
                "e": min(max(float(word["e"]), start), end),
            }
            for word in words
            if str(word.get("w") or "").strip()
        ]
    return _split_words(passage["text"], float(passage["start"]), float(passage["end"]))


# ------------------------------------------------------------------ lines and cues


def _break(words: list[dict]) -> int | None:
    """Index of the first word of the second line, or None when one line is enough / two cannot hold."""
    if len(words) < 2:
        return None
    if len(" ".join(word["w"] for word in words)) <= WIDTH:
        return None
    best = None
    for cut in range(1, len(words)):
        first = " ".join(word["w"] for word in words[:cut])
        second = " ".join(word["w"] for word in words[cut:])
        if len(first) > WIDTH or len(second) > WIDTH:
            continue
        penalty = abs(len(first) - len(second))
        if words[cut - 1]["w"][-1:] in ",;:.!?…":
            penalty -= 8
        if words[cut - 1]["w"].lower().strip('.,;:!?«»"()') in WEAK:
            penalty += 6
        if best is None or penalty < best[0]:
            best = (penalty, cut)
    return best[1] if best else None


def _fits(words: list[dict]) -> bool:
    """True when `words` holds in one or two lines of WIDTH characters."""
    return len(" ".join(word["w"] for word in words)) <= WIDTH or _break(words) is not None


def lines_of(words: list[dict]) -> list[str]:
    """One line when short, otherwise two balanced lines that each hold in WIDTH characters."""
    cut = _break(words)
    if cut is None:
        return [" ".join(word["w"] for word in words)]
    return [" ".join(word["w"] for word in words[:cut]), " ".join(word["w"] for word in words[cut:])]


def _pages(words: list[dict]) -> list[list[dict]]:
    """Cuts a segment into cues: two lines at most, on a silence or a strong punctuation when possible."""
    pages: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current:
            trial = current + [word]
            text = " ".join(item["w"] for item in current)
            long_pause = word["s"] - current[-1]["e"] > PAUSE
            full_stop = text[-1:] in ".!?;:…" and len(text) >= 12
            comma = text[-1:] == "," and len(text) >= 25
            too_long = trial[-1]["e"] - trial[0]["s"] > MAX_DURATION
            if not _fits(trial) or long_pause or full_stop or comma or too_long:
                pages.append(current)
                current = []
        current.append(word)
    if current:
        pages.append(current)
    return pages


def _tidy(cues: list[dict]) -> list[dict]:
    """No cue on screen with the next one, a readable minimum, nothing that starts before the video."""
    kept = [cue for cue in cues if cue["end"] > 0 and cue["end"] > cue["start"]]
    for index, cue in enumerate(kept):
        nxt = kept[index + 1]["start"] if index + 1 < len(kept) else None
        cue["start"] = max(0.0, cue["start"])
        if index and cue["start"] < kept[index - 1]["end"]:
            cue["start"] = kept[index - 1]["end"]
        room = (nxt - GAP - cue["start"]) if nxt is not None else None
        if room is not None and cue["end"] - cue["start"] > room:
            cue["end"] = cue["start"] + max(0.0, room)
        if cue["end"] - cue["start"] < MIN_DURATION:
            want = MIN_DURATION if room is None else min(MIN_DURATION, room)
            cue["end"] = cue["start"] + max(0.0, want)
    return [cue for cue in kept if cue["end"] - cue["start"] >= 0.15]


def cues(passages: list[dict], shift: float = 0.0) -> list[dict]:
    """Cues of a language, in the file's own time: `{"start", "end", "lines"}`, `shift` in seconds.

    `passages` are session-time passages (`start`, `end`, `text`, optional Whisper `words`)."""
    out = []
    for passage in passages:
        for page in _pages(_words(passage)):
            start, end = page[0]["s"] + shift, page[-1]["e"] + shift
            if len(page) == 1:
                # a lone last word would otherwise hang on screen for the whole tail of the passage
                end = min(end, start + MAX_DURATION)
            if end - start <= 0:
                continue
            out.append({"start": start, "end": end, "lines": lines_of(page)})
    return _tidy(out)


def srt(cues: list[dict]) -> str:
    """The SRT file itself: numbered blocks, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, blank line between."""
    blocks = []
    for number, cue in enumerate(cues, 1):
        blocks.append(
            f"{number}\n{timestamp(cue['start'])} --> {timestamp(cue['end'])}\n" + "\n".join(cue["lines"]) + "\n"
        )
    return "\n".join(blocks)


def vtt(cues: list[dict]) -> str:
    """The WebVTT file read by a browser's `<track>`: `WEBVTT` header, no cue number, dot in the timecode.

    A file with no cue keeps its header only: a track that has nothing to say is valid, and a player
    must not receive a broken file. Arabic lines carry their own right-to-left direction."""
    blocks = [
        f"{timestamp_vtt(cue['start'])} --> {timestamp_vtt(cue['end'])}\n"
        + "\n".join(_rtl(line) for line in cue["lines"])
        for cue in cues
    ]
    return ("WEBVTT\n\n" + "\n\n".join(blocks) + "\n") if blocks else "WEBVTT\n"


# ------------------------------------------------------------------ languages


def _mid(seg: dict) -> float:
    return (float(seg["start"]) + float(seg["end"])) / 2


def _verse_fr(translations: dict, m: dict) -> str:
    """Hamidullah translation of the verse (the excerpt picked for the recited part), never a model's."""
    try:
        return verse_fr(translations, m) or ""
    except (KeyError, TypeError):
        return str(m.get("verse_fr") or "")


def units(segments: list[dict], matches: list[dict], translations: dict, translated: dict | None = None) -> list[dict]:
    """Stretches of speech of the sermon, each with its text in every language it can be read in.

    A unit is `{"start", "end", "ar", "fr", "ar_words", "fr_words", "verse", "coran"}`; `ar`/`fr` is
    None when that language does not know the passage yet (untranslated speech, or a Quranic passage
    deliberately left out). `translated` is the French→Arabic cache (`translations_ar.json`).

    A detected verse is ONE unit over the segments it covers: Arabic side the exact Quranic text
    (`recited_arabic`), French side the reference translation — a model never translates a verse.
    """
    translated = translated or {}
    order = sorted(matches, key=lambda m: (float(m["start"]), float(m["end"])))
    taken = [False] * len(segments)
    out: list[dict] = []
    for m in order:
        a, b = float(m["start"]) - 0.3, float(m["end"]) + 0.3
        picked = []
        for i, seg in enumerate(segments):
            if taken[i]:
                continue
            span = max(1e-6, float(seg["end"]) - float(seg["start"]))
            overlap = min(float(seg["end"]), b) - max(float(seg["start"]), a)
            if a <= _mid(seg) <= b or overlap / span > 0.5:
                picked.append(i)
        if not picked:
            continue
        for i in picked:
            taken[i] = True
        heard = str(m.get("heard") or "")
        exact = recited_arabic(m) if (m.get("verse_text") and heard) else ""
        if not exact:  # older verses.json without the Quranic text: the transcription is all we have
            exact = " ".join(str(segments[i].get("text") or "").strip() for i in picked).strip()
        out.append(
            {
                "start": min(float(segments[i]["start"]) for i in picked),
                "end": max(float(segments[i]["end"]) for i in picked),
                "ar": exact or None,
                "fr": _verse_fr(translations, m) or None,
                "ar_words": None,
                "fr_words": None,
                "verse": m.get("ref"),
                "coran": False,
            }
        )
    for i, seg in enumerate(segments):
        if taken[i]:
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        words = seg.get("words")
        unit = {
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "ar": None,
            "fr": None,
            "ar_words": None,
            "fr_words": None,
            "verse": None,
            "coran": False,
        }
        if is_arabic(text):
            unit["ar"] = text
            unit["ar_words"] = words
            shown = clipmod.subtitle(seg, [], translations)
            if shown.get("fr"):
                unit["fr"] = shown["fr"]
                # word timestamps belong to the original text: a translation has none
                if words and str(shown["fr"]).strip() == text:
                    unit["fr_words"] = words
            elif shown.get("kind") == "recitation":
                # unmatched Quranic quotation: nothing on screen rather than an improvised translation
                unit["coran"] = True
        else:
            unit["fr"] = text
            unit["fr_words"] = words
            unit["ar"] = (segment_ar(translated, seg) or {}).get("ar") or None
        out.append(unit)
    return sorted(out, key=lambda u: (u["start"], u["end"]))


def _passages(units_: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"ar": [], "fr": []}
    for unit in units_:
        for code in ("ar", "fr"):
            text = unit[code]
            if not text:
                continue
            passage = {"start": unit["start"], "end": unit["end"], "text": text}
            if unit.get(f"{code}_words"):
                passage["words"] = unit[f"{code}_words"]
            out[code].append(passage)
    return {code: passages for code, passages in out.items() if passages}


def _languages(units_: list[dict]) -> dict[str, dict]:
    """How much of each track is known: `{"done", "total", "complete"}` per language."""
    out = {}
    for code in ("ar", "fr"):
        # an unmatched Quranic quotation has no French text BY CHOICE: it must not keep a track
        # « incomplete » for ever, the human corrects it from the transcript when they recognise it.
        done = sum(1 for u in units_ if u[code] or (code == "fr" and u.get("coran")))
        out[code] = {"done": done, "total": len(units_), "complete": bool(units_) and done == len(units_)}
    return out


def plan(segments: list[dict], matches: list[dict], translations: dict, translated: dict | None = None) -> dict:
    """Everything the subtitles need from the session: passages per language and completeness.

    `segments` are session-time segments (corrections applied), `matches` the verses to show,
    `translations` the Arabic→French cache, `translated` the French→Arabic one. One read, one answer,
    so a caller that needs both never builds the units twice.
    """
    units_ = units(segments, matches, translations, translated)
    return {"passages": _passages(units_), "languages": _languages(units_), "units": len(units_)}


def tracks(segments: list[dict], matches: list[dict], translations: dict, translated: dict | None = None) -> dict:
    """Passages per language, in session time: the whole sermon in each language it can be read in.

    Only the languages that hold at least one cue are returned; `plan` gives the completeness.
    """
    return plan(segments, matches, translations, translated)["passages"]


def coverage(segments: list[dict], matches: list[dict], translations: dict, translated: dict | None = None) -> dict:
    """`{"ar": {"done", "total", "complete"}, "fr": …}`: a language is complete only when all of it is known."""
    return plan(segments, matches, translations, translated)["languages"]
