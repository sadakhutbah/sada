"""Migrations versionnées de la base du back-office (sans dépendre d'Alembic).

Principe (façon Alembic, mais léger) :

- une table `schema_migrations(version INTEGER PRIMARY KEY, appliquee_le TEXT)`
  garde les versions déjà appliquées ;
- une liste ordonnée de migrations, chacune idempotente (`up` peut être relancée
  sans effet si elle a déjà été appliquée) ;
- `init(path)` applique celles qui manquent, et refuse une base plus récente que
  le code ;
- une base créée par l'ancien code (tables présentes, pas de `schema_migrations`)
  est adoptée sans perte de données : on enregistre la version de départ (le
  schéma initial) puis on n'applique que ce qui manque.

Le schéma initial est engendré depuis les modèles SQLAlchemy (`models.py`) : les
modèles sont la seule source de vérité ; les migrations 2 et 3 ne servent qu'aux
bases existantes auxquelles il manque des colonnes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.schema import CreateColumn

from ..i18n import _
from . import models

# bookkeeping, hors `metadata` (doit exister avant de décider des migrations)
_BOOK = sa.MetaData()
schema_migrations = sa.Table(
    "schema_migrations",
    _BOOK,
    sa.Column("version", sa.Integer, primary_key=True),
    sa.Column("appliquee_le", sa.Text, nullable=False),
)

# version d'une base existante créée par l'ancien code : les tables existent déjà.
LEGACY_BASELINE = 1


@dataclass(frozen=True)
class Migration:
    version: int
    nom: str
    up: Callable[[sa.Connection], None]


def _baseline(conn: sa.Connection) -> None:
    """Migration 1 : crée toutes les tables et index décrits par les modèles (idempotente)."""
    models.metadata.create_all(conn, checkfirst=True)


def _add_columns(conn: sa.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """Ajoute les colonnes manquantes, telles que les décrivent les modèles (seule source de vérité).

    SQLAlchemy sans Alembic n'a pas d'opération « ajouter une colonne » : la déclaration vient du
    modèle, compilée pour la base courante ; seul l'enrobage `ALTER TABLE ... ADD COLUMN` est écrit ici.
    """
    have = {c["name"] for c in sa.inspect(conn).get_columns(table)}
    t = models.metadata.tables[table]
    for col, _decl in columns:
        if col not in have:
            decl = CreateColumn(t.c[col]).compile(dialect=conn.dialect)
            conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN {decl}')


def _drop_columns(conn: sa.Connection, table: str, columns: tuple[str, ...]) -> None:
    """Retire des colonnes devenues inutiles (SQLite >= 3.35 et PostgreSQL savent le faire)."""
    have = {c["name"] for c in sa.inspect(conn).get_columns(table)}
    for col in columns:
        if col in have:
            conn.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{col}"')


def _khutbas_dars(conn: sa.Connection) -> None:
    """Migration 2 : colonnes « dars » des khutbas, ajoutées après coup."""
    _add_columns(
        conn,
        "khutbas",
        (
            ("kind", "TEXT NOT NULL DEFAULT 'khutba'"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("series", "TEXT NOT NULL DEFAULT ''"),
            ("session_no", "INTEGER"),
        ),
    )


def _mosques_logo(conn: sa.Connection) -> None:
    """Migration 3 : logo et date d'embarquement des mosquées, ajoutés après coup."""
    _add_columns(
        conn,
        "mosques",
        (
            ("logo_path", "TEXT NOT NULL DEFAULT ''"),
            ("onboarded_at", "TEXT NOT NULL DEFAULT ''"),
        ),
    )


def _tables_du_modele(conn: sa.Connection) -> None:
    """Migration 4 : rattrape les tables et index ajoutés aux modèles après le schéma initial.

    Un `CREATE ... IF NOT EXISTS`, donc sans effet sur une base déjà complète : la migration existe
    pour les bases qui ont enregistré la version 1 avant que ces tables n'apparaissent dans les
    modèles (`live_targets`, `live_input`, `storage`, `screen_texts`), où plus rien ne les créait.
    """
    _baseline(conn)


def _mosques_couleurs(conn: sa.Connection) -> None:
    """Migration 5 : couleurs de la mosquée pour l'habillage des clips, ajoutées après coup."""
    _add_columns(
        conn,
        "mosques",
        (
            ("brand_green", "TEXT NOT NULL DEFAULT ''"),
            ("brand_gold", "TEXT NOT NULL DEFAULT ''"),
        ),
    )


