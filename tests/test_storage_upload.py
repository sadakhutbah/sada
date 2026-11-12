"""Rediffusions : réglage du compartiment, masque des clés, envoi, reprises."""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, env  # noqa: E402,F401  (conftest fournit le chemin)

BON = {"endpoint": "https://compte.r2.cloudflarestorage.com", "region": "auto",
       "bucket": "sada-rediffusions", "access_key": "AKIAEXEMPLE",
       "secret_key": "secret-tres-secret", "prefix": "rediffusions/", "enabled": "1"}


# --- validation -------------------------------------------------------------------------------

def test_valeurs_refusees():
    from sada.backoffice import storage
    cas = [("bucket", {"bucket": ""}), ("bucket", {"bucket": "Majuscule"}), ("bucket", {"bucket": "ab"}),
           ("endpoint", {"endpoint": "compte.r2.cloudflarestorage.com"}),
           ("endpoint", {"endpoint": "https://x.fr/un/chemin"}),
           ("access_key", {"access_key": ""})]
    for champ, remplacement in cas:
        d = {**BON, **remplacement}
        with pytest.raises(storage.InvalidStorage) as e:
            storage.check(d["endpoint"], d["bucket"], d["region"], d["prefix"], d["access_key"])
        assert champ in e.value.erreurs, (champ, e.value.erreurs)


def test_valeurs_nettoyees():
    from sada.backoffice import storage
    v = storage.check("https://x.r2.cloudflarestorage.com/ ", "mon-bucket", "", "clips", "AK")
    assert v["endpoint"] == "https://x.r2.cloudflarestorage.com"
    assert v["region"] == "auto" and v["prefix"] == "clips/"
    # AWS sans endpoint
    assert storage.check("", "mon-bucket", "eu-west-3", "", "AK")["endpoint"] == ""


# --- réglage enregistré -----------------------------------------------------------------------

