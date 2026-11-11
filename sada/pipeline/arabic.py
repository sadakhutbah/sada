"""Arabic normalisation (without diacritics) to compare a transcript with the Quranic text."""
from __future__ import annotations
import re
import unicodedata

from ..text import AR_LETTER

_DIAC = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ࣓-ࣿـ]")
_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "ی": "ي", "ڤ": "ف",
})


def normalize(text: str) -> str:
    """Remove diacritics/tatweel, unify alef/ya/ta marbuta, keep only Arabic letters and spaces."""
    t = unicodedata.normalize("NFKD", text)
    t = _DIAC.sub("", t).translate(_MAP)
    t = "".join(ch if (AR_LETTER.match(ch) or ch.isspace()) else " " for ch in t)
    return re.sub(r"\s+", " ", t).strip()
