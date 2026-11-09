"""Texte : écriture arabe, dates et durées en français (partagé par le live, le pipeline et le back-office)."""
from __future__ import annotations

import datetime as dt
import re

# libraqm : mise en forme des lettres liées + sens RTL par Pillow/libass ; à défaut, repli sur
# arabic_reshaper + python-bidi (optionnel : rien ne casse si le paquet est absent).
try:
    from PIL import features as _pil_features

    RAQM = _pil_features.check("raqm")
except ImportError:
    RAQM = False


def reshaped_view(text: str) -> str:
    """Forme visuelle de l'arabe quand libraqm n'est pas là (lettres liées, ordre RTL).

    libass met en forme tout seul : la forme visuelle sert alors uniquement à la MESURE
    (PIL sans libraqm compterait les lettres isolées, beaucoup plus larges) ; le texte
    émis reste brut. Pour un rendu Pillow, cf. shaped()."""
    if not has_arabic(text) or RAQM:
        return text
    try:
        import arabic_reshaper  # type: ignore
        from bidi.algorithm import get_display  # type: ignore

        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def shaped(text: str) -> tuple[str, dict]:
    """Texte mono-écriture prêt pour ImageDraw.text() : sous libraqm, sens RTL ; sinon forme visuelle."""
    if RAQM:
        # direction arabe pour un texte qui contient de l'arabe seulement : sinon la ponctuation
        # d'une phrase française se retrouve du mauvais côté
        return text, ({"direction": "rtl", "language": "ar"} if has_arabic(text) else {})
    if not has_arabic(text):
        return text, {}
    return reshaped_view(text), {}

# écriture arabe (lettres, signes, formes de présentation)
AR_CHARS = "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
AR_RUN = re.compile(f"[{AR_CHARS}]+")
_AR_CHAR = re.compile(f"[{AR_CHARS}]")
AR_LETTER = re.compile(r"[ء-يٱ-ۓ]")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


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
    """Segment de transcription en arabe (récitation, formule ou phrase arabe) plutôt qu'en français."""
    return arabic_ratio(text) > 0.5


def french_date(d: dt.date) -> str:
    return f"{JOURS[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"


def duration(sec: float) -> str:
    """mm:ss, ou h:mm:ss au-delà d'une heure."""
    s = int(round(float(sec)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"
