"""Search across sermons, whatever their language: words, Quran verses and meaning.

- Words: one row per transcript segment and per translation of it (`transcript_segments`), each in
  its own language. PostgreSQL: a tsvector built with the text-search configuration of that language
  ('simple' when the server has none), over `unaccent`, GIN index. SQLite (dev, tests): FTS5.
  Arabic is folded before indexing and querying (no tashkeel or tatweel, one alef, ة→ه, ى→ي, article
  removed), so a query typed without vowels finds a vowelled transcript and the reverse.
- Verses: '2:153', '2:153-155', a surah name in Arabic, French or English, optionally followed by
  a verse number → `verse_citations`, independent of the spoken language.
- Meaning: multilingual-e5-small embeddings of the transcript segments (computed after the live,
  see `traitement.embed_pending`), compared with the query's: pgvector when the server has it,
  brute-force cosine otherwise. Without the model the search keeps words and verses.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import sqlalchemy as sa
from rapidfuzz import fuzz
from sqlalchemy.dialects.postgresql import REGCONFIG

from ..i18n import _
from ..pipeline import verses as verses_mod
from ..pipeline.output import SessionOutput
from ..surahs import SURAHS
from . import embeddings
from .models import Khutba, TranscriptSegment, User, VerseCitation, create_vector_table, pg_extension

TS = TranscriptSegment.__table__
VC = VerseCitation.__table__

# --------------------------------------------------------------------------- normalisation

_AR_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")  # tashkeel, Quranic marks, tatweel
_AR_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"})
_ARABIC = re.compile("[\u0600-\u06ff]")
_ARTICLE = re.compile("^(?:وال|فال|بال|كال|لل|ال)")
_WORD = re.compile(r"\w+")


def fold_arabic(text: str) -> str:
    return _AR_MARKS.sub("", unicodedata.normalize("NFKC", text)).translate(_AR_LETTERS)


def _word(w: str) -> str:
    if _ARABIC.match(w):
        bare = _ARTICLE.sub("", w)
        if len(bare) >= 3:  # « الله » stays whole
            return bare
    return w


def normalize(text: str) -> str:
    """Indexed form of a text: Arabic folded, lower case, one space between words."""
    return " ".join(_word(w) for w in _WORD.findall(fold_arabic(text).casefold()))


def strip_accents(text: str) -> str:
    return unicodedata.normalize(
        "NFC", "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    )


def terms(query: str) -> list[str]:
    """Words of a query in the indexed form, accents removed (what FTS5 and `unaccent` compare)."""
    seen: list[str] = []
    for w in strip_accents(normalize(query)).split():
        if w not in seen:
            seen.append(w)
    return seen[:12]


# --------------------------------------------------------------------------- engine features

#: Whisper / translation language → PostgreSQL text-search configuration (those PostgreSQL ships).
PG_CONFIGS = {
    "ar": "arabic",
    "fr": "french",
    "en": "english",
    "tr": "turkish",
    "de": "german",
    "es": "spanish",
    "it": "italian",
    "nl": "dutch",
    "pt": "portuguese",
    "ru": "russian",
    "id": "indonesian",
    "sv": "swedish",
    "da": "danish",
    "no": "norwegian",
    "fi": "finnish",
    "hu": "hungarian",
    "ro": "romanian",
    "el": "greek",
    "hi": "hindi",
    "ne": "nepali",
    "ta": "tamil",
    "sr": "serbian",
    "lt": "lithuanian",
    "hy": "armenian",
    "ca": "catalan",
    "eu": "basque",
    "ga": "irish",
    "yi": "yiddish",
}


@dataclass
class Features:
    dialect: str
    unaccent: str | None = None  # schema of the unaccent() function
    vector: str | None = None  # schema of pgvector when segment_vectors is usable
    configs: dict[str, str] = field(default_factory=dict)  # lang → available configuration


_features: dict[tuple, Features] = {}
_features_lock = threading.Lock()


def _bind(conn):
    return conn.get_bind() if hasattr(conn, "get_bind") else conn.engine


def features(conn) -> Features:
    bind = _bind(conn)
    key = (id(bind), str(bind.url))
    with _features_lock:
        f = _features.get(key)
    if f is not None:
        return f
    f = Features(bind.dialect.name)
    if f.dialect == "postgresql":
        f.unaccent = pg_extension(conn, "unaccent")
        if conn.execute(sa.select(sa.func.to_regclass("segment_vectors"))).scalar() is not None:
            f.vector = pg_extension(conn, "vector")
        cfg = sa.table("pg_ts_config", sa.column("cfgname"), schema="pg_catalog")
        have = set(conn.execute(sa.select(cfg.c.cfgname)).scalars())
        f.configs = {lang: c for lang, c in PG_CONFIGS.items() if c in have}
    with _features_lock:
        _features[key] = f
    return f


def reset_features() -> None:
    with _features_lock:
        _features.clear()


def _pg_config(f: Features, lang_col):
    """SQL expression: the regconfig of a segment's language, 'simple' when the server has none."""
    whens = [(lang_col == lang, c) for lang, c in sorted(f.configs.items())]
    expr = sa.case(*whens, else_="simple") if whens else sa.literal("simple")
    return sa.cast(expr, REGCONFIG)


