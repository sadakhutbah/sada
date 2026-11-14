"""Back-office and dars: labels, column migration, clip caption."""

import pytest
from conftest import columns, create_schema, legacy_metadata, one  # noqa: E402

from sada.backoffice import db, models, publish
from sada.backoffice.importer import khutba_label

MOSQUE = {"name": "Mosquée Exemple", "clip_title": "Khutba du vendredi"}


def test_libelle_dars_et_khutba():
    assert (
        khutba_label({"index": 1, "kind": "dars", "title": "Tafsir Al-Kahf", "session_no": 7})
        == "Dars — Tafsir Al-Kahf · séance 7"
    )
    assert (
        khutba_label({"index": 0, "kind": "conference", "title": "Les droits des voisins"})
        == "Conférence — Les droits des voisins"
    )
    assert khutba_label({"index": 0, "planned": "13:30"}) == "Session 1 — 13 h 30"
    assert khutba_label({"index": 1, "kind": "dars"}) == "Session 2"


@pytest.mark.skipif(bool(__import__("os").environ.get("SADA_DATABASE_URL")), reason="SQLite file format")
def test_migration_ajoute_les_colonnes(tmp_path):
    """Une base créée par l'ancien code (tables présentes, sans `schema_migrations`) est adoptée
    sans perte de données : les colonnes ajoutées depuis apparaissent avec leurs valeurs par défaut."""
    path = tmp_path / "vieux.db"
    # Base d'avant l'ancien code : colonnes ajoutées absentes, pas de table de migrations.
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
        cols = columns(conn, "khutbas")
        assert {"kind", "title", "series", "session_no"} <= cols
        row = one(conn, models.Khutba)
        assert row["kind"] == "khutba" and row["title"] == ""
        assert one(conn, models.Mosque)["slug"] == "exemple"  # données conservées
        migrated = columns(conn, "khutbas")
    finally:
        conn.close()

    fresh = tmp_path / "neuve.db"
    db.init(fresh)
    conn = db.connect(fresh)
    try:
        assert columns(conn, "khutbas") == migrated
        assert {"kind", "title", "series", "session_no"} <= migrated
        assert "logo_path" in columns(conn, "mosques")
        db.create_mosque(conn, "exemple", "Mosquée Exemple")
    finally:
        conn.close()


def test_legende_dun_clip_de_dars():
    khutba = {
        "kind": "dars",
        "title": "Tafsir Al-Kahf",
        "label": "Dars — Tafsir Al-Kahf · séance 7",
        "date": "2026-11-10",
    }
    cap = publish.default_caption(khutba, MOSQUE, ["65:3"])
    assert cap.splitlines()[0] == "Dars : Tafsir Al-Kahf — Mosquée Exemple"
    assert "65:3" in cap

    khutba = {"kind": "khutba", "title": "", "label": "Session 1 — 12 h 30", "date": "2026-11-06"}
    cap = publish.default_caption(khutba, MOSQUE, [])
    assert cap.splitlines()[0] == "Khutba du vendredi — Mosquée Exemple"
