"""Textes communs du gabarit : lignes arabes par mosquée, sans aucune valeur inventée.

Le gabarit peut afficher le logo, le nom et une ligne arabe. Ces textes-là ne viennent que de la
mosquée : vides au départ, ils restent vides tant que personne ne les saisit.
"""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, env  # noqa: E402,F401

ARABE = "خطبة الجمعة"


def test_aucun_texte_arabe_par_defaut(env):
    from minbar.backoffice import variables_ecran

    vides = {slot: "" for slot in variables_ecran.SLOTS}
    assert variables_ecran.lire(env["conn"], env["mid"]) == vides
    d = client(env, "admin").get("/api/v1/settings").json()["screens_variables"]
    assert d == vides


def test_enregistrement_et_relecture(env):
    from minbar.backoffice import db, variables_ecran

    c = client(env, "admin")
    r = c.ecrire("/settings/screens/variables",
                 {"variables": {"arabe_khutba": f"  {ARABE}  "}}, methode="PUT")
    assert r.status_code == 200, r.text
    assert r.json()["screens_variables"]["arabe_khutba"] == ARABE
    assert variables_ecran.lire(env["conn"], env["mid"])["arabe_khutba"] == ARABE
    row = db.one(env["conn"], "SELECT screen, slot, text FROM screen_texts WHERE mosque_id = ?",
                 env["mid"])
    assert row == {"screen": "_commun", "slot": "arabe_khutba", "text": ARABE}
    assert c.get("/api/v1/settings").json()["screens_variables"]["arabe_khutba"] == ARABE

    # Un champ vidé efface la ligne : rien ne revient à la place.
    r = c.ecrire("/settings/screens/variables", {"variables": {"arabe_khutba": ""}}, methode="PUT")
    assert r.status_code == 200, r.text
    assert r.json()["screens_variables"]["arabe_khutba"] == ""
    assert db.one(env["conn"], "SELECT count(*) n FROM screen_texts")["n"] == 0


def test_borne_de_longueur(env):
    from minbar.backoffice import variables_ecran

    c = client(env, "admin")
    r = c.ecrire("/settings/screens/variables",
                 {"variables": {"arabe_dars": "د" * variables_ecran.MAX_LEN}}, methode="PUT")
    assert r.status_code == 200, r.text
    r = c.ecrire("/settings/screens/variables",
                 {"variables": {"arabe_dars": "د" * (variables_ecran.MAX_LEN + 1)}}, methode="PUT")
    assert r.status_code == 422
    assert "160" in r.json()["message"]
    assert variables_ecran.lire(env["conn"], env["mid"])["arabe_dars"] == "د" * variables_ecran.MAX_LEN


def test_balises_html_refusees(env):
    from minbar.backoffice import variables_ecran

    c = client(env, "admin")
    for texte in ("<b>خطبة</b>", "<script>alert(1)</script>", "a > b"):
        r = c.ecrire("/settings/screens/variables", {"variables": {"arabe_khutba": texte}}, methode="PUT")
        assert r.status_code == 422, texte
        assert "HTML" in r.json()["message"]
    assert variables_ecran.lire(env["conn"], env["mid"])["arabe_khutba"] == ""
    r = c.ecrire("/settings/screens/variables", {"variables": {"inconnu": "x"}}, methode="PUT")
    assert r.status_code == 422 and "champ inconnu" in r.json()["message"]


def test_isole_par_mosquee(env):
    from minbar.backoffice import db, variables_ecran

    autre = db.create_mosque(env["conn"], "autre-mosquee", "Autre Mosquée")
    variables_ecran.enregistrer(env["conn"], autre, {"arabe_priere": "صلاة"})
    assert variables_ecran.lire(env["conn"], autre)["arabe_priere"] == "صلاة"
    assert variables_ecran.lire(env["conn"], env["mid"])["arabe_priere"] == ""
    d = client(env, "admin").get("/api/v1/settings").json()["screens_variables"]
    assert d["arabe_priere"] == ""


def test_reserve_a_l_admin_et_csrf(env):
    c = client(env, "admin")
    r = c.put("/api/v1/settings/screens/variables", json={"variables": {"arabe_dars": "درس"}})
    assert r.status_code == 403                      # sans jeton CSRF
    r = c.put("/api/v1/settings/screens/variables", json={"variables": {"arabe_dars": "درس"}},
              headers={"X-CSRF": "faux"})
    assert r.status_code == 403
    for role in ("imam", "responsable"):
        r = client(env, role).ecrire("/settings/screens/variables",
                                     {"variables": {"arabe_dars": "درس"}}, methode="PUT")
        assert r.status_code == 403


def test_le_moteur_recoit_logo_nom_et_lignes_arabes(env):
    from minbar.backoffice import db, internal, variables_ecran

    c = client(env, "admin")
    c.ecrire("/settings/screens/variables", {"variables": {"arabe_khutba": ARABE}}, methode="PUT")
    h = {internal.HEADER: internal.token()}
    d = c.get("/api/v1/internal/screens", headers=h).json()
    assert d["variables"]["arabe_khutba"] == ARABE
    assert d["variables"]["arabe_janaza"] == ""       # jamais inventée
    assert d["variables"]["nom"] == "Mosquée Exemple"
    assert d["variables"]["logo"] == ""               # aucun logo : rien à afficher
    assert "_commun" not in d["screens"]
    assert c.get("/api/v1/internal/screens", headers={internal.HEADER: "faux"}).status_code == 403

    db.update_mosque(env["conn"], env["mid"], logo_path="data/backoffice/logos/ma-mosquee.png")
    d = c.get("/api/v1/internal/screens", headers=h).json()
    assert d["variables"]["logo"].startswith("/logo/ma-mosquee")
    assert variables_ecran.lire(env["conn"], env["mid"])["arabe_khutba"] == ARABE


def test_enregistrer_les_ecrans_ne_touche_pas_aux_textes_communs(env):
    from minbar.backoffice import internal, screens, variables_ecran

    c = client(env, "admin")
    c.ecrire("/settings/screens/variables", {"variables": {"arabe_janaza": "صلاة الجنازة"}}, methode="PUT")
    r = c.ecrire("/settings/screens", {"screens": {"avant": {"foot": "Bonjour."}}}, methode="PUT")
    assert r.status_code == 200, r.text
    assert variables_ecran.lire(env["conn"], env["mid"])["arabe_janaza"] == "صلاة الجنازة"
    assert screens.saved(env["conn"], env["mid"]) == {"avant": {"foot": "Bonjour."}}
    d = c.get("/api/v1/internal/screens", headers={internal.HEADER: internal.token()}).json()
    assert d["screens"] == {"avant": {"foot": "Bonjour."}}
    assert d["variables"]["arabe_janaza"] == "صلاة الجنازة"
