"""Settings (JSON API): sections, partial registry, folder picker, connectors."""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401  (conftest provides the path)


def test_les_sections_sont_annoncees(env):
    d = client(env, "admin").get("/api/v1/settings").json()
    assert list(d["sections"]) == ["mosque", "logo", "screens", "live", "replays", "folders", "model",
                                   "processing", "connectors", "access"]
    assert d["mosque"]["name"] == "Mosquée Exemple" and d["mosque"]["slug"] == "ma-mosquee"
    assert d["imams"] and d["model"]["label"]
    assert d["processing"]["clips"] >= 1 and d["processing"]["max"] >= d["processing"]["min"]


def test_reglages_reserves_a_l_admin(env):
    for role in ("imam", "responsable"):
        assert client(env, role).get("/api/v1/settings").status_code == 403


def test_registre_partiel_ne_vide_pas_les_autres_reglages(env):
    from sada.backoffice import db
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
    from sada.backoffice import db, dirs
    c = client(env, "admin")
    target = env["tmp"] / "nouveau" / "output"
    c.ecrire("/settings/mosque", {"output_dir": str(target)}, methode="PATCH")
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["output_dir"] == str(target)
    assert db.mosque_dirs(m)["output"] == target
    # a file cannot serve as a folder
    f = env["tmp"] / "pas-un-dossier"
    f.write_text("x")
    ok, why = dirs.check(str(f))
    assert not ok and "file" in why


def test_aucun_chemin_de_la_machine_dans_les_reglages(env):
    """A placeholder or a “default” note must never expose the server's real location."""
    import json
    from pathlib import Path as _P

    from sada import paths

    d = client(env, "admin").get("/api/v1/settings").json()
    texte = json.dumps({k: d[k] for k in ("defaults", "sections")}, ensure_ascii=False)
    assert str(paths.ROOT) not in texte and str(_P.home()) not in texte
    for valeur in d["defaults"].values():
        assert not valeur.startswith("/"), valeur
        assert "home" not in valeur and "Users" not in valeur and "mnt" not in valeur, valeur


def test_les_connecteurs_sont_decoupes(env):
    """Each connector exposes its own fields: the screen shows one at a time."""
    c = client(env, "admin")
    rows = {r["key"]: r for r in c.get("/api/v1/settings").json()["connectors"]}
    assert {"local", "facebook", "youtube", "instagram"} <= set(rows)
    for cle, libelle in (("local", "Local folder"), ("facebook", "Facebook page"),
                         ("youtube", "YouTube"), ("instagram", "Instagram")):
        assert rows[cle]["label"] == libelle
        assert rows[cle]["fields"], cle
    assert {f["name"] for f in rows["facebook"]["fields"]} == {"page_id", "page_token"}
    assert "page_id" not in {f["name"] for f in rows["local"]["fields"]}
    # a saved secret never comes back in clear text
    c.ecrire("/settings/connectors/facebook", {"page_id": "123", "page_token": "secret"})
    from sada.backoffice import db
    row = next(r for r in c.get("/api/v1/settings").json()["connectors"] if r["key"] == "facebook")
    assert {f["name"]: f["value"] for f in row["fields"]}["page_token"] == db.MASK


def test_le_jeton_et_la_cle_ne_ressortent_jamais_en_clair(env, monkeypatch):
    """The screen receives a mask for the token, and never the model's API key nor the logo path."""
    import json

    from sada.backoffice import db
    from sada.pipeline import llm

    monkeypatch.setenv("SADA_LLM_CONFIG", str(env["tmp"] / "llm.json"))
    c = client(env, "admin")
    c.ecrire("/settings/mosque", {"orchestrator_token": "tres-secret"}, methode="PATCH")
    llm.config_path().write_text(json.dumps({"enabled": True, "url": "http://h:1/v1", "key": "cle-api-secrete"}))
    d = c.get("/api/v1/settings").json()
    texte = json.dumps(d, ensure_ascii=False)
    assert d["mosque"]["orchestrator_token"] == db.MASK
    assert "tres-secret" not in texte and "cle-api-secrete" not in texte
    assert d["model"]["has_key"] is True and d["mosque"]["has_logo"] is False and "logo_path" not in d["mosque"]
    # the mask returned as-is leaves the token unchanged
    c.ecrire("/settings/mosque", {"orchestrator_token": db.MASK, "name": "Autre nom"}, methode="PATCH")
    m = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert m["orchestrator_token"] == "tres-secret" and m["name"] == "Autre nom"


def test_mawaqit_et_fuseau_enregistres_et_valides(env):
    from sada.backoffice import db
    c = client(env, "admin")
    assert c.ecrire("/settings/mosque", {"mawaqit_url": "https://mawaqit.net/fr/x", "timezone": "Africa/Casablanca"},
                    methode="PATCH").status_code == 200
    d = c.get("/api/v1/settings").json()["mosque"]
    assert d["mawaqit_url"] == "https://mawaqit.net/fr/x" and d["timezone"] == "Africa/Casablanca"
    r = c.ecrire("/settings/mosque", {"timezone": "Mars/Olympus"}, methode="PATCH")
    assert r.status_code == 422 and "time zone" in r.json()["message"]
    assert db.one(env["conn"], "SELECT timezone FROM mosques WHERE id = ?", env["mid"])["timezone"] == "Africa/Casablanca"