def _unaccent(f: Features, expr):
    return getattr(sa.func, f.unaccent).unaccent(expr) if f.unaccent else expr


# --------------------------------------------------------------------------- indexing


def _citations(verses: list[dict]) -> list[tuple[int, int, float]]:
    out = []
    for m in verses_mod.shown(verses):
        try:
            s, a = int(m["surah"]), int(m["ayah"])
        except (KeyError, TypeError, ValueError):
            continue
        last = a
        r = re.fullmatch(r"\d+:\d+-(\d+)", str(m.get("ref") or ""))
        if r:
            last = max(a, int(r.group(1)))
        out += [(s, n, float(m.get("start") or 0.0)) for n in range(a, last + 1)]
    return out


def _seg_key(text: str) -> str:
    from ..pipeline.translate import _seg_key as key

    return key(text)


def segment_rows(o: SessionOutput) -> list[dict]:
    """Rows of `transcript_segments` for a session output: the transcript in the language Whisper
    detected, plus each translation (translations.json, translations_ar.json) in its own language."""
    tr = o.read(o.transcript, {})
    lang = (tr.get("language") or "").strip().lower()[:8]
    cache = {}
    for path in (o.translations, o.translations_ar):
        cache.update((o.read(path, {}) or {}).get("seg") or {})
    rows = []
    for i, s in enumerate(tr.get("segments") or []):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        base = {"seg_index": i, "start": float(s.get("start") or 0.0), "end": float(s.get("end") or 0.0)}
        rows.append({**base, "source": "transcript", "lang": script_lang(text, lang), "text": text})
        for tlang, ttext in sorted((cache.get(_seg_key(text)) or {}).items()):
            if tlang != lang and isinstance(ttext, str) and ttext.strip() and re.fullmatch(r"[a-z]{2,3}", tlang):
                rows.append({**base, "source": "translation", "lang": tlang, "text": ttext.strip()})
    return rows


_ARABIC_SCRIPT_LANGS = {"ar", "ur", "fa", "ps", "ug", "sd", "ku"}


def script_lang(text: str, lang: str) -> str:
    """Whisper gives one language per session; a khutba switches between Arabic and the local
    language. A segment whose script contradicts it is indexed without a language ('simple')."""
    arabic = bool(_ARABIC.search(text))
    if arabic != (lang in _ARABIC_SCRIPT_LANGS):
        return "ar" if arabic and lang not in _ARABIC_SCRIPT_LANGS else ""
    return lang


def _norm_for(f: Features, text: str) -> str:
    n = normalize(text)
    # PostgreSQL without unaccent: accents are folded here (the query is folded the same way)
    return strip_accents(n) if f.dialect == "postgresql" and not f.unaccent else n


