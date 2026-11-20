"""Couche de migrations versionnées : schéma des modèles SQLAlchemy, adoption des bases
existantes, refus d'une base plus récente que le code."""

import pytest
import sqlalchemy as sa
from conftest import (  # noqa: E402
    applied_versions,
    columns,
    create_schema,
    delete,
    index_names,
    insert,
    legacy_metadata,
    one,
    rows,
    table_names,
)

from sada.backoffice import db, migrations, models, prayer_times  # noqa: E402
from sada.backoffice.orm import engine  # noqa: E402

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
        conn.execute(old.tables["mosques"].insert().values(slug="exemple", name="Mosquée Exemple", created_at="now"))
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


def test_le_titre_des_clips_est_retire_d_une_base_existante(tmp_path):
    """Le titre des clips n'est plus un réglage : la colonne disparaît, la mosquée reste intacte."""
    path = tmp_path / "clips.db"
    old = legacy_metadata(
        without={"khutbas": {"kind", "title", "series", "session_no"}, "mosques": {"logo_path", "onboarded_at"}}
    )
    create_schema(path, old)
    conn = db.connect(path)
    try:
        conn.execute(sa.text("ALTER TABLE mosques ADD COLUMN clip_title TEXT NOT NULL DEFAULT ''"))
        conn.execute(old.tables["mosques"].insert().values(slug="exemple", name="Mosquée Exemple", created_at="now"))
        conn.execute(sa.text("UPDATE mosques SET clip_title = 'Ancien titre'"))
        conn.commit()
    finally:
        conn.close()

    db.init(path)
    conn = db.connect(path)
    try:
        assert "clip_title" not in columns(conn, "mosques")
        assert one(conn, models.Mosque)["name"] == "Mosquée Exemple"
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


def test_tarawih_de_la_premiere_version_mis_a_jour(tmp_path):
    """A base that ran the first tarawih table (single imam, fixed end): the imam becomes the roster,
    the signal columns arrive with their defaults, and the nights get their chapters column."""
    path = tmp_path / "tw.db"
    db.init(path)
    conn = db.connect(path)
    try:
        conn.execute(sa.text("DELETE FROM schema_migrations WHERE version = 20"))
        conn.execute(sa.text("ALTER TABLE khutbas DROP COLUMN chapters"))
        for col in migrations._TARAWIH_SIGNAUX:
            conn.execute(sa.text(f"ALTER TABLE tarawih DROP COLUMN {col}"))
        conn.execute(sa.text("ALTER TABLE tarawih ADD COLUMN imam TEXT NOT NULL DEFAULT ''"))
        conn.execute(models.Mosque.__table__.insert().values(slug="exemple", name="Mosquée Exemple", created_at="now"))
        mid = conn.execute(sa.text("SELECT id FROM mosques")).scalar()
        conn.execute(
            sa.text("INSERT INTO tarawih (mosque_id, mode, imam, updated_at) VALUES (:m, 'audio', ' Imam A ', 'now')"),
            {"m": mid},
        )
        conn.commit()
    finally:
        conn.close()

    db.init(path)
    conn = db.connect(path)
    try:
        assert "imam" not in columns(conn, "tarawih") and "chapters" in columns(conn, "khutbas")
        row = conn.execute(sa.text("SELECT reciters, silence_end_min, after_isha FROM tarawih")).one()
        assert row == ('["Imam A"]', 6, 0)
    finally:
        conn.close()


# --- migration 21 on a database that looks like production -----------------------------------------

PONTOISE_URL = "https://mawaqit.net/fr/mosquee-de-pontoise"


def _prod_like_v20(path):
    """A base as production has it before the prayer-times feature: schema at version 20, the real
    Pontoise mosque on Mawaqit, a mosque without Mawaqit, one whose address is only blanks."""
    db.init(path)
    with engine(path, db.DEFAULT_DB).connect() as conn, conn.begin():
        conn.execute(sa.delete(models.prayer_settings))
        models.prayer_times.drop(conn)
        models.prayer_settings.drop(conn)
        conn.execute(sa.delete(migrations.schema_migrations).where(migrations.schema_migrations.c.version >= 21))
        for i, (name, slug, url) in enumerate(
            [("Mosquée de Pontoise", "pontoise", PONTOISE_URL), ("Sans Mawaqit", "sans", ""), ("Blanc", "blanc", "  ")],
            start=1,
        ):
            conn.execute(
                sa.insert(models.mosques).values(
                    id=i, name=name, slug=slug, mawaqit_url=url, created_at="2026-01-01T00:00:00+00:00"
                )
            )


def test_migration_21_pontoise_reste_sur_mawaqit(tmp_path):
    path = tmp_path / "prod.db"
    _prod_like_v20(path)
    assert "prayer_settings" not in sa.inspect(engine(path, db.DEFAULT_DB)).get_table_names()

    db.init(path)

    conn = db.connect(path)
    try:
        assert applied_versions(conn)[-1] == migrations.LATEST >= 21
        mosques = {m["slug"]: m for m in rows(conn, models.Mosque)}
        pontoise = mosques["pontoise"]
        settings = rows(conn, models.PrayerSettings)
        # one explicit Mawaqit row, not public, for Pontoise only: no duplicate, nothing for the others
        assert [(s["mosque_id"], s["source"], s["public"]) for s in settings] == [(pontoise["id"], "mawaqit", 0)]
        assert pontoise["mawaqit_url"] == PONTOISE_URL  # the identifier is untouched
        assert rows(conn, models.PrayerDay) == []  # no timetable copied, none hosted by Sada
        assert prayer_times.read(conn, pontoise)["source"] == "mawaqit"
        assert prayer_times.read(conn, mosques["sans"])["source"] == ""
        assert prayer_times.read(conn, mosques["blanc"])["source"] == ""
    finally:
        conn.close()

    db.init(path)  # replaying changes nothing
    conn = db.connect(path)
    try:
        assert len(rows(conn, models.PrayerSettings)) == 1
    finally:
        conn.close()


def test_migration_21_ne_touche_pas_un_choix_deja_fait(tmp_path):
    path = tmp_path / "choix.db"
    _prod_like_v20(path)
    with engine(path, db.DEFAULT_DB).connect() as conn, conn.begin():
        models.prayer_settings.create(conn)
        conn.execute(sa.insert(models.prayer_settings).values(mosque_id=1, source="computed", public=1))
    db.init(path)
    conn = db.connect(path)
    try:
        assert [(s["source"], s["public"]) for s in rows(conn, models.PrayerSettings)] == [("computed", 1)]
    finally:
        conn.close()


def test_migration_21_pontoise_sans_page_publique_chez_nous(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sada.backoffice.app import create_app

    path = tmp_path / "web.db"
    _prod_like_v20(path)
    db.init(path)
    c = TestClient(create_app(path, regen_background=False))
    for suffix in ("prayer-times", "prayer-times/export?format=csv&month=2026-11"):
        r = c.get(f"/api/v1/public/mosques/pontoise/{suffix}")
        assert r.status_code == 404, r.text
        assert r.json()["source"] == "mawaqit" and r.json()["mawaqit_url"] == PONTOISE_URL
