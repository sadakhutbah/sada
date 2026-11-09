"""Réglages (API JSON) : sections, registre partiel, sélecteur de dossiers, connecteurs."""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, env  # noqa: E402,F401  (conftest assure le chemin)


def test_les_sections_sont_annoncees(env):
    d = client(env, "admin").get("/api/v1/settings").json()
    assert list(d["sections"]) == ["mosque", "logo", "live", "folders", "model", "connectors", "access"]
    assert d["mosque"]["name"] == "Mosquée Exemple" and d["mosque"]["slug"] == "ma-mosquee"
    assert d["imams"] and d["model"]["label"]


def test_reglages_reserves_a_l_admin(env):
    for role in ("imam", "responsable"):
        assert client(env, role).get("/api/v1/settings").status_code == 403


def test_registre_partiel_ne_vide_pas_les_autres_reglages(env):
    from minbar.backoffice import db
    c = client(env, "admin")
    r = c.ecrire("/settings/mosque", {"orchestrator_url": "http://127.0.0.1:8089", "orchestrator_token": ""}, methode="PATCH")
    assert r.status_code == 200, r.text
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["orchestrator_url"] == "http://127.0.0.1:8089"
    assert m["name"] == "Mosquée Exemple" and m["output_dir"] == str(env["tmp"] / "output")


def test_valeur_refusee(env):
    c = client(env, "admin")
    assert c.ecrire("/settings/mosque", {"name": ""}, methode="PATCH").status_code == 422
    r = c.ecrire("/settings/mosque", {"orchestrator_url": "pas-une-adresse"}, methode="PATCH")
    assert r.status_code == 422 and "http" in r.json()["message"]


def test_api_dossiers_liste_la_machine_reelle(env):
    from pathlib import Path

    c = client(env, "admin")
    d = c.get("/api/v1/folders").json()
    assert d["path"] == str(Path.home()) and d["roots"]
    d2 = c.get("/api/v1/folders", params={"path": str(env["tmp"])}).json()
    assert {"output", "state"} <= {e["name"] for e in d2["entries"]}
    assert all(e["path"].startswith(str(env["tmp"])) for e in d2["entries"])


def test_api_dossiers_masque_ce_qui_n_est_pas_dossier(env):
    c = client(env, "admin")
    f = env["tmp"] / "fichier.txt"
    f.write_text("x")
    d = c.get("/api/v1/folders", params={"path": str(env["tmp"])}).json()
    assert "fichier.txt" not in [e["name"] for e in d["entries"]]
    nope = c.get("/api/v1/folders", params={"path": str(env["tmp"] / "absent")}).json()
    assert nope["exists"] is False


def test_api_dossiers_reservee_a_l_admin(env):
    assert client(env, "responsable").get("/api/v1/folders").status_code == 403


def test_dossier_choisi_enregistre_et_valide(env):
    from minbar.backoffice import db, dirs
    c = client(env, "admin")
    target = env["tmp"] / "nouveau" / "output"
    c.ecrire("/settings/mosque", {"output_dir": str(target)}, methode="PATCH")
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["output_dir"] == str(target)
    assert db.mosque_dirs(m)["output"] == target
    # un fichier ne peut pas servir de dossier
    f = env["tmp"] / "pas-un-dossier"
    f.write_text("x")
    ok, why = dirs.check(str(f))
    assert not ok and "fichier" in why


def test_aucun_chemin_de_la_machine_dans_les_reglages(env):
    """Un placeholder ou une note « par défaut » ne doit jamais exposer l'emplacement réel du serveur."""
    import json
    from pathlib import Path as _P

    from minbar import paths

    d = client(env, "admin").get("/api/v1/settings").json()
    texte = json.dumps({k: d[k] for k in ("defaults", "sections")}, ensure_ascii=False)
    assert str(paths.ROOT) not in texte and str(_P.home()) not in texte
    for valeur in d["defaults"].values():
        assert not valeur.startswith("/"), valeur
        assert "home" not in valeur and "Users" not in valeur and "mnt" not in valeur, valeur


def test_les_connecteurs_sont_decoupes(env):
    """Chaque connecteur expose ses propres champs : l'écran en montre un à la fois."""
    c = client(env, "admin")
    rows = {r["key"]: r for r in c.get("/api/v1/settings").json()["connectors"]}
    assert {"local", "facebook", "youtube", "instagram"} <= set(rows)
    for cle, libelle in (("local", "Dossier local"), ("facebook", "Page Facebook"),
                         ("youtube", "YouTube"), ("instagram", "Instagram")):
        assert rows[cle]["label"] == libelle
        assert rows[cle]["fields"], cle
    assert {f["name"] for f in rows["facebook"]["fields"]} == {"page_id", "page_token"}
    assert "page_id" not in {f["name"] for f in rows["local"]["fields"]}
    # un secret enregistré ne ressort jamais en clair
    c.ecrire("/settings/connectors/facebook", {"page_id": "123", "page_token": "secret"})
    from minbar.backoffice import db
    row = next(r for r in c.get("/api/v1/settings").json()["connectors"] if r["key"] == "facebook")
    assert {f["name"]: f["value"] for f in row["fields"]}["page_token"] == db.MASK