def _clips_manuels(conn: sa.Connection) -> None:
    """Migration 7 : clips créés à la main et bornes ajustées par un humain."""
    _add_columns(
        conn,
        "clips",
        (
            ("origin", "TEXT NOT NULL DEFAULT 'ia' CHECK (origin IN ('ia', 'manuel'))"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("created_by", "INTEGER REFERENCES users (id)"),
            ("bounds_by", "INTEGER REFERENCES users (id)"),
        ),
    )


def _habillage(conn: sa.Connection) -> None:
    """Migration 8 : habillage des vidéos (table `habillages`, réglages retenus par la mosquée)."""
    _add_columns(conn, "mosques", (("habillage_defaults", "TEXT NOT NULL DEFAULT ''"),))
    _baseline(conn)


def _users_permissions(conn: sa.Connection) -> None:
    """Migration 9 : permissions choisies compte par compte (vide : celles du rôle)."""
    _add_columns(conn, "users", (("permissions", "TEXT NOT NULL DEFAULT ''"),))


def _users_mdp_impose(conn: sa.Connection) -> None:
    """Migration 10 : mot de passe à changer à la première connexion."""
    _add_columns(conn, "users", (("must_change_password", "INTEGER NOT NULL DEFAULT 0"),))


def _users_plateforme(conn: sa.Connection) -> None:
    """Migration 11 : administrateur de la plateforme (réglages communs à toutes les mosquées).

    Une instance à une seule mosquée garde son fonctionnement : ses administrateurs le deviennent.
    """
    _add_columns(conn, "users", (("platform_admin", "INTEGER NOT NULL DEFAULT 0"),))
    mosques, users = models.metadata.tables["mosques"], models.metadata.tables["users"]
    if conn.scalar(sa.select(sa.func.count()).select_from(mosques)) == 1:
        conn.execute(sa.update(users).where(users.c.role == "admin").values(platform_admin=1))


def _mosques_jeton_agent(conn: sa.Connection) -> None:
    """Migration 12 : jeton propre à l'orchestrateur de chaque mosquée (empreinte seulement)."""
    _add_columns(conn, "mosques", (("agent_token_hash", "TEXT NOT NULL DEFAULT ''"),))


def _events_en_base(conn: sa.Connection) -> None:
    """Migration 13 : programme des cours en base, par mosquée (remplace `events.json`).

    Le fichier existant est repris par le back-office au premier accès (`events_store.adopt`).
    """
    models.metadata.tables["events"].create(conn, checkfirst=True)


def _mosques_cache_mawaqit(conn: sa.Connection) -> None:
    """Migration 14 : cache des horaires Mawaqit par mosquée (remplace `cache/mawaqit.json`)."""
    _add_columns(
        conn,
        "mosques",
        (("mawaqit_cache", "TEXT NOT NULL DEFAULT ''"), ("mawaqit_cached_at", "REAL NOT NULL DEFAULT 0")),
    )


def _mosques_cle_diffusion(conn: sa.Connection) -> None:
    """Migration 15 : clé de diffusion de chaque mosquée (empreinte seulement)."""
    _add_columns(conn, "mosques", (("publish_key_hash", "TEXT NOT NULL DEFAULT ''"),))


def _multi_tenant(conn: sa.Connection) -> None:
    """Migration 16 : rattachements compte-mosquée et mosquée choisie par la session."""
    _baseline(conn)  # crée `memberships` (modèle) si elle manque
    _add_columns(
        conn,
        "auth_sessions",
        (("mosque_id", "INTEGER"), ("support", "INTEGER NOT NULL DEFAULT 0")),
    )


def _mosques_titre_des_clips(conn: sa.Connection) -> None:
    """Migration 17 : le titre des clips n'est plus un réglage — l'en-tête vient du type d'enregistrement."""
    _drop_columns(conn, "mosques", ("clip_title",))


def _recherche(conn: sa.Connection) -> None:
    """Migration 18 : index de recherche (segments de transcription, versets cités), plein texte
    propre au moteur (FTS5 / tsvector + GIN) et vecteurs sémantiques (pgvector quand disponible).

    Les séances déjà importées s'indexent par `python -m sada.backoffice search-index`."""
    _baseline(conn)


def _tarawih(conn: sa.Connection) -> None:
    """Migration 19 : réglages des tarawih par mosquée (mode, période, après-isha, fin sur silence,
    récitants)."""
    models.metadata.tables["tarawih"].create(conn, checkfirst=True)


_TARAWIH_SIGNAUX = ("after_isha", "isha_min", "silence_end_min", "reciters", "nights")


def _khutbas_chapitres(conn: sa.Connection) -> None:
    """Migration 20 : chapitres d'une séance (récitants d'une nuit de tarawih), et table tarawih
    d'une première version (un seul imam, fin fixe) mise à jour : l'imam devient la liste des
    récitants, les colonnes du début et de la fin sur signaux arrivent avec leurs valeurs par défaut."""
    _add_columns(conn, "khutbas", (("chapters", "TEXT NOT NULL DEFAULT ''"),))
    have = {c["name"] for c in sa.inspect(conn).get_columns("tarawih")}
    _add_columns(conn, "tarawih", tuple((c, "") for c in _TARAWIH_SIGNAUX))
    if "imam" in have:
        for mid, imam in conn.exec_driver_sql("SELECT mosque_id, imam FROM tarawih").all():
            if (imam or "").strip():
                conn.execute(
                    sa.text("UPDATE tarawih SET reciters = :r WHERE mosque_id = :m"),
                    {"r": json.dumps([imam.strip()], ensure_ascii=False), "m": mid},
                )
        _drop_columns(conn, "tarawih", ("imam",))


def _horaires_propres(conn: sa.Connection) -> None:
    """Migration 21 : horaires de prière saisis par la mosquée (`prayer_times`) et choix de la source
    (`prayer_settings`). Une mosquée qui avait une adresse Mawaqit garde Mawaqit : aucune ligne
    n'est créée, la source par défaut est lue d'après l'adresse."""
    for name in ("prayer_settings", "prayer_times"):
        models.metadata.tables[name].create(conn, checkfirst=True)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "schéma initial", _baseline),
    Migration(2, "khutbas : colonnes dars (kind, title, series, session_no)", _khutbas_dars),
    Migration(3, "mosques : logo et embarquement (logo_path, onboarded_at)", _mosques_logo),
    Migration(4, "tables des modèles ajoutées après coup (direct, entrées, stockage, textes)", _tables_du_modele),
    Migration(5, "mosques : couleurs des clips (brand_green, brand_gold)", _mosques_couleurs),
    Migration(6, "sous-titres ajoutés à un instant précis (added_subtitles)", _tables_du_modele),
    Migration(7, "clips : création manuelle et bornes ajustées (origin, title, created_by, bounds_by)", _clips_manuels),
    Migration(8, "habillage des vidéos (habillages, mosques.habillage_defaults)", _habillage),
    Migration(9, "users : permissions par compte", _users_permissions),
    Migration(10, "users : mot de passe à changer", _users_mdp_impose),
    Migration(11, "users : administrateur de la plateforme", _users_plateforme),
    Migration(12, "mosques : jeton de l'orchestrateur", _mosques_jeton_agent),
    Migration(13, "events : programme des cours en base", _events_en_base),
    Migration(14, "mosques : cache des horaires Mawaqit", _mosques_cache_mawaqit),
    Migration(15, "mosques : clé de diffusion", _mosques_cle_diffusion),
    Migration(16, "multi-tenant : rattachements et mosquée de la session", _multi_tenant),
    Migration(
        17, "mosques : titre des clips retiré (l'en-tête vient du type d'enregistrement)", _mosques_titre_des_clips
    ),
    Migration(18, "recherche : segments de transcription et versets cités", _recherche),
    Migration(19, "tarawih : réglages de diffusion par mosquée", _tarawih),
    Migration(20, "khutbas : chapitres (récitants des tarawih)", _khutbas_chapitres),
    Migration(21, "horaires de prière propres à la mosquée et choix de la source", _horaires_propres),
)

