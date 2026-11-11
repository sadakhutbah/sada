"""Back-office and dars: labels, column migration, clip caption."""
import sqlite3

from sada.backoffice import db, publish
from sada.backoffice.importer import khutba_label

MOSQUE = {"name": "Mosquée Exemple", "clip_title": "Khutba du vendredi"}


def test_libelle_dars_et_khutba():
    assert khutba_label({"index": 1, "kind": "dars", "title": "Tafsir Al-Kahf", "session_no": 7}) == \
        "Dars — Tafsir Al-Kahf · séance 7"
    assert khutba_label({"index": 0, "kind": "conference", "title": "Les droits des voisins"}) == \
        "Conférence — Les droits des voisins"
    assert khutba_label({"index": 0, "planned": "13:30"}) == "Session 1 — 13 h 30"
    assert khutba_label({"index": 1, "kind": "dars"}) == "Session 2"


def test_migration_ajoute_les_colonnes(tmp_path):
    """Une base créée par l'ancien code (tables présentes, sans `schema_migrations`) est adoptée
    sans perte de données : les colonnes ajoutées depuis apparaissent avec leurs valeurs par défaut."""
    path = tmp_path / "vieux.db"
    # Base au schéma courant, rembobinée à l'état de l'ancien code (colonnes ajoutées retirées).
    db.init(path)
    old = sqlite3.connect(path)
    old.executescript(
        "DROP TABLE schema_migrations;"
        "ALTER TABLE khutbas DROP COLUMN kind;"
        "ALTER TABLE khutbas DROP COLUMN title;"
        "ALTER TABLE khutbas DROP COLUMN series;"
        "ALTER TABLE khutbas DROP COLUMN session_no;"
        "ALTER TABLE mosques DROP COLUMN logo_path;"
        "ALTER TABLE mosques DROP COLUMN onboarded_at;")
    old.execute("INSERT INTO mosques (slug, name, clip_title, created_at) "
                "VALUES ('exemple', 'Mosquée Exemple', 'Khutba du vendredi', 'now')")
    old.execute("INSERT INTO khutbas (mosque_id, run_id, session_index, label, output_dir, imported_at)"
                " VALUES (1, '20261106_220224', 0, 'Khutba du vendredi', '/tmp/x', 'now')")
    old.commit()
    old.close()

    db.init(path)
    conn = db.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(khutbas)")}
        assert {"kind", "title", "series", "session_no"} <= cols
        row = db.one(conn, "SELECT * FROM khutbas")
        assert row["kind"] == "khutba" and row["title"] == ""
        assert db.one(conn, "SELECT slug FROM mosques")["slug"] == "exemple"   # données conservées
        migrated = {r[1] for r in conn.execute("PRAGMA table_info(khutbas)")}
    finally:
        conn.close()

    fresh = tmp_path / "neuve.db"
    db.init(fresh)
    conn = db.connect(fresh)
    try:
        assert {r[1] for r in conn.execute("PRAGMA table_info(khutbas)")} == migrated
        assert {"kind", "title", "series", "session_no"} <= migrated
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mosques)")}
        assert "logo_path" in cols
        db.create_mosque(conn, "exemple", "Mosquée Exemple")
    finally:
        conn.close()


def test_legende_dun_clip_de_dars():
    khutba = {"kind": "dars", "title": "Tafsir Al-Kahf", "label": "Dars — Tafsir Al-Kahf · séance 7",
              "date": "2026-11-10"}
    cap = publish.default_caption(khutba, MOSQUE, ["65:3"])
    assert cap.splitlines()[0] == "Dars : Tafsir Al-Kahf — Mosquée Exemple"
    assert "65:3" in cap

    khutba = {"kind": "khutba", "title": "", "label": "Session 1 — 12 h 30", "date": "2026-11-06"}
    cap = publish.default_caption(khutba, MOSQUE, [])
    assert cap.splitlines()[0] == "Khutba du vendredi — Mosquée Exemple"
