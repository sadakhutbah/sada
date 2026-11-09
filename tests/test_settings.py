"""Réglages en onglets, sélecteur de dossiers de la machine, formulaires partiels."""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, csrf, env  # noqa: E402,F401  (conftest assure le chemin)


def test_les_sept_onglets_repondent(env):
    c = client(env, "admin")
    for sec, titre in [("identite", "La mosquée"), ("image", "Logo de l'interface"), ("live", "Orchestrateur du live"),
                       ("dossiers", "Dossiers sur cette machine"), ("modele", "Modèle de langage"),
                       ("connecteurs", "Connecteurs de publication"), ("acces", "Comptes et accès")]:
        r = c.get(f"/admin/mosquee?section={sec}")
        assert r.status_code == 200
        assert titre in r.text, sec
        assert r.text.count("<h2>") <= 2, f"onglet {sec} trop chargé"


def test_onglet_par_defaut_et_inconnu(env):
    c = client(env, "admin")
    assert "La mosquée" in c.get("/admin/mosquee").text
    assert "La mosquée" in c.get("/admin/mosquee?section=nimportequoi").text


def test_formulaire_partiel_ne_vide_pas_les_autres_reglages(env):
    from minbar.backoffice import db
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosquee")
    c.post("/admin/mosquee", data={"csrf": tok, "orchestrator_url": "http://127.0.0.1:8089", "orchestrator_token": ""})
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["orchestrator_url"] == "http://127.0.0.1:8089"
    assert m["name"] == "Mosquée Exemple" and m["output_dir"] == str(env["tmp"] / "output")


def test_retour_sur_l_onglet_d_origine(env):
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosquee")
    cases = [({"state_dir": ""}, "/admin/mosquee?section=dossiers"),
             ({"orchestrator_url": "http://127.0.0.1:8088"}, "/admin/mosquee?section=live"),
             ({"name": "Autre nom"}, "/admin/mosquee?section=identite")]
    for data, where in cases:
        r = c.post("/admin/mosquee", data={"csrf": tok, **data}, follow_redirects=False)
        loc = r.headers.get("location") or r.history[-1].headers.get("location")
        assert loc == where, (data, loc)


def test_api_dossiers_liste_la_machine_reelle(env):
    from pathlib import Path

    c = client(env, "admin")
    d = c.get("/api/dossiers").json()
    assert d["path"] == str(Path.home()) and d["roots"]
    d2 = c.get("/api/dossiers", params={"path": str(env["tmp"])}).json()
    assert {"output", "state"} <= {e["name"] for e in d2["entries"]}
    assert all(e["path"].startswith(str(env["tmp"])) for e in d2["entries"])


def test_api_dossiers_masque_ce_qui_n_est_pas_dossier(env):
    c = client(env, "admin")
    f = env["tmp"] / "fichier.txt"
    f.write_text("x")
    d = c.get("/api/dossiers", params={"path": str(env["tmp"])}).json()
    assert "fichier.txt" not in [e["name"] for e in d["entries"]]
    nope = c.get("/api/dossiers", params={"path": str(env["tmp"] / "absent")}).json()
    assert nope["exists"] is False


def test_api_dossiers_reservee_a_l_admin(env):
    c = client(env, "responsable")
    assert c.get("/api/dossiers").status_code == 403


def test_dossier_choisi_enregistre_et_valide(env):
    from minbar.backoffice import db, dirs
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosquee")
    target = env["tmp"] / "nouveau" / "output"
    r = c.post("/admin/mosquee", data={"csrf": tok, "output_dir": str(target)}, follow_redirects=False)
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["output_dir"] == str(target)
    assert db.mosque_dirs(m)["output"] == target
    # un fichier ne peut pas servir de dossier
    f = env["tmp"] / "pas-un-dossier"
    f.write_text("x")
    ok, why = dirs.check(str(f))
    assert not ok and "fichier" in why
