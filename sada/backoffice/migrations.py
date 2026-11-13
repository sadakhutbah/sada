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

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from ..i18n import _
from . import models

# bookkeeping, hors `metadata` (doit exister avant de décider des migrations)
SCHEMA_MIGRATIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ( version INTEGER PRIMARY KEY, appliquee_le TEXT NOT NULL)"
)

# version d'une base existante créée par l'ancien code : les tables existent déjà.
LEGACY_BASELINE = 1


@dataclass(frozen=True)
class Migration:
    version: int
    nom: str
    up: Callable[[sqlite3.Connection], None]


def _create_ddl() -> str:
    """CREATE TABLE / CREATE INDEX `IF NOT EXISTS` de tout le schéma, depuis les modèles."""
    parts: list[str] = []
    for table in models.metadata.sorted_tables:
        parts.append(str(CreateTable(table, if_not_exists=True).compile(dialect=sqlite_dialect.dialect())))
    for table in models.metadata.sorted_tables:
        for index in table.indexes:
            parts.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=sqlite_dialect.dialect())))
    return ";\n".join(parts)


def _baseline(conn: sqlite3.Connection) -> None:
    """Migration 1 : crée toutes les tables et index décrits par les modèles (idempotente)."""
    conn.executescript(_create_ddl())


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f'PRAGMA table_info("{table}")'))


def _add_columns(conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    for col, decl in columns:
        if not _has_column(conn, table, col):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _khutbas_dars(conn: sqlite3.Connection) -> None:
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


def _mosques_logo(conn: sqlite3.Connection) -> None:
    """Migration 3 : logo et date d'embarquement des mosquées, ajoutés après coup."""
    _add_columns(
        conn,
        "mosques",
        (
            ("logo_path", "TEXT NOT NULL DEFAULT ''"),
            ("onboarded_at", "TEXT NOT NULL DEFAULT ''"),
        ),
    )


def _tables_du_modele(conn: sqlite3.Connection) -> None:
    """Migration 4 : rattrape les tables et index ajoutés aux modèles après le schéma initial.

    Un `CREATE ... IF NOT EXISTS`, donc sans effet sur une base déjà complète : la migration existe
    pour les bases qui ont enregistré la version 1 avant que ces tables n'apparaissent dans les
    modèles (`live_targets`, `live_input`, `storage`, `screen_texts`), où plus rien ne les créait.
    """
    conn.executescript(_create_ddl())


def _mosques_couleurs(conn: sqlite3.Connection) -> None:
    """Migration 5 : couleurs de la mosquée pour l'habillage des clips, ajoutées après coup."""
    _add_columns(
        conn,
        "mosques",
        (
            ("brand_green", "TEXT NOT NULL DEFAULT ''"),
            ("brand_gold", "TEXT NOT NULL DEFAULT ''"),
        ),
    )


def _clips_manuels(conn: sqlite3.Connection) -> None:
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


def _habillage(conn: sqlite3.Connection) -> None:
    """Migration 8 : habillage des vidéos (table `habillages`, réglages retenus par la mosquée)."""
    _add_columns(conn, "mosques", (("habillage_defaults", "TEXT NOT NULL DEFAULT ''"),))
    conn.executescript(_create_ddl())


def _users_permissions(conn: sqlite3.Connection) -> None:
    """Migration 9 : permissions choisies compte par compte (vide : celles du rôle)."""
    _add_columns(conn, "users", (("permissions", "TEXT NOT NULL DEFAULT ''"),))


def _users_mdp_impose(conn: sqlite3.Connection) -> None:
    """Migration 10 : mot de passe à changer à la première connexion."""
    _add_columns(conn, "users", (("must_change_password", "INTEGER NOT NULL DEFAULT 0"),))


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
)

#: numéros pris par une autre branche pas encore fusionnée : le trou est voulu, pas un oubli.
#: À retirer quand la migration correspondante arrive dans MIGRATIONS.
RESERVEES: frozenset[int] = frozenset()

LATEST = MIGRATIONS[-1].version


def _business_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} - {"schema_migrations"}


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def init(path: str | Path) -> None:
    """Crée / met à jour la base `path` vers la dernière version connue du code."""
    from .db import connect, now_iso  # import tardif : évite le cycle db <-> migrations

    conn = connect(path)
    try:
        conn.execute(SCHEMA_MIGRATIONS_DDL)
        applied = _applied_versions(conn)

        if not applied and _business_tables(conn):
            # base créée par l'ancien code : on l'adopte à sa version de départ (schéma initial),
            # sans rien toucher aux données.
            conn.execute(
                "INSERT INTO schema_migrations (version, appliquee_le) VALUES (?, ?)", (LEGACY_BASELINE, now_iso())
            )
            conn.commit()
            applied = {LEGACY_BASELINE}

        if applied and max(applied) > LATEST:
            raise RuntimeError(
                _(
                    "database {path} is at version {version}, newer than the code ({latest}): update the back-office",
                    path=path,
                    version=max(applied),
                    latest=LATEST,
                )
            )

        for migration in MIGRATIONS:
            if migration.version not in applied:
                migration.up(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, appliquee_le) VALUES (?, ?)",
                    (migration.version, now_iso()),
                )
                conn.commit()
    finally:
        conn.close()