def is_indexed(conn, sermon_id: int, rows: list[dict], cites: list) -> bool:
    n = conn.execute(sa.select(sa.func.count()).select_from(TS).where(TS.c.sermon_id == sermon_id)).scalar()
    v = conn.execute(sa.select(sa.func.count()).select_from(VC).where(VC.c.sermon_id == sermon_id)).scalar()
    return n == len(rows) and v == len(cites)


def index_sermon(conn, k: dict, o: SessionOutput | None = None, force: bool = False) -> int:
    """(Re)builds the search rows of a sermon from its pipeline output. Unchanged counts are left
    alone (the import runs at every scan). Returns the number of segments written."""
    o = o or SessionOutput(k["output_dir"])
    rows = segment_rows(o)
    cites = _citations(o.read(o.verses, []) or [])
    if not force and is_indexed(conn, k["id"], rows, cites):
        return 0
    f = features(conn)
    conn.execute(sa.delete(TS).where(TS.c.sermon_id == k["id"]))
    conn.execute(sa.delete(VC).where(VC.c.sermon_id == k["id"]))
    if rows:
        conn.execute(
            sa.insert(TS),
            [{**r, "mosque_id": k["mosque_id"], "sermon_id": k["id"], "norm": _norm_for(f, r["text"])} for r in rows],
        )
        if f.dialect == "postgresql":
            conn.execute(
                sa.update(TS)
                .where(TS.c.sermon_id == k["id"])
                .values(tsv=sa.func.to_tsvector(_pg_config(f, TS.c.lang), _unaccent(f, TS.c.norm)))
            )
    if cites:
        conn.execute(
            sa.insert(VC),
            [{"mosque_id": k["mosque_id"], "sermon_id": k["id"], "surah": s, "ayah": a, "t": t} for s, a, t in cites],
        )
    return len(rows)


def index_all(conn, force: bool = False, log=print) -> tuple[int, int]:
    """Backfill: every imported sermon whose output is still on disk.
    Returns (sermons written, sermons already up to date)."""
    n = up_to_date = 0
    for k in conn.execute(sa.select(Khutba.__table__).order_by(Khutba.id)).mappings():
        o = SessionOutput(k["output_dir"] or "")
        if not k["output_dir"] or not o.is_transcribed():
            continue
        w = index_sermon(conn, dict(k), o, force=force)
        conn.commit()
        if w:
            log(_("sermon {id}: {n} segment(s) indexed", id=k["id"], n=w))
            n += 1
        else:
            up_to_date += 1
    return n, up_to_date


def pending_embeddings(conn) -> int:
    q = sa.select(sa.func.count()).select_from(TS).where(TS.c.source == "transcript", TS.c.embedding.is_(None))
    return conn.execute(q).scalar() or 0


#: Semantic chunks: consecutive segments joined up to ~30 words. A transcript segment is often a
#: few words (« Donc, », a Quranic fragment): alone it matches everything; within its context, its meaning.
CHUNK_WORDS = 30
CHUNK_MAX_SEGMENTS = 6
CHUNK_MIN_WORDS = 8


def chunks(rows: list) -> list[list]:
    """Groups the transcript segments of a sermon (ordered) into semantic chunks."""
    out: list[list] = []
    cur: list = []
    words = 0
    for r in rows:
        cur.append(r)
        words += len(r.text.split())
        if words >= CHUNK_WORDS or len(cur) >= CHUNK_MAX_SEGMENTS:
            out.append(cur)
            cur, words = [], 0
    if cur:
        if out and words < CHUNK_MIN_WORDS:
            out[-1].extend(cur)
        else:
            out.append(cur)
    return out


def degenerate(text: str) -> bool:
    """A Whisper repetition loop: many words, few distinct ones."""
    words = re.findall(r"\w+", normalize(text))
    return len(words) >= 12 and len(set(words)) / len(words) < 0.3


def text_lang(text: str, lang: str) -> str:
    """Language shown for a passage: the script that dominates it, then the session language."""
    letters = [c for c in text if c.isalpha()]
    arabic = sum(1 for c in letters if _ARABIC.match(c)) > len(letters) / 2
    return script_lang("ا" if arabic else "a", lang)