#: numéros pris par une autre branche pas encore fusionnée : le trou est voulu, pas un oubli.
#: À retirer quand la migration correspondante arrive dans MIGRATIONS.
RESERVEES: frozenset[int] = frozenset()

LATEST = MIGRATIONS[-1].version


def _business_tables(conn: sa.Connection) -> set[str]:
    return set(sa.inspect(conn).get_table_names()) - {"schema_migrations"}


def _applied_versions(conn: sa.Connection) -> set[int]:
    return set(conn.scalars(sa.select(schema_migrations.c.version)))


def _mark(conn: sa.Connection, version: int, now_iso) -> None:
    conn.execute(sa.insert(schema_migrations).values(version=version, appliquee_le=now_iso()))


def init(path: str | Path) -> None:
    """Crée / met à jour la base `path` vers la dernière version connue du code.

    Une base neuve reçoit d'emblée la forme actuelle des modèles (toutes les migrations comptent
    comme appliquées) ; une base existante ne reçoit que ce qui lui manque. Chaque migration est
    appliquée dans sa propre transaction avec son enregistrement.
    """
    from .db import DEFAULT_DB, now_iso  # import tardif : évite le cycle db <-> migrations
    from .orm import engine

    with engine(path, DEFAULT_DB).connect() as conn:
        with conn.begin():
            _BOOK.create_all(conn, checkfirst=True)
            applied = _applied_versions(conn)
            if not applied:
                if _business_tables(conn):
                    # base créée par l'ancien code : on l'adopte à sa version de départ (schéma initial),
                    # sans rien toucher aux données.
                    _mark(conn, LEGACY_BASELINE, now_iso)
                    applied = {LEGACY_BASELINE}
                else:
                    _baseline(conn)
                    for m in MIGRATIONS:
                        _mark(conn, m.version, now_iso)
                    return
        if max(applied) > LATEST:
            raise RuntimeError(
                _(
                    "database {path} is at version {version}, newer than the code ({latest}): update the back-office",
                    path=path,
                    version=max(applied),
                    latest=LATEST,
                )
            )
        for m in MIGRATIONS:
            if m.version not in applied:
                with conn.begin():
                    m.up(conn)
                    _mark(conn, m.version, now_iso)
