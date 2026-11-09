"""Back-office et dars : libellés, migration des colonnes, légende des clips."""
import re
import sqlite3

from minbar.backoffice import db, publish
from minbar.backoffice.importer import khutba_label

MOSQUE = {"name": "Mosquée Exemple", "clip_title": "Khutba du vendredi"}


def test_libelle_dars_et_khutba():
    assert khutba_label({"index": 1, "kind": "dars", "title": "Tafsir Al-Kahf", "session_no": 7}) == \
        "Dars — Tafsir Al-Kahf · séance 7"
    assert khutba_label({"index": 0, "kind": "conference", "title": "Les droits des voisins"}) == \
        "Conférence — Les droits des voisins"
    assert khutba_label({"index": 0, "planned": "13:30"}) == "Session 1 — 13 h 30"
    assert khutba_label({"index": 1, "kind": "dars"}) == "Session 2"


def test_migration_ajoute_les_colonnes(tmp_path):
    path = tmp_path / "vieux.db"
    old_schema = db.SCHEMA
    for _, col, _ in db.ADDED_COLUMNS:                       # schéma d'avant : colonnes retirées
        old_schema = re.sub(rf"\n\s*{col} [^\n]*,", "", old_schema, count=1)
    assert "session_no" not in old_schema
    old = sqlite3.connect(path)
    old.executescript(old_schema)
    old.execute("INSERT INTO mosques (slug, name, created_at) VALUES ('exemple', 'Mosquée Exemple', 'now')")
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
