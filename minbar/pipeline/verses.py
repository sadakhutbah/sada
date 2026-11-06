"""Détection des versets coraniques dans une transcription (correspondance floue par fenêtres de mots).

Méthode :
 1. On normalise l'arabe (sans diacritiques) ; on garde les mots arabes de chaque segment Whisper.
 2. Index : toutes les fenêtres de N mots de chaque verset (verset court = fenêtre unique).
 3. Pour chaque fenêtre de N mots de la transcription (glissante, par segment), meilleur score rapidfuzz (ratio 0-100).
 4. On garde les fenêtres >= seuil, on les regroupe par (sourate, verset) et par proximité temporelle.
 5. Un « match » = sourate:verset, score max, horodatage début/fin, texte transcrit, texte exact.
Toute correspondance reste « à vérifier par un humain » : l'outil propose, ne tranche pas.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

from rapidfuzz import fuzz, process

from .arabic import arabic_ratio, normalize

WIN = 4          # mots par fenêtre
MIN_VERSE_WORDS = 3
THRESH = 82.0


@dataclass
class Match:
    surah: int
    ayah: int
    surah_name: str
    score: float
    start: float
    end: float
    heard: str
    verse_text: str
    verse_fr: str
    coverage: float = 0.0   # part des mots du verset retrouvés (approx.)
    ref: str = ""

    def __post_init__(self):
        self.ref = f"{self.surah}:{self.ayah}"


class VerseIndex:
    def __init__(self, verses: list[dict], win: int = WIN):
        self.verses, self.win = verses, win
        self.keys: list[str] = []
        self.owner: list[int] = []
        for vi, v in enumerate(verses):
            w = v["norm"].split()
            if len(w) < MIN_VERSE_WORDS:
                continue
            if len(w) <= win:
                self.keys.append(" ".join(w)); self.owner.append(vi)
            else:
                for i in range(len(w) - win + 1):
                    self.keys.append(" ".join(w[i:i + win])); self.owner.append(vi)

    def search(self, windows: list[str], thresh: float = THRESH):
        """Pour chaque fenêtre : (meilleur_score, index_verset) ou None."""
        if not windows:
            return []
        res = process.cdist(windows, self.keys, scorer=fuzz.ratio, workers=-1, score_cutoff=thresh, dtype="uint8")
        out = []
        for row in res:
            j = int(row.argmax())
            sc = float(row[j])
            out.append((sc, self.owner[j]) if sc >= thresh else None)
        return out


def detect(segments: list[dict], index: VerseIndex, thresh: float = THRESH, gap_s: float = 25.0) -> list[Match]:
    """segments : [{start, end, text}] -> liste de Match triée par temps."""
    wins, meta = [], []
    for seg in segments:
        if arabic_ratio(seg["text"]) < 0.3:
            continue
        words = normalize(seg["text"]).split()
        if len(words) < index.win:
            # segment court : une seule fenêtre si assez de mots
            if len(words) >= MIN_VERSE_WORDS:
                wins.append(" ".join(words)); meta.append((seg, " ".join(words)))
            continue
        for i in range(len(words) - index.win + 1):
            wins.append(" ".join(words[i:i + index.win])); meta.append((seg, " ".join(words[i:i + index.win])))
    hits = index.search(wins, thresh)
    groups: dict[int, list] = {}
    for (seg, w), h in zip(meta, hits):
        if h:
            groups.setdefault(h[1], []).append((seg, w, h[0]))
    matches: list[Match] = []
    for vi, items in groups.items():
        items.sort(key=lambda x: x[0]["start"])
        cur = [items[0]]
        clusters = []
        for it in items[1:]:
            if it[0]["start"] - cur[-1][0]["end"] <= gap_s:
                cur.append(it)
            else:
                clusters.append(cur); cur = [it]
        clusters.append(cur)
        v = index.verses[vi]
        vwords = set(v["norm"].split())
        for cl in clusters:
            segs = {id(s): s for s, _, _ in cl}.values()
            heard_words = set(" ".join(w for _, w, _ in cl).split())
            cov = len(vwords & heard_words) / max(1, len(vwords))
            m = Match(v["surah"], v["ayah"], v["surah_name"], max(sc for _, _, sc in cl),
                      min(s["start"] for s in segs), max(s["end"] for s in segs),
                      " ".join(s["text"].strip() for s in segs), v["text"], v["fr"], round(cov, 2))
            matches.append(m)
    # un même segment ne doit pas porter 3 versets voisins par hasard : on garde le meilleur par segment dominant
    matches.sort(key=lambda m: (m.start, -m.score))
    return matches


def to_dicts(ms: list[Match]) -> list[dict]:
    return [asdict(m) for m in ms]