def embed_batch(conn, model) -> int:
    """Embeds the next sermon still without vectors: one vector per chunk, on its first segment; the
    other segments of the chunk get an empty marker (covered). Returns the segments processed."""
    sid = conn.execute(
        sa.select(TS.c.sermon_id)
        .where(TS.c.source == "transcript", TS.c.embedding.is_(None))
        .order_by(TS.c.sermon_id)
        .limit(1)
    ).scalar()
    if sid is None:
        return 0
    rows = conn.execute(
        sa.select(TS.c.id, TS.c.text).where(TS.c.sermon_id == sid, TS.c.source == "transcript").order_by(TS.c.seg_index)
    ).all()
    groups = chunks(rows)
    vecs = model.encode([" ".join(r.text for r in g) for g in groups], kind="passage")
    f = features(conn)
    for g, v in zip(groups, vecs, strict=True):
        # a transcription loop (« shukr, shukr, shukr… ») is not a passage: covered, never matched
        loop = degenerate(" ".join(r.text for r in g))
        conn.execute(sa.update(TS).where(TS.c.id == g[0].id).values(embedding=b"" if loop else embeddings.to_bytes(v)))
        if len(g) > 1:
            conn.execute(sa.update(TS).where(TS.c.id.in_([r.id for r in g[1:]])).values(embedding=b""))
    if f.vector:
        sv = _vectors(f.vector)
        conn.execute(sa.delete(sv).where(sv.c.segment_id.in_([r.id for r in rows])))
        kept = [
            {"segment_id": g[0].id, "embedding": v}
            for g, v in zip(groups, vecs, strict=True)
            if not degenerate(" ".join(r.text for r in g))
        ]
        if kept:
            conn.execute(sa.insert(sv), kept)
    conn.commit()
    return len(rows)


class _Vector(sa.types.UserDefinedType):
    """pgvector `vector`, qualified by the extension schema (outside the search_path of a tenant)."""

    cache_ok = True

    def __init__(self, schema: str = "public"):
        self.schema = schema

    def get_col_spec(self, **kw):
        return f'"{self.schema}".vector({embeddings.DIM})'

    def bind_processor(self, dialect):
        return lambda v: None if v is None else "[" + ",".join(f"{x:.6f}" for x in np.asarray(v).ravel()) + "]"


def _vectors(schema: str = "public") -> sa.Table:
    return sa.Table(
        "segment_vectors",
        sa.MetaData(),
        sa.Column("segment_id", sa.Integer),
        sa.Column("embedding", _Vector(schema)),
    )


def ensure_vectors(conn) -> bool:
    """PostgreSQL: creates segment_vectors when pgvector appeared after the migration."""
    f = features(conn)
    if f.dialect != "postgresql" or f.vector:
        return bool(f.vector)
    ok = create_vector_table(conn)
    conn.commit()
    reset_features()
    return ok


# --------------------------------------------------------------------------- query parsing


def _name_key(s: str) -> str:
    s = strip_accents(fold_arabic(s)).casefold()
    s = re.sub(r"^(?:sourate|surah|surat|sura|soorah|سوره)\s+", "", s.strip())
    s = re.sub(r"^(?:the|la|le|les|l)\b\s*'?\s*", "", s)
    s = re.sub(r"^(?:al|an|ar|as|at|ad|adh|az|ash|aal|ath)[\s-]+", "", s)
    s = re.sub(r"^ال", "", s)
    return re.sub(r"[^\w]+", "", s)


_SURAH_KEYS: list[tuple[int, str]] = [
    (n, k) for n, ar, tr, en, fr, _ in SURAHS for k in {_name_key(ar), _name_key(tr), _name_key(en), _name_key(fr)} if k
]
_AYAHS = {n: count for n, *_rest, count in SURAHS}
_REF = re.compile(r"^\s*(\d{1,3})\s*[:.،,/]\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*$")


