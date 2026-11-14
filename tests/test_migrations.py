"""Couche de migrations versionnées : schéma des modèles SQLAlchemy, adoption des bases
existantes, refus d'une base plus récente que le code."""

import pytest
from conftest import (  # noqa: E402
    applied_versions,
    columns,
    create_schema,
    delete,
    index_names,
    insert,
    legacy_metadata,
    one,
    table_names,
)

from sada.backoffice import db, migrations, models  # noqa: E402

# colonnes ajoutées après le schéma initial (migrations 2, 3 et 5)
ADDED = {
    "khutbas": {"kind", "title", "series", "session_no"},
    "mosques": {"logo_path", "onboarded_at", "brand_green", "brand_gold"},
}

pytestmark = pytest.mark.skipif(bool(__import__("os").environ.get("SADA_DATABASE_URL")), reason="SQLite file format")


#: versions attendues dans schema_migrations après mise à jour complète
VERSIONS = [m.version for m in migrations.MIGRATIONS]


def test_migrations_sont_ordonnees_et_sans_trou():
    versions = [m.version for m in migrations.MIGRATIONS]
    assert versions == sorted(versions)
    assert versions[0] == 1
    assert versions == [v for v in range(1, migrations.LATEST + 1) if v not in migrations.RESERVEES]
    assert not set(versions) & migrations.RESERVEES
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
            assert cols <= columns(conn, table)
        # les index de requête sont créés
        assert {"audit_mosque", "clips_mosque", "khutbas_mosque", "live_targets_mosque"} <= index_names(conn)
        # toutes les versions sont enregistrées
        assert applied_versions(conn) == VERSIONS
    finally:
        conn.close()


def test_adoption_d_une_base_existante(tmp_path):
    """Base créée par l'ancien code : tables présentes, pas de `schema_migrations` -> adoptée,
    version de départ enregistrée, seules les migrations manquantes appliquées, données conservées."""
    path = tmp_path / "ancienne.db"
    # Base au schéma d'avant l'ancien code : colonnes ajoutées absentes, pas de schema_migrations.
    old = legacy_metadata(
        without={
            "khutbas": {"kind", "title", "series", "session_no"},
            "mosques": {"logo_path", "onboarded_at"},
        }
    )
    create_schema(path, old)
    conn = db.connect(path)
    try:
        conn.execute(
            old.tables["mosques"]
            .insert()
            .values(slug="exemple", name="Mosquée Exemple", clip_title="Khutba du vendredi", created_at="now")
        )
        conn.execute(
            old.tables["khutbas"]
            .insert()
            .values(
                mosque_id=1,
                run_id="20261106_220224",
                session_index=0,
                label="Khutba du vendredi",
                output_dir="/tmp/x",
                imported_at="now",
            )
        )
        conn.commit()
    finally:
        conn.close()

    db.init(path)
    conn = db.connect(path)
    try:
        # colonnes ajoutées, valeurs par défaut
        assert ADDED["khutbas"] <= columns(conn, "khutbas")
        assert ADDED["mosques"] <= columns(conn, "mosques")
        row = one(conn, models.Khutba)
        assert row["kind"] == "khutba" and row["title"] == "" and row["session_no"] is None
        # données conservées
        assert one(conn, models.Mosque)["slug"] == "exemple"
        # version de départ (1) enregistrée, puis 2 et 3 appliquées
        assert applied_versions(conn) == VERSIONS
    finally:
        conn.close()


def test_init_est_idempotent(tmp_path):
    path = tmp_path / "base.db"
    db.init(path)
    db.init(path)
    conn = db.connect(path)
    try:
        assert applied_versions(conn) == VERSIONS
    finally:
        conn.close()


def test_refus_d_une_base_plus_recente_que_le_code(tmp_path):
    path = tmp_path / "future.db"
    db.init(path)
    conn = db.connect(path)
    try:
        insert(conn, migrations.schema_migrations, version=migrations.LATEST + 1, appliquee_le="now")
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer than the code"):
        db.init(path)


def test_table_ajoutee_apres_coup_est_creee_sur_une_base_ancienne(tmp_path):
    """Base déjà en version 1..3 mais sans les tables récentes : la migration 4 les rattrape.

    Cas réel : une base locale créée avant `live_targets` répondait 500 sur /settings.
    """
    path = tmp_path / "ancienne_v3.db"
    db.init(path)
    conn = db.connect(path)
    try:
        for table in (models.live_targets, models.live_input, models.storage, models.screen_texts):
            table.drop(conn.connection())
        delete(conn, migrations.schema_migrations, migrations.schema_migrations.c.version >= 4)
    finally:
        conn.close()

    db.init(path)
    conn = db.connect(path)
    try:
        assert {"live_targets", "live_input", "storage", "screen_texts"} <= table_names(conn)
        assert applied_versions(conn)[-1] == migrations.LATEST
        assert "live_targets_mosque" in index_names(conn)
    finally:
        conn.close()


def test_le_schema_des_modeles_est_applique_via_les_migrations(tmp_path):
    """Le schéma produit par `init` (migration 1) est bien celui décrit par les modèles SQLAlchemy."""
    path = tmp_path / "modele.db"
    db.init(path)
    conn = db.connect(path)
    try:
        for table in models.TABLES:
            assert columns(conn, table.name) == {c.name for c in table.columns}, table.name
    finally:
        conn.close()
