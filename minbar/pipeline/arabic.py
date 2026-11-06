"""Normalisation de l'arabe (sans diacritiques) pour comparer transcription et texte coranique."""
from __future__ import annotations
import re
import unicodedata

_DIAC = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u08D3-\u08FF\u0640]")
_AR_LETTER = re.compile(r"[\u0621-\u064A\u0671-\u06D3]")
_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "ی": "ي", "ڤ": "ف",
})


def normalize(text: str) -> str:
    """Retire diacritiques/tatweel, unifie alef/ya/ta marbuta, garde seulement les lettres arabes et espaces."""
    t = unicodedata.normalize("NFKD", text)
    t = _DIAC.sub("", t).translate(_MAP)
    t = "".join(ch if (_AR_LETTER.match(ch) or ch.isspace()) else " " for ch in t)
    return re.sub(r"\s+", " ", t).strip()


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _AR_LETTER.match(c)) / len(letters)
