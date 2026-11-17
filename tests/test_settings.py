"""Settings (JSON API): sections, partial registry, folder picker, connectors."""

import pytest

pytest.importorskip("fastapi")

from conftest import client, one  # noqa: E402,F401  (conftest provides the path)

from sada.backoffice import models


def test_les_sections_sont_annoncees(env):
    """The mosque's screen announces its own tabs only: the installation's live elsewhere."""
    d = client(env, "admin").get("/api/v1/settings").json()
    assert list(d["sections"]) == [
        "mosque",
        "logo",
        "screens",
        "live",
        "processing",
    ]
    assert d["mosque"]["name"] == "Mosquée Exemple" and d["mosque"]["slug"] == "ma-mosquee"
    assert d["imams"]
    assert d["processing"]["clips"] >= 1 and d["processing"]["max"] >= d["processing"]["min"]
    assert not {"replays", "folders", "model", "transcription", "public_url"} & set(d)
    assert not {"locked_fields", "defaults", "public_url"} & set(d)


def test_reglages_reserves_a_l_admin(env):
    for role in ("imam", "responsable"):
        assert client(env, role).get("/api/v1/settings").status_code == 403


def test_registre_partiel_ne_vide_pas_les_autres_reglages(env):

    c = client(env, "admin")
    r = c.ecrire("/settings/mosque", {"name": "Nouveau nom"}, methode="PATCH")
    assert r.status_code == 200, r.text
    m = one(env["conn"], models.Mosque, id=env["mid"])
    assert m["name"] == "Nouveau nom"
    assert m["timezone"] == "Europe/Paris" and m["output_dir"] == str(env["tmp"] / "output")


def test_champ_de_la_plateforme_refuse_par_le_reglage_de_la_mosquee(env):
    """Name, time zone and Mawaqit only: the installation's fields are refused, for every account."""
    c = client(env, "admin")
    avant = one(env["conn"], models.Mosque, id=env["mid"])
    for champ in ("orchestrator_url", "output_dir", "public_url"):
        r = c.ecrire("/settings/mosque", {champ: "http://127.0.0.1:8089"}, methode="PATCH")
        assert r.status_code == 422, champ
        assert "platform" in r.json()["message"], champ
    apres = one(env["conn"], models.Mosque, id=env["mid"])
    assert (apres["orchestrator_url"], apres["output_dir"]) == (avant["orchestrator_url"], avant["output_dir"])


def test_valeur_refusee(env):
    c = client(env, "admin")
    assert c.ecrire("/settings/mosque", {"name": ""}, methode="PATCH").status_code == 422


def test_api_dossiers_liste_la_machine_reelle(env):
    from pathlib import Path

    c = client(env, "admin")
    d = c.get("/api/v1/platform/folders").json()
    assert d["path"] == str(Path.home()) and d["roots"]
    d2 = c.get("/api/v1/platform/folders", params={"path": str(env["tmp"])}).json()
    assert {"output", "state"} <= {e["name"] for e in d2["entries"]}
    assert all(e["path"].startswith(str(env["tmp"])) for e in d2["entries"])


def test_api_dossiers_masque_ce_qui_n_est_pas_dossier(env):
    c = client(env, "admin")
    f = env["tmp"] / "fichier.txt"
    f.write_text("x")
    d = c.get("/api/v1/platform/folders", params={"path": str(env["tmp"])}).json()
    assert "fichier.txt" not in [e["name"] for e in d["entries"]]
    nope = c.get("/api/v1/platform/folders", params={"path": str(env["tmp"] / "absent")}).json()
    assert nope["exists"] is False


def test_api_dossiers_reservee_a_l_admin(env):
    assert client(env, "responsable").get("/api/v1/platform/folders").status_code == 403