def _surah(name: str) -> int | None:
    key = _name_key(name)
    if len(key) < 3:
        return None
    for n, k in _SURAH_KEYS:
        if k == key:
            return n
    best = max(((fuzz.ratio(key, k), n) for n, k in _SURAH_KEYS if len(k) >= 4), default=(0, None))
    return best[1] if best[0] >= 90 and len(key) >= 5 else None


def parse_verse(q: str) -> tuple[int, int | None, int | None] | None:
    """(surah, first verse, last verse) of a verse query; None when the query is not one."""
    q = unicodedata.normalize("NFKC", q)
    m = _REF.match(q)
    if m:
        s, a = int(m.group(1)), int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        if 1 <= s <= 114 and 1 <= a <= _AYAHS[s]:
            return s, a, min(max(a, b), _AYAHS[s])
        return None
    m = re.match(r"^(.*?)[\s:]*(\d{1,3})?(?:\s*[-–]\s*(\d{1,3}))?\s*$", q.strip())
    if not m or not m.group(1).strip():
        return None
    s = _surah(m.group(1))
    if s is None:
        return None
    if m.group(2):
        a = int(m.group(2))
        if not 1 <= a <= _AYAHS[s]:
            return None
        b = int(m.group(3)) if m.group(3) else a
        return s, a, min(max(a, b), _AYAHS[s])
    return s, None, None


# --------------------------------------------------------------------------- snippets


def _close(word: str, term: str) -> bool:
    if word == term:
        return True
    w, t = len(word), len(term)
    if min(w, t) < 4:
        return False
    stem = max(4, min(w, t) - 2)
    return word[:stem] == term[:stem]


