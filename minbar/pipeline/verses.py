"""Detection of Quranic verses in a transcript (fuzzy matching on word windows).

Method:
 1. Normalise the Arabic (without diacritics); keep the Arabic words of each Whisper segment.
 2. Index: every window of N words of every verse (short verse = a single window).
 3. For every window of N words of the transcript (sliding, per segment), best rapidfuzz score (ratio 0-100).
 4. Keep the windows >= threshold, group them by (surah, verse) and by temporal proximity.
 5. A « match » = surah:verse, max score, start/end timestamp, transcribed text, exact text.
Every match stays « to be checked by a human »: the tool suggests, it does not decide.

This module also decides what is SHOWN as a verse (shown / for_display): ritual formulas
(ta'awwudh, basmala…), which fuzzy detection brings close to 16:98, 64:1…, are not verses.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict

from rapidfuzz import fuzz, process

from ..text import arabic_ratio
from .arabic import normalize
from .verses_fr import FrenchIndex, content_words

WIN = 5          # words per window
MIN_VERSE_WORDS = 3
THRESH = 86.0
# French-translation detection: lower threshold than for Arabic (Whisper rephrases), content words only,
# and confidence capped at « trad » — a verse recognised from French alone is suggested, never taken for granted.
THRESH_FR = 68.0
MIN_FR_WORDS = 4


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
    coverage: float = 0.0   # share of the verse words recovered (approx.)
    ref: str = ""
    confidence: str = "sûr"      # sûr | court | ambigu (formula in several verses) | faible (fragment) | trad (via French)
    candidates: list = field(default_factory=list)
    via: str = "arabe"           # arabe | francais (detection via the translation)

    def __post_init__(self):
        self.ref = f"{self.surah}:{self.ayah}"


class VerseIndex:
    def __init__(self, verses: list[dict], win: int = WIN):
        self.verses, self.win = verses, win
        self.fr = FrenchIndex(verses)
        self.keys: list[str] = []
        self.owner: list[int] = []
        self.key_owners: dict[str, set] = {}
        for vi, v in enumerate(verses):
            w = v["norm"].split()
            if len(w) < MIN_VERSE_WORDS:
                continue
            if len(w) <= win:
                self.keys.append(" ".join(w)); self.owner.append(vi)
            else:
                for i in range(len(w) - win + 1):
                    self.keys.append(" ".join(w[i:i + win])); self.owner.append(vi)
        for k, o in zip(self.keys, self.owner):
            self.key_owners.setdefault(k, set()).add(o)

    def search(self, windows: list[str], thresh: float = THRESH):
        """For each window: (best_score, verse_index) or None."""
        if not windows:
            return []
        res = process.cdist(windows, self.keys, scorer=fuzz.ratio, workers=-1, score_cutoff=thresh, dtype="uint8")
        out = []
        for row in res:
            j = int(row.argmax())
            sc = float(row[j])
            out.append((sc, self.owner[j], self.keys[j]) if sc >= thresh else None)
        return out


def detect(segments: list[dict], index: VerseIndex, thresh: float = THRESH, gap_s: float = 25.0,
           thresh_fr: float = THRESH_FR) -> list[Match]:
    """segments: [{start, end, text}] -> list of Match sorted by time.

    Two passes: Arabic (transcription of the recitation) then French (translation/transcription
    of the meaning in French). A given stretch of speech is assigned to a single pass, so as not to count
    the same passage twice.
    """
    matches = _detect_ar(segments, index, thresh, gap_s)
    taken = [(m.start, m.end) for m in matches]
    fr = [m for m in _detect_fr(segments, index, thresh_fr, gap_s)
          if not any(m.start < e and s < m.end for s, e in taken)]
    matches = sorted(matches + fr, key=lambda m: (m.start, -m.score))
    return matches


def _detect_ar(segments: list[dict], index: VerseIndex, thresh: float = THRESH, gap_s: float = 25.0) -> list[Match]:
    """Arabic pass: windows of normalised words compared with the verse windows."""
    wins, meta = [], []
    for seg in segments:
        if arabic_ratio(seg["text"]) < 0.3:
            continue
        words = normalize(seg["text"]).split()
        if len(words) < index.win:
            # short segment: a single window if enough words
            if len(words) >= MIN_VERSE_WORDS:
                wins.append(" ".join(words)); meta.append((seg, " ".join(words)))
            continue
        for i in range(len(words) - index.win + 1):
            wins.append(" ".join(words[i:i + index.win])); meta.append((seg, " ".join(words[i:i + index.win])))
    hits = index.search(wins, thresh)
    groups: dict = {}
    for (seg, w), h in zip(meta, hits):
        if not h:
            continue
        sc, vi, key = h
        owners = index.key_owners.get(key, {vi})
        gk = ("amb", key) if len(owners) > 1 else ("v", vi)
        groups.setdefault(gk, []).append((seg, w, sc, owners, vi))
    matches: list[Match] = []
    for gk, items in groups.items():
        items.sort(key=lambda x: x[0]["start"])
        clusters, cur = [], [items[0]]
        for it in items[1:]:
            if it[0]["start"] - cur[-1][0]["end"] <= gap_s:
                cur.append(it)
            else:
                clusters.append(cur); cur = [it]
        clusters.append(cur)
        for cl in clusters:
            owners = cl[0][3]
            vi = min(owners) if gk[0] == "amb" else gk[1]
            v = index.verses[vi]
            vwords = set(v["norm"].split())
            segs = {id(s): s for s, *_ in cl}.values()
            heard_words = set(" ".join(w for _, w, *_ in cl).split())
            cov = len(vwords & heard_words) / max(1, len(vwords))
            if gk[0] == "amb":
                conf = "ambigu"
            elif len(v["norm"].split()) < index.win:
                conf = "court"
            elif cov < 0.4:
                conf = "faible"   # fragment too short relative to the verse: probably a neighbouring formula
            else:
                conf = "sûr"
            m = Match(v["surah"], v["ayah"], v["surah_name"], max(sc for _, _, sc, *_ in cl),
                      min(s["start"] for s in segs), max(s["end"] for s in segs),
                      " ".join(s["text"].strip() for s in segs), v["text"], v["fr"], round(cov, 2))
            m.confidence = conf
            if conf == "ambigu":
                m.candidates = sorted(f"{index.verses[o]['surah']}:{index.verses[o]['ayah']}" for o in owners)[:8]
            matches.append(m)
    matches.sort(key=lambda m: (m.start, -m.score))
    return matches


def _detect_fr(segments: list[dict], index: VerseIndex, thresh: float = THRESH_FR, gap_s: float = 25.0) -> list[Match]:
    """French pass: sliding windows over the continuous text, compared with the verse translations.

    Whisper cuts speech anywhere: we therefore stitch several neighbouring segments back together (sentence
    boundaries rarely fall on the key word) and keep, per verse, the best window, with the
    segments it covers.
    """
    fr_ = index.fr
    if not len(fr_):
        return []
    usable = [i for i, seg in enumerate(segments) if arabic_ratio(seg["text"]) < 0.3]
    if not usable:
        return []
    n = fr_.win
    wins, spans = [], []              # spans: (first segment, last segment)
    i = 0
    while i < len(usable):
        j, words = i, []
        while j < len(usable) and len(words) < 3 * n:
            words += content_words(segments[usable[j]]["text"])
            j += 1
        if len(words) >= n:
            step = 2 if len(words) > 3 * n else 1      # hybrid: fine step, then double step over long passages
            for k in range(0, len(words) - n + 1, step):
                wins.append(" ".join(words[k:k + n])); spans.append((usable[i], usable[j - 1]))
        i = j
    if not wins:
        return []
    hits = fr_.search(wins, thresh)
    best: dict[int, tuple] = {}
    for (a, b), h in zip(spans, hits):
        if not h:
            continue
        sc, vi, key = h
        if sc > best.get(vi, (0.0,))[0]:
            best[vi] = (sc, a, b)
    matches: list[Match] = []
    for vi, (sc, a, b) in best.items():
        v = index.verses[vi]
        covered = segments[a:b + 1]
        m = Match(v["surah"], v["ayah"], v["surah_name"], sc,
                  min(s["start"] for s in covered), max(s["end"] for s in covered),
                  " ".join(s["text"].strip() for s in covered), v["text"], v["fr"], 0.0, via="francais")
        m.confidence = "trad"
        matches.append(m)
    matches.sort(key=lambda m: (m.start, -m.score))
    return matches


def to_dicts(ms: list[Match]) -> list[dict]:
    return [asdict(m) for m in ms]


# ------------------------------------------------------------------ displayed verses

FORMULAS = {
    "اعوذ بالله من الشيطان الرجيم": "Je cherche protection auprès d'Allah contre Satan le banni.",
    "بسم الله الرحمن الرحيم": "Au nom d'Allah, le Tout Miséricordieux, le Très Miséricordieux.",
    "اشهد ان لا اله الا الله": "J'atteste qu'il n'y a de divinité qu'Allah,",
    "وحده لا شريك له": "Seul, sans associé.",
    "له الملك وله الحمد": "À Lui la royauté, à Lui la louange,",
    "وهو علي كل شيء قدير": "et Il est capable de toute chose.",
    "الحمد لله رب العالمين": "Louange à Allah, Seigneur des mondes.",
    "صلي الله عليه وسلم": "que la paix et les bénédictions d'Allah soient sur lui.",
}
FORM_NORM = {normalize(k): v for k, v in FORMULAS.items()}
# « trad » = recognised from the French translation alone: suggested, never treated as a safe reference.
FIRM = ("sûr", "court", "trad")
FORMULA_MATCH = 85


def formula_fr(text: str) -> str | None:
    """Fixed translation of a segment made of ritual formulas, otherwise None."""
    if not is_formula(text):
        return None
    hn = normalize(text)
    return " ".join(v for f, v in FORM_NORM.items() if fuzz.partial_ratio(f, hn) >= FORMULA_MATCH) or None


def is_formula(heard: str) -> bool:
    """True if the heard text is (almost) entirely made of ritual formulas."""
    words = normalize(heard).split()
    if not words:
        return False
    hn = " ".join(words)
    used: set[str] = set()
    for f in FORM_NORM:
        if fuzz.partial_ratio(f, hn) >= FORMULA_MATCH:
            used |= set(f.split())
    rest = [w for w in words if w not in used and not any(fuzz.ratio(w, u) >= 80 for u in used)]
    return len(rest) < 3


def shown(matches: list[dict], ambiguous: bool = False) -> list[dict]:
    """Verses to show as verses: confidence « sûr »/« court » and not a ritual formula.

    ambiguous=True adds Quranic passages that are certain but common to several verses
    (e.g. « والله يعلم وأنتم لا تعلمون »): a « CORAN » card without a reference (field `ambiguous`).
    """
    out = []
    for m in matches:
        if is_formula(m.get("heard", "")):
            continue
        if m.get("confidence", "sûr") in FIRM:
            out.append(m)
        elif ambiguous and m.get("confidence") == "ambigu" and m.get("score", 0) >= 95 and len(m.get("heard", "").split()) >= 3:
            out.append({**m, "ambiguous": True})
    return out


def for_display(matches: list[dict]) -> list[dict]:
    """List of the verses of a khutba: « sûr »/« court » detections + all ambiguous passages, flagged « to be checked »."""
    sure = shown(matches)
    keys = {(m["ref"], m["start"]) for m in sure}
    amb = [{**m, "ambiguous": True} for m in matches
           if (m["ref"], m["start"]) not in keys and m.get("confidence") == "ambigu" and not is_formula(m.get("heard", ""))]
    return sorted(sure + amb, key=lambda m: m["start"])


def match_for(seg: dict, vms: list[dict]) -> dict | None:
    """Verse (among vms) recited by the segment."""
    mid = (seg["start"] + seg["end"]) / 2
    return next((m for m in vms if m["start"] - 0.3 <= mid <= m["end"] + 0.3), None)


def strip_quran_marks(t: str) -> str:
    """Remove the pause marks / Quranic annotations (U+06D6–U+06ED) and normalise the whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[\u06D6-\u06ED]", "", t)).strip()


def recited_arabic(m: dict) -> str:
    """Portion of the Quranic text actually recited (word window of the verse closest to what was heard)."""
    vw = strip_quran_marks(m["verse_text"]).split()
    hn = normalize(m.get("heard", ""))
    k = len(hn.split())
    if not vw or k == 0 or k >= len(vw):
        return " ".join(vw)
    best = (0.0, 0, len(vw))
    for n in range(max(1, k - 2), min(len(vw), k + 2) + 1):
        for i in range(0, len(vw) - n + 1):
            sc = fuzz.ratio(normalize(" ".join(vw[i:i + n])), hn)
            if sc > best[0]:
                best = (sc, i, i + n)
    return " ".join(vw[best[1]:best[2]]) if best[0] >= 70 else " ".join(vw)
