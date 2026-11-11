"""Couche de migrations versionnées : schéma des modèles SQLAlchemy, adoption des bases
existantes, refus d'une base plus récente que le code."""

import sqlite3

import pytest

from minbar.backoffice import db, migrations, models

# colonnes ajoutées après le schéma initial (migrations 2, 3 et 5)
ADDED = {
    "khutbas": {"kind", "title", "series", "session_no"},
    "mosques": {"logo_path", "onboarded_at", "brand_green", "brand_gold"},
}


def table_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def table_names(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def applied_versions(conn) -> list[int]:
    return [r["version"] for r in db.all_(conn, "SELECT version FROM schema_migrations ORDER BY version")]


def test_migrations_sont_ordonnees_et_sans_trou():
    versions = [m.version for m in migrations.MIGRATIONS]
    assert versions == sorted(versions)
    assert versions[0] == 1
    assert versions == list(range(1, migrations.LATEST + 1))
    assert migrations.LATEST == db.LATEST_VERSION


def test_base_neuve_cree_le_schema_complet(tmp_path):
    path = tmp_path / "neuve.db"
    db.init(path)
    conn = db.connect(path)
    try:
        # toutes les tables métier + la table de migrations
        assert {t.name for t in models.TABLES} <= table_names(conn)
        assert "schema_migrations" in table_names(conn)
        # les colonnes « ajoutées » sont bien là dès la création
        for table, cols in ADDED.items():
            assert cols <= table_columns(conn, table)
        # les index de requête sont créés
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"audit_mosque", "clips_mosque", "khutbas_mosque", "live_targets_mosque"} <= indexes
        # toutes les versions sont enregistrées
        assert applied_versions(conn) == list(range(1, migrations.LATEST + 1))
    finally:
        conn.close()


def test_adoption_d_une_base_existante(tmp_path):
    """Base créée par l'ancien code : tables présentes, pas de `schema_migrations` -> adoptée,
    version de départ enregistrée, seules les migrations manquantes appliquées, données conservées."""
    path = tmp_path / "ancienne.db"
    # Base au schéma courant rembobinée à l'état de l'ancien code (colonnes ajoutées retirées).
    db.init(path)
    raw = sqlite3.connect(path)
    raw.executescript(
        "DROP TABLE schema_migrations;"
        "ALTER TABLE khutbas DROP COLUMN kind;"
        "ALTER TABLE khutbas DROP COLUMN title;"
        "ALTER TABLE khutbas DROP COLUMN series;"
        "ALTER TABLE khutbas DROP COLUMN session_no;"
        "ALTER TABLE mosques DROP COLUMN logo_path;"
        "ALTER TABLE mosques DROP COLUMN onboarded_at;")
    raw.execute("INSERT INTO mosques (slug, name, clip_title, created_at) "
                "VALUES ('exemple', 'Mosquée Exemple', 'Khutba du vendredi', 'now')")
    raw.execute("INSERT INTO khutbas (mosque_id, run_id, session_index, label, output_dir, imported_at)"
                " VALUES (1, '20261106_220224', 0, 'Khutba du vendredi', '/tmp/x', 'now')")
    raw.commit()
    raw.close()

    db.init(path)
    conn = db.connect(path)
    try:
        # colonnes ajoutées, valeurs par défaut
        assert ADDED["khutbas"] <= table_columns(conn, "khutbas")
        assert ADDED["mosques"] <= table_columns(conn, "mosques")
        row = db.one(conn, "SELECT * FROM khutbas")
        assert row["kind"] == "khutba" and row["title"] == "" and row["session_no"] is None
        # données conservées
        assert db.one(conn, "SELECT slug FROM mosques")["slug"] == "exemple"
        # version de départ (1) enregistrée, puis 2 et 3 appliquées
        assert applied_versions(conn) == list(range(1, migrations.LATEST + 1))
    finally:
        conn.close()


def test_init_est_idempotent(tmp_path):
    path = tmp_path / "base.db"
    db.init(path)
    db.init(path)
    conn = db.connect(path)
    try:
        assert applied_versions(conn) == list(range(1, migrations.LATEST + 1))
    finally:
        conn.close()


def test_refus_d_une_base_plus_recente_que_le_code(tmp_path):
    path = tmp_path / "future.db"
    db.init(path)
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO schema_migrations (version, appliquee_le) VALUES (?, ?)",
                (migrations.LATEST + 1, "now"))
    raw.commit()
    raw.close()

    with pytest.raises(RuntimeError, match="plus récente que le code"):
        db.init(path)


def test_table_ajoutee_apres_coup_est_creee_sur_une_base_ancienne(tmp_path):
    """Base déjà en version 1..3 mais sans les tables récentes : la migration 4 les rattrape.

    Cas réel : une base locale créée avant `live_targets` répondait 500 sur /settings.
    """
    path = tmp_path / "ancienne_v3.db"
    db.init(path)
    conn = db.connect(path)
    try:
        for t in ("live_targets", "live_input", "storage", "screen_texts"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("DELETE FROM schema_migrations WHERE version >= 4")
        conn.commit()
    finally:
        conn.close()

    db.init(path)
    conn = db.connect(path)
    try:
        assert {"live_targets", "live_input", "storage", "screen_texts"} <= table_names(conn)
        assert applied_versions(conn)[-1] == migrations.LATEST
        assert "live_targets_mosque" in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        conn.close()


def test_le_schema_des_modeles_est_applique_via_les_migrations(tmp_path):
    """Le schéma produit par `init` (migration 1) est bien celui décrit par les modèles SQLAlchemy."""
    path = tmp_path / "modele.db"
    db.init(path)
    conn = db.connect(path)
    try:
        for table in models.TABLES:
            model_cols = {c.name for c in table.columns}
            assert table_columns(conn, table.name) == model_cols, table.name
    finally:
        conn.close()
