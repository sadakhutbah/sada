"""Traduction française des versets : normalisation et index (détection quand la récitation est transcrite en français).

Whisper traduit ou transcrit l'arabe en français approximatif (tournures Hamidullah voisines, mots estompés).
On compare donc des mots pleins (sans accents ni ponctuation, sans mots vides) plutôt qu'une chaîne brute :
une racine de sens retrouvée suffit, et le score est abaissé volontairement pour rester prudent.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

WIN_FR = 6

# mots outils : présents partout dans une traduction, ils ne discriminent pas
STOP = {
    "le", "la", "les", "l", "d", "de", "des", "du", "un", "une", "et", "ou", "a", "au", "aux", "en", "dans", "sur",
    "par", "pour", "que", "qui", "quoi", "qu", "ce", "cette", "ces", "cet", "il", "elle", "ils", "elles", "on",
    "nous", "vous", "je", "tu", "se", "sa", "son", "ses", "leur", "leurs", "est", "sont", "etait", "ete", "ont",
    "avec", "sans", "ne", "pas", "plus", "tout", "tous", "toute", "toutes", "bien", "aussi", "comme", "ainsi",
    "vers", "chez", "y", "en", "puis", "mais", "donc", "si", "alors", "dont", "ou", "duquel",
    # verbe « dire » : omniprésent dans une khutba qui annonce ses citations, jamais décisif
    "dit", "dis", "dire", "disent", "declare", "declaree", "raconte", "relate", "mentionne", "rappelle",
}
# mots du Coran qui traînent dans TOUS les versets : ils ne doivent pas faire une détection à eux seuls
GENERIC = {
    "allah", "dieu", "seigneur", "certes", "voici", "verite", "gens", "chose", "choses", "jour", "monde",
    "terres", "cieux", "terre", "vie", "mort", "coeur", "coeurs", "homme", "hommes",
}


def norm_fr(text: str) -> str:
    """Minuscules, sans accents ni ponctuation, mots vides retirés, apostrophes séparées."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[’']", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [w for w in t.split() if w and w not in STOP and len(w) > 1]
    return " ".join(words)


def content_words(text: str) -> list[str]:
    """Mots significatifs : ni vides, ni mots du Coran omniprésents."""
    return [w for w in norm_fr(text).split() if w not in GENERIC]


def fr_ratio(text: str) -> float:
    """Part de mots pleins dans un texte : très basse pour « la patience est une vertu » sans sujet coranique."""
    words = re.findall(r"[a-zA-ZÀ-ÿ']+", text or "")
    if not words:
        return 0.0
    return sum(1 for w in words if norm_fr(w)) / len(words)


class FrenchIndex:
    """Index des traductions françaises : fenêtres de mots pleins de chaque verset."""

    def __init__(self, verses: list[dict], win: int = WIN_FR):
        self.verses, self.win = verses, win
        self.keys: list[str] = []
        self.owner: list[int] = []
        self.usable: set[int] = set()
        for vi, v in enumerate(verses):
            words = content_words(v.get("fr") or "")
            if len(words) < 3:
                continue                     # traduction absente ou trop courte : non exploitable
            self.usable.add(vi)
            if len(words) <= win:
                self.keys.append(" ".join(words)); self.owner.append(vi)
            else:
                for i in range(len(words) - win + 1):
                    self.keys.append(" ".join(words[i:i + win])); self.owner.append(vi)

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
