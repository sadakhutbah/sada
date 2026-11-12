"""French translation of the verses: normalisation and index (detection when the recitation is transcribed in French).

Whisper translates or transcribes Arabic into approximate French (near-Hamidullah phrasing, blurred words).
We therefore compare content words (without accents or punctuation, without stop words) rather than a raw string:
one recovered meaning-bearing root is enough, and the score is deliberately lowered to stay cautious.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

WIN_FR = 6

# stop words: present everywhere in a translation, they do not discriminate
STOP = {
    "le",
    "la",
    "les",
    "l",
    "d",
    "de",
    "des",
    "du",
    "un",
    "une",
    "et",
    "ou",
    "a",
    "au",
    "aux",
    "en",
    "dans",
    "sur",
    "par",
    "pour",
    "que",
    "qui",
    "quoi",
    "qu",
    "ce",
    "cette",
    "ces",
    "cet",
    "il",
    "elle",
    "ils",
    "elles",
    "on",
    "nous",
    "vous",
    "je",
    "tu",
    "se",
    "sa",
    "son",
    "ses",
    "leur",
    "leurs",
    "est",
    "sont",
    "etait",
    "ete",
    "ont",
    "avec",
    "sans",
    "ne",
    "pas",
    "plus",
    "tout",
    "tous",
    "toute",
    "toutes",
    "bien",
    "aussi",
    "comme",
    "ainsi",
    "vers",
    "chez",
    "y",
    "puis",
    "mais",
    "donc",
    "si",
    "alors",
    "dont",
    "duquel",
    # verb « to say »: ubiquitous in a khutba that announces its quotations, never decisive
    "dit",
    "dis",
    "dire",
    "disent",
    "declare",
    "declaree",
    "raconte",
    "relate",
    "mentionne",
    "rappelle",
}
# Quran words that linger in ALL the verses: they must not cause a detection on their own
GENERIC = {
    "allah",
    "dieu",
    "seigneur",
    "certes",
    "voici",
    "verite",
    "gens",
    "chose",
    "choses",
    "jour",
    "monde",
    "terres",
    "cieux",
    "terre",
    "vie",
    "mort",
    "coeur",
    "coeurs",
    "homme",
    "hommes",
}


def norm_fr(text: str) -> str:
    """Lowercase, without accents or punctuation, stop words removed, apostrophes split off."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[’']", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [w for w in t.split() if w and w not in STOP and len(w) > 1]
    return " ".join(words)


def content_words(text: str) -> list[str]:
    """Meaningful words: neither stop words nor ubiquitous Quran words."""
    return [w for w in norm_fr(text).split() if w not in GENERIC]


def fr_ratio(text: str) -> float:
    """Share of content words in a text: very low for « patience is a virtue » with no Quranic subject."""
    words = re.findall(r"[a-zA-ZÀ-ÿ']+", text or "")
    if not words:
        return 0.0
    return sum(1 for w in words if norm_fr(w)) / len(words)


class FrenchIndex:
    """Index of the French translations: content-word windows of each verse."""

    def __init__(self, verses: list[dict], win: int = WIN_FR):
        self.verses, self.win = verses, win
        self.keys: list[str] = []
        self.owner: list[int] = []
        self.usable: set[int] = set()
        for vi, v in enumerate(verses):
            words = content_words(v.get("fr") or "")
            if len(words) < 3:
                continue  # translation missing or too short: not usable
            self.usable.add(vi)
            if len(words) <= win:
                self.keys.append(" ".join(words))
                self.owner.append(vi)
            else:
                for i in range(len(words) - win + 1):
                    self.keys.append(" ".join(words[i : i + win]))
                    self.owner.append(vi)

    def __len__(self) -> int:
        return len(self.keys)

    def search(self, windows: list[str], thresh: float):
        if not windows or not self.keys:
            return []
        res = process.cdist(windows, self.keys, scorer=fuzz.ratio, workers=-1, score_cutoff=thresh, dtype="uint8")
        out = []
        for row in res:
            j = int(row.argmax())
            sc = float(row[j])
            out.append((sc, self.owner[j], self.keys[j]) if sc >= thresh else None)
        return out
