"""Gabarit `ecrans.html` : import contrôlé, conservation du fichier, textes appliqués, aperçu protégé.

Le fichier produit par un assistant reste une référence visuelle : le direct est toujours dessiné par
le moteur. L'import reprend les textes du bloc `#sada-screens` et les enregistre comme réglages.
"""
import json

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, env  # noqa: E402,F401

SCREENS = ("avant", "priere", "entracte", "termine", "janaza", "dars_fin")


def _bloc(ecrans: dict | None = None) -> dict:
    """Un bloc conforme : les six écrans, avec {id, texts}."""
    base = {
        "avant": {"eyebrow": "Début imminent", "title": "Khutba du vendredi", "sub": "Début à {heure}",
                  "foot": "Merci de patienter."},
        "priere": {"eyebrow": "Prière en cours", "title": "Prière du vendredi", "sub": "Non diffusée.",
                   "foot": "Reprise après la prière."},
        "entracte": {"eyebrow": "Entracte", "title": "", "sub": "Le live continue.", "foot": ""},
        "termine": {"eyebrow": "Terminé", "title": "Jazakoum Allahou khayran", "sub": "", "foot": ""},
        "janaza": {"eyebrow": "Janaza", "title": "Prière funéraire", "sub": "", "foot": "Reprise après."},
        "dars_fin": {"eyebrow": "Fin du dars", "title": "Barak Allahou fikoum", "sub": "", "foot": ""},
    }
    base.update(ecrans or {})
    return {s: {"id": {k: f"#{s}-{k}" for k in ("eyebrow", "title", "sub", "foot")},
                "texts": dict(v)} for s, v in base.items()}


def _html(bloc: dict | None = None, *, marqueur: str = "GABARIT-EXEMPLE", script: str | None = None) -> bytes:
    contenu = script if script is not None else json.dumps(bloc if bloc is not None else _bloc(), ensure_ascii=False)
    return (f"<!doctype html>\n<html lang=\"fr\"><head><meta charset=\"utf-8\"><title>Écrans</title>"
            f"<style>.ecran{{display:none}}</style></head><body>{marqueur}"
            f"<script type=\"application/json\" id=\"sada-screens\">{contenu}</script>"
            f"</body></html>").encode()


@pytest.fixture
def stockage(env, monkeypatch):
    """Le dossier de référence sort du dépôt : chaque test écrit dans son tmp_path."""
    from sada.backoffice import gabarit

    monkeypatch.setattr(gabarit, "DIR", env["tmp"] / "screens")
    return gabarit


def _envoi(c, donnees: bytes, nom: str = "ecrans.html"):
    return c.request("POST", "/api/v1/settings/screens/import",
                     files={"file": (nom, donnees, "text/html")}, headers={"X-CSRF": c.csrf})


def _valeurs(c) -> dict:
    return c.get("/api/v1/settings").json()["screens"]


def test_import_valide_applique_les_textes_et_conserve_le_fichier(env, stockage):
    c = client(env, "admin")
    r = _envoi(c, _html())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["taken"]["avant"] == {"eyebrow": "Début imminent", "sub": "Début à {heure}",
                                   "foot": "Merci de patienter."}
    # `avant` n'a pas de champ titre dans le moteur : la valeur est signalée, pas appliquée.
    assert any(i["screen"] == "avant" and i["slot"] == "title" for i in d["ignored"])
    assert any("n'a pas de champ" in i["reason"] for i in d["ignored"])
    assert "priere.eyebrow" not in d["taken"]["priere"]
    assert "5 textes repris" in d["message"] or "textes repris" in d["message"]

    champs = _valeurs(c)
    assert champs["avant"]["eyebrow"]["value"] == "Début imminent"
    assert champs["avant"]["foot"]["value"] == "Merci de patienter."
    assert champs["priere"]["title"]["value"] == "Prière du vendredi"
    # le fichier est la référence de la mosquée, à l'emplacement prévu
    assert (env["tmp"] / "screens" / "ma-mosquee" / "ecrans.html").is_file()
    assert d["gabarit"]["present"] is True and d["gabarit"]["url"].startswith("/screens/ma-mosquee/")
    assert str(env["tmp"]) not in r.text            # aucun chemin de la machine dans la réponse

    # et le moteur de diffusion lit bien les textes repris
    from sada.backoffice import internal

    moteur = c.get("/api/v1/internal/screens", headers={internal.HEADER: internal.token()}).json()
    assert moteur["screens"]["avant"]["eyebrow"] == "Début imminent"
    assert moteur["screens"]["priere"]["title"] == "Prière du vendredi"
    assert "title" not in moteur["screens"]["avant"]