def highlight(text: str, qterms: list[str], width: int = 220) -> list[dict]:
    """Parts of `text` ({text, hit}) with the query words marked; cut around the first hit."""
    qterms = [t for t in qterms if len(t) > 2] or qterms
    spans = []
    for m in _WORD.finditer(text):
        w = strip_accents(_word(fold_arabic(m.group()).casefold()))
        if any(_close(w, t) for t in qterms):
            spans.append((m.start(), m.end()))
    lo, hi = 0, len(text)
    if len(text) > width:
        first = spans[0][0] if spans else 0
        lo = max(0, first - width // 3)
        hi = min(len(text), lo + width)
        lo = text.rfind(" ", 0, lo) + 1 if lo else 0
    parts: list[dict] = []
    pos = lo
    for a, b in spans:
        if a < lo or b > hi:
            continue
        if a > pos:
            parts.append({"text": text[pos:a], "hit": False})
        parts.append({"text": text[a:b], "hit": True})
        pos = b
    if pos < hi:
        parts.append({"text": text[pos:hi], "hit": False})
    if lo > 0:
        parts.insert(0, {"text": "… ", "hit": False})
    if hi < len(text):
        parts.append({"text": " …", "hit": False})
    return parts


# --------------------------------------------------------------------------- search

#: multilingual-e5-small cosines are compressed (unrelated text ≈ 0.78, the best chunk ≈ 0.83–0.90
#: on the khutbas measured): an absolute floor, then only what is close to the best chunk.
SEMANTIC_MIN = 0.80
SEMANTIC_BAND = 0.02
SEMANTIC_MAX = 8
CANDIDATES = 200


@dataclass
class Hit:
    sermon_id: int
    type: str  # word | verse | semantic
    start: float
    score: float
    text: str = ""
    lang: str = ""
    verse: tuple[int, int] | None = None


def _scope(where: list):
    return sa.select(Khutba.id).where(*where)


def word_hits(conn, where: list, q: str, limit: int = CANDIDATES) -> list[Hit]:
    qt = terms(q)
    if not qt:
        return []
    f = features(conn)
    cols = (TS.c.sermon_id, TS.c.start, TS.c.text, TS.c.lang)
    if f.dialect == "sqlite":
        fts = sa.table("transcript_segments_fts", sa.column("rowid"), sa.column("norm"), sa.column("rank"))
        # prefix match from 4 letters (« épreuve » finds « épreuves »); 1–2 letter words (d', l', le)
        # are dropped when the query has real words, as PostgreSQL does with its stop words
        long = [t for t in qt if len(t) > 2] or qt
        match = " ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in long)
        stmt = (
            sa.select(*cols, fts.c.rank.label("rank"))
            .select_from(TS.join(fts, fts.c.rowid == TS.c.id))
            .where(fts.c.norm.op("MATCH")(match), TS.c.sermon_id.in_(_scope(where)))
            .order_by(fts.c.rank)
            .limit(limit)
        )
        rows = conn.execute(stmt).all()
        # bm25: lower is better, negative; rescaled to 0..1 within the result set
        best = min((r.rank for r in rows), default=0.0) or -1.0
        return [Hit(r.sermon_id, "word", r.start, 2.0 + (r.rank / best), r.text, r.lang) for r in rows]
    if f.dialect != "postgresql":
        return []
    langs = list(conn.execute(sa.select(TS.c.lang).where(TS.c.sermon_id.in_(_scope(where))).distinct()).scalars())
    qtext = " ".join(qt)
    cfgs = sorted({f.configs.get(lang, "simple") for lang in langs} | {"simple"})
    tsq = None
    for c in cfgs:
        part = sa.func.websearch_to_tsquery(sa.cast(c, REGCONFIG), qtext)
        tsq = part if tsq is None else tsq.op("||")(part)
    rank = sa.func.ts_rank_cd(TS.c.tsv, tsq)
    stmt = (
        sa.select(*cols, rank.label("rank"))
        .where(TS.c.tsv.op("@@")(tsq), TS.c.sermon_id.in_(_scope(where)))
        .order_by(rank.desc())
        .limit(limit)
    )
    rows = conn.execute(stmt).all()
    best = max((r.rank for r in rows), default=0.0) or 1.0
    return [Hit(r.sermon_id, "word", r.start, 2.0 + r.rank / best, r.text, r.lang) for r in rows]


def verse_hits(conn, where: list, q: str) -> list[Hit]:
    ref = parse_verse(q)
    if not ref:
        return []
    s, a, b = ref
    cond = [VC.c.surah == s, VC.c.sermon_id.in_(_scope(where))]
    if a is not None:
        cond.append(VC.c.ayah.between(a, b))
    rows = conn.execute(
        sa.select(VC.c.sermon_id, VC.c.surah, VC.c.ayah, VC.c.t.label("at")).where(*cond).order_by(VC.c.t)
    ).all()
    return [Hit(r.sermon_id, "verse", r.at, 3.0, verse=(r.surah, r.ayah)) for r in rows]


def _chunk_text(conn, sermon_id: int, seg_index: int) -> str:
    """Text of the semantic chunk starting at a segment: it and the covered segments after it."""
    rows = conn.execute(
        sa.select(TS.c.text, TS.c.embedding)
        .where(
            TS.c.sermon_id == sermon_id,
            TS.c.source == "transcript",
            TS.c.seg_index >= seg_index,
        )
        .order_by(TS.c.seg_index)
        .limit(CHUNK_MAX_SEGMENTS * 2)
    ).all()
    parts = []
    for i, r in enumerate(rows):
        if i and r.embedding != b"":
            break
        parts.append(r.text)
    return " ".join(parts)


def semantic_hits(conn, where: list, q: str, limit: int = 50) -> list[Hit]:
    model = embeddings.get()
    if model is None or not q.strip():
        return []
    qv = model.encode([q.strip()], kind="query")[0]
    f = features(conn)
    cols = (TS.c.sermon_id, TS.c.seg_index, TS.c.start, TS.c.lang)
    if f.vector:
        sv = _vectors(f.vector)
        vt = _Vector(f.vector)
        dist = sv.c.embedding.op(f'OPERATOR("{f.vector}".<=>)', return_type=sa.Float)(sa.cast(sa.literal(qv, vt), vt))
        rows = conn.execute(
            sa.select(*cols, (1 - dist).label("score"))
            .select_from(TS.join(sv, sv.c.segment_id == TS.c.id))
            .where(TS.c.sermon_id.in_(_scope(where)))
            .order_by(dist)
            .limit(limit)
        ).all()
        scored = [(r, float(r.score)) for r in rows]
    else:
        rows = conn.execute(
            sa.select(*cols, TS.c.embedding).where(
                TS.c.source == "transcript",
                sa.func.length(TS.c.embedding) > 0,
                TS.c.sermon_id.in_(_scope(where)),
            )
        ).all()
        if not rows:
            return []
        mat = np.vstack([embeddings.from_bytes(r.embedding) for r in rows])
        sims = mat @ qv
        top = np.argsort(-sims)[:limit]
        scored = [(rows[i], float(sims[i])) for i in top]
    if not scored:
        return []
    floor = max(SEMANTIC_MIN, scored[0][1] - SEMANTIC_BAND)
    return [
        Hit(r.sermon_id, "semantic", r.start, sc, text, text_lang(text, r.lang))
        for r, sc in scored[:SEMANTIC_MAX]
        if sc >= floor
        for text in [_chunk_text(conn, r.sermon_id, r.seg_index)]
    ]


def search(conn, where: list, q: str, limit: int = 20, per_sermon: int = 3, semantic: bool = True) -> dict:
    """Results grouped by sermon, best first. `where`: conditions on `Khutba` (mosque, imam)."""
    q = (q or "").strip()[:200]
    timings = {}
    t0 = time.perf_counter()
    hits = verse_hits(conn, where, q)
    timings["verse_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    t0 = time.perf_counter()
    hits += word_hits(conn, where, q)
    timings["word_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if semantic and not parse_verse(q):  # a verse reference has no « meaning » to look for
        t0 = time.perf_counter()
        hits += semantic_hits(conn, where, q)
        timings["semantic_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    groups: dict[int, dict[float, Hit]] = {}
    for h in hits:
        g = groups.setdefault(h.sermon_id, {})
        key = round(h.start, 1)
        if key not in g or h.score > g[key].score:
            g[key] = h
    ranked = sorted(
        groups.items(),
        key=lambda kv: -(max(h.score for h in kv[1].values()) + 0.05 * min(len(kv[1]), 5)),
    )[:limit]
    ids = [sid for sid, _ in ranked]
    meta = {}
    if ids:
        k, u = Khutba.__table__, User.__table__
        stmt = (
            sa.select(k.c.id, k.c.label, k.c.title, k.c.speaker, k.c.date, k.c.kind, u.c.display_name)
            .select_from(k.outerjoin(u, u.c.id == k.c.imam_id))
            .where(k.c.id.in_(ids))
        )
        meta = {r.id: r for r in conn.execute(stmt)}
    qt = terms(q)
    results = []
    for sid, g in ranked:
        m = meta.get(sid)
        if m is None:
            continue
        top = sorted(g.values(), key=lambda h: -h.score)[:per_sermon]
        results.append(
            {
                "id": sid,
                "label": m.label,
                "title": m.title or "",
                "speaker": m.speaker or m.display_name or "",
                "date": m.date,
                "kind": m.kind or "khutba",
                "hits": [_hit_json(h, qt) for h in sorted(top, key=lambda h: h.start)],
            }
        )
    return {"q": q, "results": results, "semantic": semantic and embeddings.get() is not None, "timings": timings}


def _hit_json(h: Hit, qt: list[str]) -> dict:
    out = {"type": h.type, "start": round(h.start, 2), "lang": h.lang, "score": round(h.score, 3)}
    if h.verse:
        s, a = h.verse
        _n, ar, tr, en, fr, _c = SURAHS[s - 1]
        out["verse"] = {"surah": s, "ayah": a, "name_ar": ar, "name": tr, "name_en": en, "name_fr": fr}
        out["snippet"] = []
    else:
        out["snippet"] = highlight(h.text, qt if h.type == "word" else [])
    return out
