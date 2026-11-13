"""Text: Arabic script, dates and durations in French (shared by live, pipeline and back-office)."""

from __future__ import annotations

import datetime as dt
import re

# libraqm: shaping of joined letters + RTL direction by Pillow/libass; otherwise fall back on
# arabic_reshaper + python-bidi (optional: nothing breaks if the package is missing).
try:
    from PIL import features as _pil_features

    RAQM = _pil_features.check("raqm")
except ImportError:
    RAQM = False


def reshaped_view(text: str) -> str:
    """Visual form of Arabic when libraqm is absent (joined letters, RTL order).

    libass shapes text on its own: the visual form is then only used for MEASUREMENT
    (PIL without libraqm would count isolated letters, much wider); the emitted text
    stays raw. For a Pillow render, see shaped()."""
    if not has_arabic(text) or RAQM:
        return text
    try:
        import arabic_reshaper  # type: ignore
        from bidi.algorithm import get_display  # type: ignore

        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def shaped(text: str) -> tuple[str, dict]:
    """Single-script text ready for ImageDraw.text(): RTL direction under libraqm, else the visual form."""
    if RAQM:
        # Arabic direction only for text that contains Arabic: otherwise the punctuation
        # of a French sentence ends up on the wrong side
        return text, ({"direction": "rtl", "language": "ar"} if has_arabic(text) else {})
    if not has_arabic(text):
        return text, {}
    return reshaped_view(text), {}


# Arabic script (letters, signs, presentation forms)
AR_CHARS = "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
AR_RUN = re.compile(f"[{AR_CHARS}]+")
_AR_CHAR = re.compile(f"[{AR_CHARS}]")
AR_LETTER = re.compile(r"[ء-يٱ-ۓ]")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]


def has_arabic(s: str) -> bool:
    return bool(_AR_CHAR.search(s))


def is_arabic_char(ch: str) -> bool:
    return bool(_AR_CHAR.match(ch))


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if AR_LETTER.match(c)) / len(letters)


def is_arabic(text: str) -> bool:
    """Transcription segment in Arabic (recitation, formula or Arabic sentence) rather than in French."""
    return arabic_ratio(text) > 0.5


def french_date(d: dt.date) -> str:
    return f"{JOURS[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"


def duration(sec: float) -> str:
    """mm:ss, or h:mm:ss beyond one hour."""
    s = int(round(float(sec)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# Arabic written in Latin letters by Whisper (« wa'alha an alati lahwin ») : a well-scored segment
# escapes the log-probability test, so the share of transliterated words is measured as well.
_TRANSLIT_WORDS = frozenset(
    """wa wal fa fi bi li la lam lan ma man min an anna inna innahu qala qawluhu
qal huwa hiya hum alayhi alayha alaihi allati alladhi alladhina kana kanat yaqul yuqal lahu lahou
ila illa ala ya ayyuha tum thumma idha idhan bihi biha minhu anha anhu ma'a wala walha lahwin""".split()
)
_TRANSLIT_SHAPE = re.compile(
    r"^(?:[aeiu][lrnsdtz]-\w+|(?!(?:l|d|j|n|s|m|t|c|qu|jusqu|lorsqu|puisqu|presqu)')\w+'\w+|\w*(?:q[aiy]|kh|gh|dh|aa|ii|uu)\w*)$"
)
FRENCH_HINT = frozenset(
    "le la les de des du un une et est que qui ce il elle on tu je vous nous pas dans sur pour avec".split()
)


def is_translit_word(word: str) -> bool:
    """Word of Arabic written in Latin letters (« qawluhu », « al-ghada », « na'imul »)."""
    w = word.strip('.,;:!?«»"()').lower().replace("’", "'")
    return bool(w) and w not in FRENCH_HINT and (w in _TRANSLIT_WORDS or bool(_TRANSLIT_SHAPE.match(w)))


def has_translit(text: str, run: int = 2) -> bool:
    """Texte en lettres latines qui contient de l'arabe phonétique à traduire.

    Vrai dès `run` mots translittérés d'affilée (« walha an alati lahwin ») ou quand ils font la
    moitié du texte (« faqala, »). Un mot isolé (« dunya », « fitna ») ne compte pas : la glose de
    l'orateur l'explique déjà. Ces segments ne peuvent pas aller tels quels dans la piste française.
    """
    words = (text or "").split()
    if not words or is_arabic(text):
        return False
    best = cur = 0
    for w in words:
        cur = cur + 1 if is_translit_word(w) else 0
        best = max(best, cur)
    return best >= run or sum(map(is_translit_word, words)) / len(words) >= 0.5
