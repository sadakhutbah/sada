"""Texte : écriture arabe, dates et durées en français (partagé par le live, le pipeline et le back-office)."""
from __future__ import annotations

import datetime as dt
import re

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
