"""Détection des versets coraniques dans une transcription (correspondance floue par fenêtres de mots).

Méthode :
 1. On normalise l'arabe (sans diacritiques) ; on garde les mots arabes de chaque segment Whisper.
 2. Index : toutes les fenêtres de N mots de chaque verset (verset court = fenêtre unique).
 3. Pour chaque fenêtre de N mots de la transcription (glissante, par segment), meilleur score rapidfuzz (ratio 0-100).
 4. On garde les fenêtres >= seuil, on les regroupe par (sourate, verset) et par proximité temporelle.
 5. Un « match » = sourate:verset, score max, horodatage début/fin, texte transcrit, texte exact.
Toute correspondance reste « à vérifier par un humain » : l'outil propose, ne tranche pas.

Ce module décide aussi ce qui se MONTRE comme verset (shown / for_display) : les formules rituelles
(ta'awwudh, basmala…), que la détection floue rapproche de 16:98, 64:1…, n'en sont pas.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict

from rapidfuzz import fuzz, process

from ..text import arabic_ratio
from .arabic import normalize

WIN = 5          # mots par fenêtre
MIN_VERSE_WORDS = 3
THRESH = 86.0


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
    confidence: str = "sûr"      # sûr | court | ambigu (formule dans plusieurs versets) | faible (fragment)
    candidates: list = field(default_factory=list)

    def __post_init__(self):
        self.ref = f"{self.surah}:{self.ayah}"


class VerseIndex:
    def __init__(self, verses: list[dict], win: int = WIN):
        self.verses, self.win = verses, win
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
        """Pour chaque fenêtre : (meilleur_score, index_verset) ou None."""
        if not windows:
            return []
        res = process.cdist(windows, self.keys, scorer=fuzz.ratio, workers=-1, score_cutoff=thresh, dtype="uint8")
        out = []
        for row in res:
            j = int(row.argmax())
            sc = float(row[j])
            out.append((sc, self.owner[j], self.keys[j]) if sc >= thresh else None)
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
                conf = "faible"   # fragment trop court par rapport au verset : probablement une formule voisine
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


def to_dicts(ms: list[Match]) -> list[dict]:
    return [asdict(m) for m in ms]


# ------------------------------------------------------------------ versets montrés

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
FIRM = ("sûr", "court")
FORMULA_MATCH = 85


def formula_fr(text: str) -> str | None:
    """Traduction fixe d'un segment fait de formules rituelles, sinon None."""
    if not is_formula(text):
        return None
    hn = normalize(text)
    return " ".join(v for f, v in FORM_NORM.items() if fuzz.partial_ratio(f, hn) >= FORMULA_MATCH) or None


def is_formula(heard: str) -> bool:
    """Vrai si le texte entendu est (presque) entièrement fait de formules rituelles."""
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
    """Versets à montrer comme versets : confiance sûre/courte et pas une formule rituelle.

    ambiguous=True ajoute les passages coraniques certains mais communs à plusieurs versets
    (ex. « والله يعلم وأنتم لا تعلمون ») : carte « CORAN » sans référence (champ `ambiguous`).
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
    """Liste des versets d'une khutba : détections sûres/courtes + tous les passages ambigus, signalés « à vérifier »."""
    sure = shown(matches)
    keys = {(m["ref"], m["start"]) for m in sure}
    amb = [{**m, "ambiguous": True} for m in matches
           if (m["ref"], m["start"]) not in keys and m.get("confidence") == "ambigu" and not is_formula(m.get("heard", ""))]
    return sorted(sure + amb, key=lambda m: m["start"])


def match_for(seg: dict, vms: list[dict]) -> dict | None:
    """Verset (parmi vms) que récite le segment."""
    mid = (seg["start"] + seg["end"]) / 2
    return next((m for m in vms if m["start"] - 0.3 <= mid <= m["end"] + 0.3), None)


def strip_quran_marks(t: str) -> str:
    """Retire les signes de pause / annotations coraniques (U+06D6–U+06ED) et normalise les espaces."""
    return re.sub(r"\s+", " ", re.sub(r"[\u06D6-\u06ED]", "", t)).strip()


def recited_arabic(m: dict) -> str:
    """Portion du texte coranique effectivement récitée (fenêtre de mots du verset la plus proche de l'entendu)."""
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