def test_enregistrement_puis_masque(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/storage", BON, methode="PUT")
    assert r.status_code == 200, r.text
    v = r.json()["storage"]
    assert v["bucket"] == "sada-rediffusions" and v["enabled"] is True and v["configured"] is True
    assert v["secret_key"] == "••••••" and v["access_key"] == "AKIAEXEMPLE"
    assert c.get("/api/v1/settings").json()["storage"]["secret_key"] == "••••••"


def test_masque_renvoye_garde_le_secret(env):
    from sada.backoffice import storage
    c = client(env, "admin")
    c.ecrire("/settings/storage", BON, methode="PUT")
    c.ecrire("/settings/storage", {**BON, "secret_key": "••••••", "bucket": "autre-bucket"}, methode="PUT")
    t = storage.effective(env["conn"], env["mid"])
    assert t["secret_key"] == "secret-tres-secret" and t["bucket"] == "autre-bucket"


def test_l_envoi_est_une_option_pas_le_defaut(env):
    """Régler un compartiment ne suffit pas : sans case cochée, rien ne part."""
    from sada.backoffice import storage
    c = client(env, "admin")
    sans_case = {k: v for k, v in BON.items() if k != "enabled"}
    v = c.ecrire("/settings/storage", sans_case, methode="PUT").json()["storage"]
    assert v["enabled"] is False and v["configured"] is True
    assert storage.effective(env["conn"], env["mid"]) is None
    # une valeur vide ou fausse n'active pas non plus
    assert c.ecrire("/settings/storage", {**sans_case, "enabled": ""}, methode="PUT").json()["storage"]["enabled"] is False
    assert c.ecrire("/settings/storage", {**sans_case, "enabled": "1"}, methode="PUT").json()["storage"]["enabled"] is True
    assert storage.effective(env["conn"], env["mid"]) is not None


def test_compartiment_desactive_n_est_pas_lu_par_le_moteur(env):
    from sada.backoffice import storage
    c = client(env, "admin")
    c.ecrire("/settings/storage", {**BON, "enabled": "0"}, methode="PUT")
    assert storage.effective(env["conn"], env["mid"]) is None


def test_reserve_a_l_admin(env):
    assert client(env, "imam").ecrire("/settings/storage", BON, methode="PUT").status_code == 403


def test_audit_sans_cle(env):
    c = client(env, "admin")
    c.ecrire("/settings/storage", BON, methode="PUT")
    lignes = [dict(r) for r in env["conn"].execute(
        "SELECT action, details FROM audit WHERE action = 'rediffusion.configure'")]
    assert lignes and "secret-tres-secret" not in lignes[-1]["details"]


# --- envoi ------------------------------------------------------------------------------------

class FauxClient:
    def __init__(self, panne: Exception | None = None):
        self.envois: list[tuple] = []
        self.panne = panne

    def upload_file(self, chemin, bucket, cle, ExtraArgs=None):
        if self.panne:
            raise self.panne
        self.envois.append((Path(chemin).name, bucket, cle, (ExtraArgs or {}).get("ContentType")))

    def put_object(self, **kw):
        if self.panne:
            raise self.panne

    def delete_object(self, **kw):
        pass


def test_envoi_et_index(tmp_path, monkeypatch):
    from sada import upload
    fichier = tmp_path / "live_20261110_130000.mkv"
    fichier.write_bytes(b"x" * 2048)
    faux = FauxClient()
    idx = upload.Index(tmp_path / "uploads.json")
    cible = {"bucket": "b", "prefix": "rediffusions/", "access_key": "a", "secret_key": "s",
             "endpoint": "", "region": "auto"}
    dits = []
    assert upload._envoyer(faux, cible, idx, [fichier], dits.append) == ["rediffusions/live_20261110_130000.mkv"]
    assert faux.envois == [("live_20261110_130000.mkv", "b", "rediffusions/live_20261110_130000.mkv",
                            "video/x-matroska")]
    # deuxième passage : rien à renvoyer
    assert upload._envoyer(faux, cible, idx, [fichier], dits.append) == []
    assert len(faux.envois) == 1 and any("already sent" in m for m in dits)
    # fichier modifié depuis (taille différente) : il repart
    fichier.write_bytes(b"y" * 4096)
    assert upload._envoyer(faux, cible, idx, [fichier], dits.append) == ["rediffusions/live_20261110_130000.mkv"]


def test_index_survit_au_redemarrage(tmp_path):
    from sada import upload
    f = tmp_path / "live_a.mkv"
    f.write_bytes(b"z" * 10)
    idx = upload.Index(tmp_path / "uploads.json")
    idx.noter(f, "rediffusions/live_a.mkv")
    assert upload.Index(tmp_path / "uploads.json").deja(f) is True


def test_fichier_absent_ou_vide_est_ignore(tmp_path):
    from sada import upload
    vide = tmp_path / "live_vide.mkv"
    vide.write_bytes(b"")
    faux = FauxClient()
    dits = []
    assert upload._envoyer(faux, {"bucket": "b", "prefix": ""}, upload.Index(tmp_path / "u.json"),
                           [vide, tmp_path / "absent.mkv"], dits.append) == []
    assert faux.envois == [] and len(dits) == 2


def test_echec_est_raconte_sans_lever(tmp_path):
    from sada import upload
    f = tmp_path / "live_b.mkv"
    f.write_bytes(b"k" * 10)
    dits = []
    faux = FauxClient(panne=Exception("An error occurred (NoSuchBucket) when calling PutObject"))
    assert upload._envoyer(faux, {"bucket": "b", "prefix": ""}, upload.Index(tmp_path / "u2.json"),
                           [f], dits.append) == []
    assert any("bucket does not exist" in m for m in dits)


def test_uploader_sans_cible_n_envoie_rien(tmp_path):
    from sada import upload
    up = upload.Uploader(None, index=upload.Index(tmp_path / "u.json"))
    assert up.actif is False and up.envoyer([tmp_path / "x.mkv"]) is None


def test_uploader_envoie_en_tache_de_fond(tmp_path, monkeypatch):
    from sada import upload
    f = tmp_path / "live_c.mkv"
    f.write_bytes(b"c" * 100)
    faux = FauxClient()
    monkeypatch.setattr(upload, "client", lambda target: faux)
    up = upload.Uploader({"bucket": "b", "prefix": "r/", "access_key": "a", "secret_key": "s"},
                         index=upload.Index(tmp_path / "u3.json"))
    up.envoyer([f]).join(10)
    assert up.resultat == ["r/live_c.mkv"]


def test_repli_quand_le_back_office_repond_vide_ou_tombe():
    from sada import upload
    assert upload.from_backoffice("", 0) is None
    assert upload.from_backoffice("http://127.0.0.1:1", 8088, fetch=lambda u, t: {"target": None}) is None

    def panne(url, token):
        raise OSError("refusé")

    dits = []
    assert upload.from_backoffice("http://127.0.0.1:1", 8088, fetch=panne, log=dits.append) is None
    assert dits and "refusé" in dits[0]
    cible = upload.from_backoffice("http://127.0.0.1:1", 8088,
                                   fetch=lambda u, t: {"target": {"bucket": "b", "prefix": "r/"}})
    assert cible["bucket"] == "b"


# --- bouton Tester ----------------------------------------------------------------------------

def test_sonde_neutre(env, monkeypatch):
    from sada import upload
    from sada.backoffice import storage
    faux = FauxClient()
    monkeypatch.setattr(upload, "client", lambda target: faux)
    ok, detail = storage.probe({"bucket": "b", "prefix": "r/", "access_key": "a", "secret_key": "s"})
    assert ok is True and "b" in detail


def test_sonde_refus_lisible(env, monkeypatch):
    from sada import upload
    from sada.backoffice import storage
    panne = Exception("An error occurred (SignatureDoesNotMatch) when calling PutObject")
    monkeypatch.setattr(upload, "client", lambda target: FauxClient(panne))
    ok, detail = storage.probe({"bucket": "b", "prefix": "r/", "access_key": "a", "secret_key": "s"})
    assert ok is False and "Secret key refused" in detail


def test_bouton_tester_utilise_le_secret_enregistre(env, monkeypatch):
    from sada import upload
    faux = FauxClient()
    monkeypatch.setattr(upload, "client", lambda target: faux)
    c = client(env, "admin")
    c.ecrire("/settings/storage", BON, methode="PUT")
    r = c.ecrire("/settings/storage/test", {**BON, "secret_key": "••••••"}, methode="POST")
    assert r.status_code == 200 and r.json()["ok"] is True


# --- endpoint interne -------------------------------------------------------------------------

def test_le_moteur_recoit_le_reglage_en_clair(env):
    from sada.backoffice import internal
    c = client(env, "admin")
    c.ecrire("/settings/storage", BON, methode="PUT")
    assert c.get("/api/v1/internal/storage").status_code == 403
    d = c.get("/api/v1/internal/storage", headers={internal.HEADER: internal.token()}).json()
    assert d["target"]["secret_key"] == "secret-tres-secret" and d["target"]["bucket"] == "sada-rediffusions"