def test_apercu_same_origin_protege(env, stockage):
    c = client(env, "admin")
    url = _envoi(c, _html(marqueur="MARQUEUR-APERCU")).json()["gabarit"]["url"]
    r = c.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "MARQUEUR-APERCU" in r.text
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    # les autres réponses restent non encadrables
    s = c.get("/api/v1/settings")
    assert s.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in s.headers["content-security-policy"]

    assert client(env).get(url).status_code == 401                  # pas de session
    assert client(env, "imam").get(url).status_code == 403          # pas administrateur
    assert c.get("/screens/autre-mosquee/ecrans.html").status_code == 404


def test_fichier_sans_bloc_refuse(env, stockage):
    c = client(env, "admin")
    r = _envoi(c, b"<!doctype html><html><body>Une page ordinaire.</body></html>")
    assert r.status_code == 422
    assert "sada-screens" in r.json()["message"]
    assert _valeurs(c)["avant"]["eyebrow"]["value"] == ""       # rien n'a bougé
    assert not (env["tmp"] / "screens").exists()


def test_bloc_json_invalide_refuse(env, stockage):
    c = client(env, "admin")
    r = _envoi(c, _html(script='{"avant": {"texts": },}'))
    assert r.status_code == 422
    message = r.json()["message"]
    assert "JSON valide" in message and "ligne" in message
    assert not (env["tmp"] / "screens").exists()


def test_gabarit_incomplet_refuse(env, stockage):
    c = client(env, "admin")
    bloc = _bloc()
    del bloc["janaza"]
    r = _envoi(c, _html(bloc))
    assert r.status_code == 422
    assert "janaza" in r.json()["message"] and "manquent" in r.json()["message"]


def test_fichier_trop_gros_refuse(env, stockage):
    from sada.backoffice import gabarit

    c = client(env, "admin")
    gros = b"<!doctype html><html><body>" + b"x" * (gabarit.MAX_BYTES + 1) + b"</body></html>"
    r = _envoi(c, gros)
    assert r.status_code == 422
    assert "trop volumineux" in r.json()["message"]
    assert not (env["tmp"] / "screens").exists()


def test_mauvaise_extension_refusee(env, stockage):
    c = client(env, "admin")
    r = _envoi(c, _html(), nom="ecrans.txt")
    assert r.status_code == 422
    assert "Format refusé" in r.json()["message"]
    assert not (env["tmp"] / "screens").exists()


def test_extension_majuscule_acceptee(env, stockage):
    c = client(env, "admin")
    assert _envoi(c, _html(), nom="ECRANS.HTML").status_code == 200


def test_import_reserve_a_l_admin(env, stockage):
    for role in ("imam", "responsable"):
        r = _envoi(client(env, role), _html())
        assert r.status_code == 403
    assert not (env["tmp"] / "screens").exists()


def test_import_ne_touche_pas_aux_autres_reglages(env, stockage):
    c = client(env, "admin")
    c.ecrire("/settings/screens", {"screens": {
        "priere": {"sub": "Texte déjà enregistré."},
        "entracte": {"title_cours": "Cours maison"},     # champ du moteur absent du gabarit
    }}, methode="PUT")
    avant = c.get("/api/v1/settings").json()

    bloc = _bloc()
    bloc["priere"]["texts"]["sub"] = ""                  # une valeur vide ne doit rien effacer
    r = _envoi(c, _html(bloc))
    assert r.status_code == 200, r.text
    assert any(v == "priere.sub" for v in r.json()["empty"])

    apres = c.get("/api/v1/settings").json()
    assert apres["mosque"]["name"] == avant["mosque"]["name"]
    assert apres["model"] == avant["model"]
    assert apres["targets"] == avant["targets"]
    assert apres["screens"]["priere"]["sub"]["value"] == "Texte déjà enregistré."   # vide ≠ effacer
    assert apres["screens"]["entracte"]["title_cours"]["value"] == "Cours maison"   # absent ≠ effacé
    assert apres["screens"]["avant"]["eyebrow"]["value"] == "Début imminent"


def test_un_texte_trop_long_est_ignore_et_signale(env, stockage):
    c = client(env, "admin")
    bloc = _bloc({"avant": {"eyebrow": "x" * 200, "sub": "Début à {heure}", "foot": "Merci."}})
    r = _envoi(c, _html(bloc))
    assert r.status_code == 200, r.text
    assert any(i["screen"] == "avant" and i["slot"] == "eyebrow" and "160" in i["reason"]
               for i in r.json()["ignored"])
    assert _valeurs(c)["avant"]["eyebrow"]["value"] == ""


