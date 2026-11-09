"""Réglages en onglets, sélecteur de dossiers de la machine, formulaires partiels."""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, csrf, env  # noqa: E402,F401  (conftest assure le chemin)


def test_les_sept_onglets_repondent(env):
    c = client(env, "admin")
    for sec, titre in [("mosque", "La mosquée"), ("logo", "Logo de l'interface"), ("live", "Orchestrateur du live"),
                       ("folders", "Dossiers sur cette machine"), ("model", "Modèle de langage"),
                       ("connectors", "Connecteurs de publication"), ("access", "Comptes et accès")]:
        r = c.get(f"/admin/mosque?section={sec}")
        assert r.status_code == 200
        assert titre in r.text, sec
        assert r.text.count("<h2>") <= 2, f"onglet {sec} trop chargé"


def test_onglet_par_defaut_et_inconnu(env):
    c = client(env, "admin")
    assert "La mosquée" in c.get("/admin/mosque").text
    assert "La mosquée" in c.get("/admin/mosque?section=nimportequoi").text


def test_formulaire_partiel_ne_vide_pas_les_autres_reglages(env):
    from minbar.backoffice import db
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosque")
    c.post("/admin/mosque", data={"csrf": tok, "orchestrator_url": "http://127.0.0.1:8089", "orchestrator_token": ""})
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["orchestrator_url"] == "http://127.0.0.1:8089"
    assert m["name"] == "Mosquée Exemple" and m["output_dir"] == str(env["tmp"] / "output")


def test_retour_sur_l_onglet_d_origine(env):
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosque")
    cases = [({"state_dir": ""}, "/admin/mosque?section=folders"),
             ({"orchestrator_url": "http://127.0.0.1:8088"}, "/admin/mosque?section=live"),
             ({"name": "Autre nom"}, "/admin/mosque?section=mosque")]
    for data, where in cases:
        r = c.post("/admin/mosque", data={"csrf": tok, **data}, follow_redirects=False)
        loc = r.headers.get("location") or r.history[-1].headers.get("location")
        assert loc == where, (data, loc)


def test_api_dossiers_liste_la_machine_reelle(env):
    from pathlib import Path

    c = client(env, "admin")
    d = c.get("/api/folders").json()
    assert d["path"] == str(Path.home()) and d["roots"]
    d2 = c.get("/api/folders", params={"path": str(env["tmp"])}).json()
    assert {"output", "state"} <= {e["name"] for e in d2["entries"]}
    assert all(e["path"].startswith(str(env["tmp"])) for e in d2["entries"])


def test_api_dossiers_masque_ce_qui_n_est_pas_dossier(env):
    c = client(env, "admin")
    f = env["tmp"] / "fichier.txt"
    f.write_text("x")
    d = c.get("/api/folders", params={"path": str(env["tmp"])}).json()
    assert "fichier.txt" not in [e["name"] for e in d["entries"]]
    nope = c.get("/api/folders", params={"path": str(env["tmp"] / "absent")}).json()
    assert nope["exists"] is False


def test_api_dossiers_reservee_a_l_admin(env):
    c = client(env, "responsable")
    assert c.get("/api/folders").status_code == 403


def test_dossier_choisi_enregistre_et_valide(env):
    from minbar.backoffice import db, dirs
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosque")
    target = env["tmp"] / "nouveau" / "output"
    r = c.post("/admin/mosque", data={"csrf": tok, "output_dir": str(target)}, follow_redirects=False)
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["output_dir"] == str(target)
    assert db.mosque_dirs(m)["output"] == target
    # un fichier ne peut pas servir de dossier
    f = env["tmp"] / "pas-un-dossier"
    f.write_text("x")
    ok, why = dirs.check(str(f))
    assert not ok and "fichier" in why


def test_aucun_chemin_de_la_machine_dans_la_page(env):
    """Un placeholder ou une note « par défaut » ne doit jamais exposer l'emplacement réel du serveur."""
    import re
    from pathlib import Path as _P

    from minbar import paths

    c = client(env, "admin")
    for sect in ("dossiers", "connecteurs", "modele"):
        page = c.get(f"/admin/mosque?section={sect}").text
        # ni placeholder, ni note « par défaut », ni aide ne révèlent l'emplacement du serveur
        for ph in re.findall(r'placeholder="([^"]*)"', page):
            assert "/" not in ph or not ph.startswith("/"), ph
            assert "home" not in ph and "Users" not in ph and "mnt" not in ph, ph
        for note in re.findall(r'data-dir-state="[^"]*">(.*?)</div>', page, re.S):
            assert str(paths.ROOT) not in note and str(_P.home()) not in note, note
        if sect == "modele":                       # l'aide du modèle ne nomme plus le fichier par son chemin
            assert str(paths.ROOT) not in page and str(_P.home()) not in page


def test_les_connecteurs_sont_decoupes(env):
    """Un seul formulaire de connecteur par écran : quatre empilés faisaient une page démesurée."""
    c = client(env, "admin")
    for cle, libelle in (("local", "Dossier local"), ("facebook", "Page Facebook"),
                         ("youtube", "YouTube"), ("instagram", "Instagram")):
        page = c.get(f"/admin/mosque?section=connectors&c={cle}").text
        assert page.count('class="connector') == 1
        assert libelle in page
        for autre in ("Page Facebook", "YouTube", "Instagram", "Dossier local"):
            if autre != libelle:                       # présent comme sous-onglet, pas comme formulaire
                assert page.count(f'<label>{autre}</label>') == 0
    assert 'class="connector' in c.get("/admin/mosque?section=connectors").text   # sans paramètre : le premier