def test_dossier_choisi_enregistre_et_valide(env):
    from sada.backoffice import db, dirs

    c = client(env, "admin")
    target = env["tmp"] / "nouveau" / "output"
    r = c.ecrire("/platform/folders", {"output": str(target)}, methode="PUT")
    assert r.status_code == 200, r.text
    m = one(env["conn"], models.Mosque, id=env["mid"])
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

    c = client(env, "admin")
    reglages = c.get("/api/v1/settings").json()
    plateforme = c.get("/api/v1/platform/settings").json()
    # What the screen writes itself: hints, addresses, labels. The values typed by an administrator
    # are their own text, echoed back in the field, and are not part of this check.
    hints = {cle: {"default": dossier["default"]} for cle, dossier in plateforme["folders"].items()}
    texte = json.dumps(
        {
            "sections": reglages["sections"],
            "public_url": plateforme["public_url"],
            "docs": plateforme["docs"],
            "model": plateforme["model"],
            "transcription": plateforme["transcription"],
            "folders": hints,
        },
        ensure_ascii=False,
    )
    assert str(paths.ROOT) not in texte and str(_P.home()) not in texte
    for dossier in hints.values():
        assert not dossier["default"].startswith("/"), dossier
        assert "home" not in dossier["default"] and "Users" not in dossier["default"], dossier


def test_les_connecteurs_sont_decoupes(env):
    """Each connector exposes its own fields: the screen shows one at a time."""
    c = client(env, "admin")
    rows = {r["key"]: r for r in c.get("/api/v1/settings").json()["connectors"]}
    assert {"local", "facebook", "youtube", "instagram"} <= set(rows)
    for cle, libelle in (
        ("local", "Local folder"),
        ("facebook", "Facebook page"),
        ("youtube", "YouTube"),
        ("instagram", "Instagram"),
    ):
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
    r = c.ecrire(
        f"/platform/mosques/{env['mid']}/orchestrator",
        {"url": "http://127.0.0.1:8089", "token": "tres-secret"},
        methode="PUT",
    )
    assert r.status_code == 200, r.text
    assert r.json()["mosque"]["orchestrator_token"] == db.MASK
    llm.config_path().write_text(json.dumps({"enabled": True, "url": "http://h:1/v1", "key": "cle-api-secrete"}))
    reglages = c.get("/api/v1/settings").json()
    plateforme = c.get("/api/v1/platform/settings").json()
    texte = json.dumps({"reglages": reglages, "plateforme": plateforme}, ensure_ascii=False)
    assert "tres-secret" not in texte and "cle-api-secrete" not in texte
    assert plateforme["model"]["has_key"] is True
    assert c.get("/api/v1/platform/mosques").json()["mosques"][0]["orchestrator_token"] == db.MASK
    # the mosque's screen knows none of these fields
    assert not {"orchestrator_url", "orchestrator_token", "logo_path"} & set(reglages["mosque"])
    assert reglages["mosque"]["has_logo"] is False
    # the mask returned as-is leaves the token unchanged
    c.ecrire(
        f"/platform/mosques/{env['mid']}/orchestrator",
        {"url": "http://127.0.0.1:8089", "token": db.MASK},
        methode="PUT",
    )
    m = one(env["conn"], models.Mosque, id=env["mid"])
    assert m["orchestrator_token"] == "tres-secret"


def test_mawaqit_et_fuseau_enregistres_et_valides(env):

    c = client(env, "admin")
    assert (
        c.ecrire(
            "/settings/mosque",
            {"mawaqit_url": "https://mawaqit.net/fr/x", "timezone": "Africa/Casablanca"},
            methode="PATCH",
        ).status_code
        == 200
    )
    d = c.get("/api/v1/settings").json()["mosque"]
    assert d["mawaqit_url"] == "https://mawaqit.net/fr/x" and d["timezone"] == "Africa/Casablanca"
    r = c.ecrire("/settings/mosque", {"timezone": "Mars/Olympus"}, methode="PATCH")
    assert r.status_code == 422 and "time zone" in r.json()["message"]
    assert one(env["conn"], models.Mosque, id=env["mid"])["timezone"] == "Africa/Casablanca"