def test_dossier_inutilisable_refuse(env):
    c = client(env, "admin")
    f = env["tmp"] / "un-fichier"
    f.write_text("x")
    r = c.ecrire("/settings/mosque", {"publish_dir": str(f)}, methode="PATCH")
    assert r.status_code == 422 and "Folder refused" in r.json()["message"]
    ok = env["tmp"] / "cree" / "ici"
    assert c.ecrire("/settings/mosque", {"publish_dir": str(ok)}, methode="PATCH").status_code == 200
    assert ok.is_dir()


def test_modele_enregistre_et_relu(env, monkeypatch):
    monkeypatch.setenv("SADA_LLM_CONFIG", str(env["tmp"] / "llm.json"))
    c = client(env, "admin")
    r = c.ecrire("/settings/model", {"enabled": "1", "url": "http://127.0.0.1:1/v1", "model": "m", "protocol": "openai"},
                 methode="PUT")
    assert r.status_code == 200, r.text
    m = c.get("/api/v1/settings").json()["model"]
    assert (m["enabled"], m["url"], m["model"], m["protocol"]) == (True, "http://127.0.0.1:1/v1", "m", "openai")
    assert c.ecrire("/settings/model", {"protocol": "inconnu"}, methode="PUT").status_code == 422


def test_publications_real_status_and_notifications(env, monkeypatch):
    """The Publications screen shows real status, masks secrets and configures notifications."""
    from sada import notify
    c = client(env, "admin")
    d = c.get("/api/v1/publications").json()
    rows = {r["key"]: r for r in d["connectors"]}
    assert rows["local"]["available"] and rows["local"]["implemented"]
    assert all(r["implemented"] for r in rows.values())        # real connectors, all four
    # without credentials a network connector is unavailable, and says which one is missing
    assert not rows["facebook"]["available"] and "page_token" in rows["facebook"]["why"]
    # with credentials saved and the switch on, it becomes available (nothing is sent at this point)
    c.ecrire("/settings/connectors/facebook", {"page_id": "123", "page_token": "SECRET-FB", "enabled": "1"})
    d = c.get("/api/v1/publications").json()
    fb = next(r for r in d["connectors"] if r["key"] == "facebook")
    assert fb["available"] and fb["why"] == "" and "SECRET-FB" not in str(d)
    # notifications: secrets masked, value unchanged when the field comes back masked or empty
    assert c.ecrire("/settings/notifications", {"mode": "webhook", "webhook": "ftp://x"}, "PUT").status_code == 422
    assert c.ecrire("/settings/notifications", {"mode": "webhook", "webhook": "https://hook.example/abc"}, "PUT").status_code == 200
    assert c.ecrire("/settings/notifications", {"mode": "webhook", "webhook": ""}, "PUT").status_code == 200
    n = c.get("/api/v1/publications").json()["notifications"]
    assert n["mode"] == "webhook" and n["active"] and n["destination"] == "webhook"
    assert n["webhook"] == "••••••" and "hook.example" not in str(n)
    sent = []
    monkeypatch.setattr(notify, "_http_post", lambda url, payload: sent.append((url, payload)))
    assert c.ecrire("/settings/notifications/test").status_code == 200
    assert sent and sent[0][0] == "https://hook.example/abc"
    # Telegram needs token and chat; a non-admin neither sees nor changes anything
    assert c.ecrire("/settings/notifications", {"mode": "telegram", "telegram_token": "t"}, "PUT").status_code == 422
    r = client(env, "responsable")
    assert r.get("/api/v1/publications").json()["notifications"] is None
    assert r.ecrire("/settings/notifications", {"mode": ""}, "PUT").status_code == 403


def test_endpoint_interne_des_notifications(env, monkeypatch, tmp_path):
    """The orchestrator gets the settings over HTTP with the local token, never by opening the database."""
    from sada.backoffice import internal
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    c = client(env, "admin")
    c.ecrire("/settings/notifications", {"mode": "telegram", "telegram_token": "tok", "telegram_chat": "42"}, "PUT")
    url = "/api/v1/internal/notification-settings?port=8088"
    assert c.get(url).status_code == 403                        # no token, no answer
    r = c.get(url, headers={internal.HEADER: internal.token()})
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "telegram" and d["telegram_token"] == "tok" and d["telegram_chat"] == "42"


def test_notifier_reads_backoffice_settings(env, monkeypatch, tmp_path):
    """Without a destination in the orchestrator config, from_config falls back to the on-screen setting."""
    from types import SimpleNamespace

    from sada import notify
    from sada.backoffice import internal
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    c = client(env, "admin")
    c.ecrire("/settings/notifications", {"mode": "telegram", "telegram_token": "tok", "telegram_chat": "42"}, "PUT")
    seen = []

    def fetch(url, token):
        seen.append((url, token))
        return c.get("/api/v1/internal/notification-settings?port=8088",
                     headers={internal.HEADER: token}).json()

    cfg = SimpleNamespace(notify_url="", telegram_token="", telegram_chat="", api_port=8088,
                          backoffice_url="http://127.0.0.1:8090")
    n = notify.from_config(cfg, log=lambda _m: None, fetch=fetch)
    assert n.enabled and n.destination == "telegram"
    assert seen and seen[0][0] == "http://127.0.0.1:8090/api/v1/internal/notification-settings?port=8088"
    cfg.notify_url = "https://priority.example"
    assert notify.from_config(cfg, fetch=fetch).destination == "webhook"     # config wins, no call
    assert len(seen) == 1

    # back-office unreachable: no destination, the live goes on
    dead = SimpleNamespace(notify_url="", telegram_token="", telegram_chat="", api_port=8088,
                           backoffice_url="http://127.0.0.1:9")
    assert notify.from_config(dead, log=lambda _m: None).enabled is False
