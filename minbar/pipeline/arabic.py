"""Normalisation de l'arabe (sans diacritiques) pour comparer transcription et texte coranique."""
from __future__ import annotations
import re
import unicodedata

from ..text import AR_LETTER

_DIAC = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ࣓-ࣿـ]")
_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "ی": "ي", "ڤ": "ف",
})


def normalize(text: str) -> str:
    """Retire diacritiques/tatweel, unifie alef/ya/ta marbuta, garde seulement les lettres arabes et espaces."""
    t = unicodedata.normalize("NFKD", text)
    t = _DIAC.sub("", t).translate(_MAP)
    t = "".join(ch if (AR_LETTER.match(ch) or ch.isspace()) else " " for ch in t)
    return re.sub(r"\s+", " ", t).strip()