def test_dossier_inutilisable_refuse(env):
    c = client(env, "admin")
    f = env["tmp"] / "un-fichier"
    f.write_text("x")
    r = c.ecrire("/platform/folders", {"publish": str(f)}, methode="PUT")
    assert r.status_code == 422 and "Folder refused" in r.json()["message"]
    ok = env["tmp"] / "cree" / "ici"
    assert c.ecrire("/platform/folders", {"publish": str(ok)}, methode="PUT").status_code == 200
    assert ok.is_dir()


def test_modele_enregistre_et_relu(env, monkeypatch):
    monkeypatch.setenv("SADA_LLM_CONFIG", str(env["tmp"] / "llm.json"))
    c = client(env, "admin")
    r = c.ecrire(
        "/platform/model",
        {"enabled": "1", "url": "http://127.0.0.1:1/v1", "model": "m", "protocol": "openai"},
        methode="PUT",
    )
    assert r.status_code == 200, r.text
    m = c.get("/api/v1/platform/settings").json()["model"]
    assert (m["enabled"], m["url"], m["model"], m["protocol"]) == (True, "http://127.0.0.1:1/v1", "m", "openai")
    assert c.ecrire("/platform/model", {"protocol": "inconnu"}, methode="PUT").status_code == 422


def test_publications_real_status_and_notifications(env, monkeypatch):
    """The Publications screen shows real status, masks secrets and configures notifications."""
    from sada import notify

    c = client(env, "admin")
    d = c.get("/api/v1/publications").json()
    rows = {r["key"]: r for r in d["connectors"]}
    assert rows["local"]["available"] and rows["local"]["implemented"]
    assert all(r["implemented"] for r in rows.values())  # real connectors, all four
    # without credentials a network connector is unavailable, and says which one is missing
    assert not rows["facebook"]["available"] and "page_token" in rows["facebook"]["why"]
    # with credentials saved and the switch on, it becomes available (nothing is sent at this point)
    c.ecrire("/settings/connectors/facebook", {"page_id": "123", "page_token": "SECRET-FB", "enabled": "1"})
    d = c.get("/api/v1/publications").json()
    fb = next(r for r in d["connectors"] if r["key"] == "facebook")
    assert fb["available"] and fb["why"] == "" and "SECRET-FB" not in str(d)
    # notifications: secrets masked, value unchanged when the field comes back masked or empty
    assert c.ecrire("/settings/notifications", {"mode": "webhook", "webhook": "ftp://x"}, "PUT").status_code == 422
    assert (
        c.ecrire(
            "/settings/notifications", {"mode": "webhook", "webhook": "https://hook.example/abc"}, "PUT"
        ).status_code
        == 200
    )
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
    assert c.get(url).status_code == 403  # no token, no answer
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
        return c.get("/api/v1/internal/notification-settings?port=8088", headers={internal.HEADER: token}).json()

    cfg = SimpleNamespace(
        notify_url="", telegram_token="", telegram_chat="", api_port=8088, backoffice_url="http://127.0.0.1:8090"
    )
    n = notify.from_config(cfg, log=lambda _m: None, fetch=fetch)
    assert n.enabled and n.destination == "telegram"
    assert seen and seen[0][0] == "http://127.0.0.1:8090/api/v1/internal/notification-settings?port=8088"
    cfg.notify_url = "https://priority.example"
    assert notify.from_config(cfg, fetch=fetch).destination == "webhook"  # config wins, no call
    assert len(seen) == 1

    # back-office unreachable: no destination, the live goes on
    dead = SimpleNamespace(
        notify_url="", telegram_token="", telegram_chat="", api_port=8088, backoffice_url="http://127.0.0.1:9"
    )
    assert notify.from_config(dead, log=lambda _m: None).enabled is False
