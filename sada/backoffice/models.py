"""Modèles SQLAlchemy 2.0 (Core) de la base du back-office.

Les tables sont décrites ici, une seule source de vérité pour le schéma. La
couche de migrations (`migrations.py`) s'appuie sur ce `metadata` pour créer le
schéma initial ; les requêtes de l'application restent en SQL texte via
`db.one` / `db.all_` (la surface publique de `db.py` est inchangée).

Toute table métier porte `mosque_id` : c'est la clé de cloisonnement (locataire).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()


def _d(sql: str) -> sa.TextClause:
    """Valeur par défaut SQL littérale (ex. `_d("'khutba'")` -> `DEFAULT 'khutba'`)."""
    return sa.text(sql)


mosques = sa.Table(
    "mosques",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("slug", sa.Text, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("timezone", sa.Text, nullable=False, server_default=_d("'Europe/Paris'")),
    sa.Column("mawaqit_url", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("orchestrator_url", sa.Text, nullable=False, server_default=_d("'http://127.0.0.1:8088'")),
    sa.Column("orchestrator_token", sa.Text, nullable=False, server_default=_d("''")),
    # Empreinte (sha256) du jeton que l'orchestrateur de la mosquée présente au back-office.
    sa.Column("agent_token_hash", sa.Text, nullable=False, server_default=_d("''")),
    # Last Mawaqit times (JSON) and when they were fetched: fallback if the site is down.
    # Empreinte de la clé de diffusion (OBS -> serveur de réception).
    sa.Column("publish_key_hash", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("mawaqit_cache", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("mawaqit_cached_at", sa.Float, nullable=False, server_default=_d("0")),
    sa.Column("state_dir", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("output_dir", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("publish_dir", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("default_imam_id", sa.Integer),
    sa.Column("auto_import", sa.Integer, nullable=False, server_default=_d("1")),
    sa.Column("created_at", sa.Text, nullable=False),
    # ajoutées après coup (voir migrations 3) : présentes dès la création d'une base neuve
    sa.Column("logo_path", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("onboarded_at", sa.Text, nullable=False, server_default=_d("''")),
    # couleurs de la mosquée dans les clips (migration 5), vides = charter actuelle
    sa.Column("brand_green", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("brand_gold", sa.Text, nullable=False, server_default=_d("''")),
    # habillage des vidéos (migration 8) : derniers réglages choisis, JSON ; vide = réglages par défaut
    sa.Column("habillage_defaults", sa.Text, nullable=False, server_default=_d("''")),
)

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column(
        "username", sa.Text(collation="NOCASE").with_variant(sa.Text(), "postgresql"), nullable=False, unique=True
    ),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("role", sa.Text, sa.CheckConstraint("role IN ('admin', 'responsable', 'imam')"), nullable=False),
    sa.Column("active", sa.Integer, nullable=False, server_default=_d("1")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("last_login_at", sa.Text),
    # Permissions choisies à la main (liste JSON) ; vide : celles du rôle.
    sa.Column("permissions", sa.Text, nullable=False, server_default=_d("''")),
    # Compte créé ou mot de passe fixé par un admin : la personne doit choisir le sien avant tout.
    sa.Column("must_change_password", sa.Integer, nullable=False, server_default=_d("0")),
    # Administrateur de la plateforme : règle ce qui est commun à toutes les mosquées (modèle,
    # transcription). Jamais attribué par un formulaire, seulement en ligne de commande.
    sa.Column("platform_admin", sa.Integer, nullable=False, server_default=_d("0")),
)

auth_sessions = sa.Table(
    "auth_sessions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("csrf", sa.Text, nullable=False),
    sa.Column("created_at", sa.REAL, nullable=False),
    sa.Column("expires_at", sa.REAL, nullable=False),
    sa.Column("last_seen_at", sa.REAL, nullable=False),
    sa.Column("ip", sa.Text),
    sa.Column("user_agent", sa.Text),
    sa.Column("flash", sa.Text),
    # Mosque the session is opened on (NULL until chosen) and whether the operator entered it as
    # support (every write is then audited).
    sa.Column("mosque_id", sa.Integer),
    sa.Column("support", sa.Integer, nullable=False, server_default=_d("0")),
)

# Extra mosques a person belongs to, besides the home one (users.mosque_id).
memberships = sa.Table(
    "memberships",
    metadata,
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id", ondelete="CASCADE"), primary_key=True),
)

khutbas = sa.Table(
    "khutbas",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("run_id", sa.Text, nullable=False),
    sa.Column("session_index", sa.Integer, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("planned", sa.Text),
    sa.Column("date", sa.Text),
    sa.Column("start_clock", sa.Text),
    sa.Column("end_clock", sa.Text),
    sa.Column("duration_s", sa.REAL),
    sa.Column("imam_id", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("speaker", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("theme", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("output_dir", sa.Text, nullable=False),
    sa.Column("recording", sa.Text),
    sa.Column("start_pos", sa.REAL),
    sa.Column("end_pos", sa.REAL),
    sa.Column("video_path", sa.Text),
    sa.Column("verses_count", sa.Integer, nullable=False, server_default=_d("0")),
    # ajoutées après coup (voir migration 2)
    sa.Column("kind", sa.Text, nullable=False, server_default=_d("'khutba'")),
    sa.Column("title", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("series", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("session_no", sa.Integer),
    sa.Column("imported_at", sa.Text, nullable=False),
    sa.UniqueConstraint("mosque_id", "run_id", "session_index"),
)

clips = sa.Table(
    "clips",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("khutba_id", sa.Integer, sa.ForeignKey("khutbas.id"), nullable=False),
    sa.Column("idx", sa.Integer, nullable=False),
    sa.Column("kind", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("why", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("start", sa.REAL, nullable=False),
    sa.Column("end", sa.REAL, nullable=False),
    sa.Column("file_path", sa.Text, nullable=False),
    sa.Column("video_source", sa.Text),
    sa.Column("source_offset", sa.REAL),
    sa.Column("crop", sa.Text),
    sa.Column(
        "status",
        sa.Text,
        sa.CheckConstraint("status IN ('a_valider', 'valide', 'rejete', 'publie')"),
        nullable=False,
        server_default=_d("'a_valider'"),
    ),
    sa.Column("version", sa.Integer, nullable=False, server_default=_d("1")),
    sa.Column("needs_regen", sa.Integer, nullable=False, server_default=_d("0")),
    sa.Column("regen_state", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("regen_error", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("decided_by", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("decided_at", sa.Text),
    sa.Column("reject_reason", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    # ajoutées après coup (voir migration 7) : clip créé à la main, bornes choisies par un humain
    sa.Column(
        "origin", sa.Text, sa.CheckConstraint("origin IN ('ia', 'manuel')"), nullable=False, server_default=_d("'ia'")
    ),
    sa.Column("title", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("bounds_by", sa.Integer, sa.ForeignKey("users.id")),
    sa.UniqueConstraint("khutba_id", "idx"),
)

corrections = sa.Table(
    "corrections",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("khutba_id", sa.Integer, sa.ForeignKey("khutbas.id"), nullable=False),
    sa.Column("seg_index", sa.Integer, nullable=False),
    sa.Column("field", sa.Text, sa.CheckConstraint("field IN ('text', 'fr')"), nullable=False),
    sa.Column("original", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.UniqueConstraint("khutba_id", "seg_index", "field"),
)

# Sous-titres ajoutés à la main à un instant précis (passage que la transcription a sauté, souvent de
# l'arabe) : appliqués en mémoire comme les corrections, les fichiers du pipeline restent intacts.
added_subtitles = sa.Table(
    "added_subtitles",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("khutba_id", sa.Integer, sa.ForeignKey("khutbas.id"), nullable=False),
    sa.Column("start", sa.Float, nullable=False),
    sa.Column("end", sa.Float, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("created_at", sa.Text, nullable=False),
)

# Vidéo « habillée » d'une session (migration 8) : une par session, refaite à chaque nouveau rendu.
habillages = sa.Table(
    "habillages",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("khutba_id", sa.Integer, sa.ForeignKey("khutbas.id"), nullable=False, unique=True),
    sa.Column("options", sa.Text, nullable=False, server_default=_d("'{}'")),
    sa.Column(
        "status",
        sa.Text,
        sa.CheckConstraint("status IN ('', 'en_cours', 'pret', 'echec')"),
        nullable=False,
        server_default=_d("''"),
    ),
    sa.Column("progress", sa.Float, nullable=False, server_default=_d("0")),
    sa.Column("error", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("file_path", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("duration_s", sa.Float),
    sa.Column("preview_at", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("started_at", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("finished_at", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("updated_at", sa.Text, nullable=False),
)

connectors = sa.Table(
    "connectors",
    metadata,
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False, primary_key=True),
    sa.Column("key", sa.Text, nullable=False, primary_key=True),
    sa.Column("enabled", sa.Integer, nullable=False, server_default=_d("0")),
    sa.Column("settings", sa.Text, nullable=False, server_default=_d("'{}'")),
)

notifications = sa.Table(
    "notifications",
    metadata,
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), primary_key=True),
    sa.Column(
        "mode",
        sa.Text,
        sa.CheckConstraint("mode IN ('', 'webhook', 'telegram')"),
        nullable=False,
        server_default=_d("''"),
    ),
    sa.Column("webhook", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("telegram_token", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("telegram_chat", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("updated_at", sa.Text),
)

live_targets = sa.Table(
    "live_targets",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("label", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("enabled", sa.Integer, nullable=False, server_default=_d("1")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text),
)

# Programme of each mosque (dars, conferences, hand-entered khutbas): one row per event, the
# entry kept as JSON so the engine (`sada.events`) stays the only judge of its validity.
events = sa.Table(
    "events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("uid", sa.Text, nullable=False),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("position", sa.Integer, nullable=False, server_default=_d("0")),
    sa.UniqueConstraint("mosque_id", "uid"),
)

live_input = sa.Table(
    "live_input",
    metadata,
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), primary_key=True),
    sa.Column(
        "mode", sa.Text, sa.CheckConstraint("mode IN ('camera', 'push')"), nullable=False, server_default=_d("'camera'")
    ),
    sa.Column("url", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("updated_at", sa.Text),
)

storage = sa.Table(
    "storage",
    metadata,
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), primary_key=True),
    sa.Column("endpoint", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("region", sa.Text, nullable=False, server_default=_d("'auto'")),
    sa.Column("bucket", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("access_key", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("secret_key", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("prefix", sa.Text, nullable=False, server_default=_d("'rediffusions/'")),
    sa.Column("enabled", sa.Integer, nullable=False, server_default=_d("0")),
    sa.Column("updated_at", sa.Text),
)

screen_texts = sa.Table(
    "screen_texts",
    metadata,
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False, primary_key=True),
    sa.Column("screen", sa.Text, nullable=False, primary_key=True),
    sa.Column("slot", sa.Text, nullable=False, primary_key=True),
    sa.Column("text", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("updated_at", sa.Text),
)

publications = sa.Table(
    "publications",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("clip_id", sa.Integer, sa.ForeignKey("clips.id"), nullable=False),
    sa.Column("clip_version", sa.Integer, nullable=False),
    sa.Column("connector", sa.Text, nullable=False),
    sa.Column("status", sa.Text, sa.CheckConstraint("status IN ('publie', 'refuse', 'erreur')"), nullable=False),
    sa.Column("location", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("caption", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("message", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("created_at", sa.Text, nullable=False),
)

audit = sa.Table(
    "audit",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id")),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("object_type", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("object_id", sa.Integer),
    sa.Column("details", sa.Text, nullable=False, server_default=_d("'{}'")),
    sa.Column("ip", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
)

# Search index (migration 18): one row per transcript segment and per translation of it, in its own
# language. Filled at import from the pipeline output; `search.py` reads and writes it.
# `norm` is the normalised text (Arabic folded, lower case) the full-text index is built on;
# `tsv` is its PostgreSQL tsvector (NULL on SQLite, where an FTS5 table mirrors `norm`);
# `embedding` is the float32 vector of the semantic search, filled after the live by `traitement`.
transcript_segments = sa.Table(
    "transcript_segments",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("sermon_id", sa.Integer, sa.ForeignKey("khutbas.id", ondelete="CASCADE"), nullable=False),
    sa.Column("seg_index", sa.Integer, nullable=False),
    sa.Column("source", sa.Text, nullable=False, server_default=_d("'transcript'")),
    sa.Column("start", sa.Float, nullable=False),
    sa.Column("end", sa.Float, nullable=False),
    sa.Column("lang", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("norm", sa.Text, nullable=False, server_default=_d("''")),
    sa.Column("tsv", sa.Text().with_variant(postgresql.TSVECTOR(), "postgresql")),
    sa.Column("embedding", sa.LargeBinary),
)

# Quran verses recited or quoted in a session, language independent (verse search).
verse_citations = sa.Table(
    "verse_citations",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mosque_id", sa.Integer, sa.ForeignKey("mosques.id"), nullable=False),
    sa.Column("sermon_id", sa.Integer, sa.ForeignKey("khutbas.id", ondelete="CASCADE"), nullable=False),
    sa.Column("surah", sa.Integer, nullable=False),
    sa.Column("ayah", sa.Integer, nullable=False),
    sa.Column("t", sa.Float, nullable=False),
)

# Full-text side of the search index, specific to each engine. SQLite: an FTS5 table mirroring `norm`
# (kept in step by triggers). PostgreSQL: a GIN index on `tsv`, the `unaccent` extension and, when
# the server ships pgvector, a `segment_vectors` table with an HNSW index. An extension the database
# user may not create is skipped: the search falls back (accents folded in Python, brute-force cosine).
_SQLITE_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS transcript_segments_fts USING fts5(norm, content='transcript_segments',"
    " content_rowid='id', tokenize='unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS transcript_segments_ai AFTER INSERT ON transcript_segments BEGIN"
    " INSERT INTO transcript_segments_fts(rowid, norm) VALUES (new.id, new.norm); END",
    "CREATE TRIGGER IF NOT EXISTS transcript_segments_ad AFTER DELETE ON transcript_segments BEGIN"
    " INSERT INTO transcript_segments_fts(transcript_segments_fts, rowid, norm) VALUES ('delete', old.id, old.norm);"
    " END",
    "CREATE TRIGGER IF NOT EXISTS transcript_segments_au AFTER UPDATE OF norm ON transcript_segments BEGIN"
    " INSERT INTO transcript_segments_fts(transcript_segments_fts, rowid, norm) VALUES ('delete', old.id, old.norm);"
    " INSERT INTO transcript_segments_fts(rowid, norm) VALUES (new.id, new.norm); END",
)
EMBEDDING_DIM = 384


def pg_extension(conn, name: str, create: bool = False) -> str | None:
    """Schema of the PostgreSQL extension `name`, created in `public` if allowed; None if absent."""
    ext = sa.table("pg_extension", sa.column("extname"), sa.column("extnamespace"), schema="pg_catalog")
    ns = sa.table("pg_namespace", sa.column("oid"), sa.column("nspname"), schema="pg_catalog")
    q = sa.select(ns.c.nspname).select_from(ext.join(ns, ns.c.oid == ext.c.extnamespace)).where(ext.c.extname == name)
    schema = conn.execute(q).scalar()
    if schema or not create:
        return schema
    try:
        with conn.begin_nested():
            conn.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {name} SCHEMA public"))
    except sa.exc.DBAPIError:
        return None  # not shipped by the server, or the database user may not create it
    return conn.execute(q).scalar()


def create_vector_table(conn) -> bool:
    """`segment_vectors` (pgvector + HNSW), when the extension exists. True if the table is usable."""
    schema = pg_extension(conn, "vector", create=True)
    if not schema:
        return False
    conn.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS segment_vectors (segment_id INTEGER PRIMARY KEY"
            f' REFERENCES transcript_segments(id) ON DELETE CASCADE, embedding "{schema}".vector({EMBEDDING_DIM}))'
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS segment_vectors_hnsw ON segment_vectors"
            f' USING hnsw (embedding "{schema}".vector_cosine_ops)'
        )
    )
    return True


@sa.event.listens_for(transcript_segments, "after_create")
def _search_extras(target, conn, **kw) -> None:
    if conn.dialect.name == "sqlite":
        for ddl in _SQLITE_SEARCH_DDL:
            conn.exec_driver_sql(ddl)
    elif conn.dialect.name == "postgresql":
        conn.execute(
            sa.text("CREATE INDEX IF NOT EXISTS transcript_segments_tsv ON transcript_segments USING gin (tsv)")
        )
        pg_extension(conn, "unaccent", create=True)
        create_vector_table(conn)


sa.Index("audit_mosque", audit.c.mosque_id, audit.c.id)
sa.Index("clips_mosque", clips.c.mosque_id, clips.c.status)
sa.Index("khutbas_mosque", khutbas.c.mosque_id, khutbas.c.date)
sa.Index("live_targets_mosque", live_targets.c.mosque_id)
sa.Index("transcript_segments_sermon", transcript_segments.c.sermon_id, transcript_segments.c.seg_index)
sa.Index("transcript_segments_mosque", transcript_segments.c.mosque_id)
sa.Index("verse_citations_ref", verse_citations.c.mosque_id, verse_citations.c.surah, verse_citations.c.ayah)
sa.Index("verse_citations_sermon", verse_citations.c.sermon_id)

# tables métier, dans l'ordre de création (dépendances : FK)
TABLES = (
    mosques,
    users,
    auth_sessions,
    memberships,
    khutbas,
    clips,
    corrections,
    added_subtitles,
    habillages,
    connectors,
    notifications,
    live_targets,
    live_input,
    storage,
    screen_texts,
    publications,
    audit,
    transcript_segments,
    verse_citations,
)


# ---------------------------------------------------------------- ORM classes
# Mapped on the tables above (single schema source); queries go through them.
from sqlalchemy.orm import DeclarativeBase  # noqa: E402


class Base(DeclarativeBase):
    metadata = metadata


class Mosque(Base):
    __table__ = mosques


class User(Base):
    __table__ = users


class AuthSession(Base):
    __table__ = auth_sessions


class Membership(Base):
    __table__ = memberships


class Khutba(Base):
    __table__ = khutbas


class Clip(Base):
    __table__ = clips


class Correction(Base):
    __table__ = corrections


class AddedSubtitle(Base):
    __table__ = added_subtitles


class Habillage(Base):
    __table__ = habillages


class Connector(Base):
    __table__ = connectors


class Notification(Base):
    __table__ = notifications


class LiveTarget(Base):
    __table__ = live_targets


class Event(Base):
    __table__ = events


class LiveInput(Base):
    __table__ = live_input


class Storage(Base):
    __table__ = storage


class ScreenText(Base):
    __table__ = screen_texts


class Publication(Base):
    __table__ = publications


class Audit(Base):
    __table__ = audit


class TranscriptSegment(Base):
    __table__ = transcript_segments


class VerseCitation(Base):
    __table__ = verse_citations
