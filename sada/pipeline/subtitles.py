"""SRT subtitles of the long-form video: one file per language, uploaded separately to YouTube.

YouTube takes SRT (or WebVTT), never the ASS burned into the clips: these files are plain text built
from the timestamped segments the pipeline already produced (`transcript.json`, `translations.json`,
`verses.json`), with the corrections made in the back-office. Nothing is written into the picture.

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


# ------------------------------------------------------------------ languages


def tracks(segments: list[dict], matches: list[dict], translations: dict) -> dict[str, list[dict]]:
    """Passages per language, in session time, for the segments the pipeline already knows.

    `ar`: what was said, as transcribed (and corrected);
    `fr`: what the clips display — French speech, the translation of Arabic speech, the Hamidullah
    verse; a passage without translation is left out rather than approximated.
    A language produced by a later translation step would be added here (and only then offered by
    the back-office), on the same pattern.
    """
    arabic: list[dict] = []
    french: list[dict] = []
    for segment in segments:
        shown = clipmod.subtitle(segment, matches, translations)
        text = str(segment["text"]).strip()
        passage = {"start": float(segment["start"]), "end": float(segment["end"]), "text": text}
        if segment.get("words"):
            passage["words"] = segment["words"]
        if is_arabic(text):
            arabic.append(passage)
        if shown.get("fr"):
            french.append(
                {
                    "start": passage["start"],
                    "end": passage["end"],
                    "text": shown["fr"],
                    # word timestamps belong to the original text: a translation has none
                    **({"words": passage["words"]} if passage.get("words") and shown["fr"].strip() == text else {}),
                }
            )
    produced = {"ar": arabic, "fr": french}
    return {code: passages for code, passages in produced.items() if passages}