PAGE_SIX = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><style>.ecran{display:none}</style></head>
<body>
<section class="ecran" data-ecran="avant" data-actif><p>AVANT</p></section>
<section class="ecran" data-ecran="priere"><p>PRIERE</p></section>
<section class="ecran" data-ecran="entracte"><p>ENTRACTE</p></section>
<section class="ecran" data-ecran="termine"><p>TERMINE</p></section>
<section class="ecran" data-ecran="janaza"><p>JANAZA</p></section>
<section class="ecran" data-ecran="dars_fin"><p>DARS</p></section>
<script type="application/json" id="sada-screens">{bloc}</script>
</body></html>"""


def _page_six() -> bytes:
    # Dans un <script type="application/json">, le JSON va brut.
    return PAGE_SIX.replace("{bloc}", json.dumps(_bloc(), ensure_ascii=False)).encode()


def test_l_apercu_suit_l_onglet_ouvert(env, stockage):
    """L'aperçu montrait toujours l'écran mis en avant par le fichier, quel que soit l'onglet."""
    c = client(env, "admin")
    url = _envoi(c, _page_six()).json()["gabarit"]["url"]
    base = url.split("?")[0]

    def actif(ecran: str) -> str:
        html = c.get(f"{base}?ecran={ecran}").text
        assert html.count("data-actif") == 1, ecran
        return html[html.index("data-actif") - 80: html.index("data-actif") + 40]

    assert 'data-ecran="avant" data-actif' in actif("avant")
    assert 'data-ecran="priere" data-actif' in actif("priere")
    assert 'data-ecran="janaza" data-actif' in actif("janaza")


def test_un_ecran_inconnu_ne_casse_rien(env, stockage):
    c = client(env, "admin")
    url = _envoi(c, _page_six()).json()["gabarit"]["url"]
    base = url.split("?")[0]
    r = c.get(f"{base}?ecran=jeudi")
    assert r.status_code == 200 and r.text.count("data-actif") == 1


def test_le_fichier_conserve_n_est_pas_modifie(env, stockage):
    c = client(env, "admin")
    url = _envoi(c, _page_six()).json()["gabarit"]["url"]
    base = url.split("?")[0]
    c.get(f"{base}?ecran=dars_fin")
    disque = (env["tmp"] / "screens" / "ma-mosquee" / "ecrans.html").read_text(encoding="utf-8")
    assert 'data-ecran="avant" data-actif' in disque


def test_les_polices_du_moteur_sont_servies_a_l_apercu(env, stockage):
    """Sans elles l'aperçu tombait sur les polices du système : plus rien ne ressemblait au direct."""
    c = client(env, "admin")
    for nom in ("EBGaramond-SemiBold.ttf", "Montserrat-Bold.ttf", "SourceSans3-Regular.ttf"):
        r = c.get(f"/screens/assets/fonts/{nom}")
        assert r.status_code == 200, nom
        assert r.headers["content-type"].startswith("font/")
        assert len(r.content) > 10000


def test_une_police_hors_liste_est_refusee(env, stockage):
    c = client(env, "admin")
    assert c.get("/screens/assets/fonts/secret.txt").status_code == 404
    assert c.get("/screens/assets/fonts/..%2F..%2Fpyproject.toml").status_code == 404
    assert client(env).get("/screens/assets/fonts/Montserrat-Bold.ttf").status_code == 401


def test_l_apercu_remplit_le_cours_et_le_jour_comme_le_moteur(env, stockage):
    c = client(env, "admin")
    bloc = _bloc({"dars_fin": {"eyebrow": "Fin du dars", "title": "Barak Allahou fikoum",
                               "sub": "Prochain cours : {cours} · {jour} {date}", "foot": "{mosquee}"}})
    url = _envoi(c, _html(bloc)).json()["gabarit"]["url"]
    base = url.split("?")[0]
    html = c.get(f"{base}?ecran=dars_fin").text
    assert "Tafsir de la sourate Al-Kahf" in html
    for variable in ("{cours}", "{jour}", "{date}", "{mosquee}"):
        assert variable not in html, variable


def test_l_apercu_remplit_les_variables_comme_le_moteur(env, stockage):
    """L'aperçu montrait « Début à {heure} » là où le moteur affiche l'heure : deux vérités."""
    c = client(env, "admin")
    url = _envoi(c, _page_six()).json()["gabarit"]["url"]
    base = url.split("?")[0]
    html = c.get(f"{base}?ecran=avant").text
    assert "{heure}" not in html
    assert " h " in html                      # « 14 h 36 », la forme du moteur
    assert "{mosquee}" not in html
