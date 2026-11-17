"""Back-office: authentication, permissions, mosque isolation, clip validation, publication.

The interface is the React front end: every route tested here is an `/api/v1` JSON API route
(plus media and live), exactly what the browser calls.
"""

import json
import socket
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from conftest import (  # noqa: E402
    PW,
    RUN,
    audit_rows,
    client,
    clip_ids,
    count,
    login,
    make_run,
    one,
    rows,
    set_khutba,
    set_mosque,
    set_user,
)

from sada.backoffice import auth, content, db, importer, models, publish  # noqa: E402

# ------------------------------------------------------------------ authentication


def test_rien_sans_connexion(env):
    c = client(env)
    for url in ("/session", "/sermons", f"/sermons/{env['kid']}", "/clips", "/audit", "/publications", "/settings"):
        r = c.get(f"/api/v1{url}")
        assert r.status_code == 401, url
        assert r.headers["content-type"].startswith("application/json")
    assert c.get("/api/live").status_code == 401
    assert c.get(f"/media/clip/{clip_ids(env)[0]}.mp4").status_code == 401
    r = c.post(f"/api/v1/clips/{clip_ids(env)[0]}/decision", json={"decision": "valide"})
    assert r.status_code == 401
    # the front end itself loads without a session: it is what shows the login screen
    assert c.get("/").status_code == 200


def test_mots_de_passe_haches_argon2(env):
    hashes = [u["password_hash"] for u in rows(env["conn"], models.User)]
    assert all(h.startswith("$argon2id$") and PW not in h for h in hashes)
    with pytest.raises(ValueError):
        auth.create_user(env["conn"], env["mid"], "court", "123", "imam")


def test_connexion_cookie_et_deconnexion(env):
    c = client(env)
    assert login(c, "admin", "mauvais-mot-de-passe").status_code == 401
    assert login(c, "inconnu", PW, expect_ok=False).status_code == 401
    r = login(c, "admin")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "sada_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = c.cookies.get(auth.COOKIE)
    assert count(env["conn"], models.AuthSession, token_hash=token) == 0  # token not stored in clear text
    assert c.get("/api/v1/session").status_code == 200
    assert c.ecrire("/logout").status_code == 200
    c.cookies.set(auth.COOKIE, token)  # replaying the old cookie no longer works
    assert c.get("/api/v1/session").status_code == 401


def test_compte_desactive_refuse(env):
    set_user(env["conn"], "imam2", active=0)
    assert login(client(env), "imam2", expect_ok=False).status_code == 401


def test_csrf_obligatoire(env):
    c = client(env, "admin")
    cid = clip_ids(env)[0]
    r = c.post(f"/api/v1/clips/{cid}/decision", json={"decision": "rejete"})
    assert r.status_code == 403
    r = c.post(f"/api/v1/clips/{cid}/decision", json={"decision": "rejete"}, headers={"X-CSRF": "faux"})
    assert r.status_code == 403
    assert one(env["conn"], models.Clip, id=cid)["status"] == "a_valider"


def test_limitation_des_essais(env):
    c = client(env)
    for _ in range(5):
        assert login(c, "admin", "mauvais-mot-de-passe").status_code == 401
    assert login(c, "admin", expect_ok=False).status_code == 429  # even the right password is blocked


def test_journal_des_connexions(env):
    c = client(env)
    login(c, "admin", "mauvais-mot-de-passe")
    login(c, "admin")
    acts = [a["action"] for a in audit_rows(env["conn"], like="connexion%")]
    assert acts == ["connexion.echec", "connexion"]


# ------------------------------------------------------------------ permissions


@pytest.mark.parametrize(
    "role,url,code",
    [
        ("imam", "/audit", 403),
        ("imam", "/publications", 403),
        ("imam", "/settings", 403),
        ("imam", "/users", 403),
        ("responsable", "/settings", 403),
        ("responsable", "/users", 403),
        ("responsable", "/audit", 200),
        ("responsable", "/publications", 200),
        ("admin", "/settings", 200),
        ("admin", "/users", 200),
        ("admin", "/audit", 200),
    ],
)
def test_matrice_des_droits(env, role, url, code):
    assert client(env, role).get(f"/api/v1{url}").status_code == code


def test_le_journal_se_lit_page_par_page(env):
    """Paginated log: the filtered total comes from the server; the next page does not repeat the previous one."""
    from sada.backoffice import db as _db

    c = client(env, "admin")
    for i in range(40):
        _db.audit(env["conn"], env["mid"], None, "live.commande", commande=f"cmd{i}")
    d = c.get("/api/v1/audit?limit=10").json()
    assert d["total"] >= 40 and d["count"] == 10 and d["offset"] == 0
    assert "labels" not in d and "live.commande" in d["actions"]  # keys only, the front translates
    page2 = c.get("/api/v1/audit?limit=10&offset=10").json()
    assert page2["total"] == d["total"]
    assert not ({a["id"] for a in page2["items"]} & {a["id"] for a in d["items"]})
    filtre = c.get("/api/v1/audit?action=live.commande&limit=5").json()
    assert filtre["total"] == 40 and all(a["action"] == "live.commande" for a in filtre["items"])
    # the imam has no access to the log: the page is not offered to him
    assert client(env, "imam").get("/api/v1/audit").status_code == 403


def test_la_fiche_d_un_clip_porte_la_video_et_les_libelles(env):
    """Reading screen: the video is served; the sheet carries the segments and the history in plain text."""
    c = client(env, "admin")
    cid = clip_ids(env)[0]
    d = c.get(f"/api/v1/clips/{cid}").json()
    assert d["exists"] is True and d["clip"]["version"] == 1
    assert d["can_validate"] is True
    assert "labels" not in d  # history labels are translated by the front end, not sent
    # The context accompanies the clip: the khutba theme and what precedes the extract.
    assert d["contexte"]["theme"]
    assert isinstance(d["contexte"]["avant"], list)  # empty if the clip opens the khutba
    assert d["clip"]["status"] == "a_valider" and "status_labels" not in d  # keys, not screen text
    assert len(d["segments"]) >= 1
    assert c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"}).status_code == 200
    assert [a["action"] for a in c.get(f"/api/v1/clips/{cid}").json()["history"]][:1] == ["clip.valide"]

    r = c.get(f"/media/clip/{cid}.mp4")
    assert r.status_code == 200 and r.content[4:12] == b"ftypmp42"
    assert "attachment" not in r.headers.get("content-disposition", "")

    # Téléchargement depuis le téléphone : pièce jointe, nom neutre, même contenu.
    t = c.get(f"/media/clip/{cid}.mp4?download=1")
    assert t.status_code == 200 and t.content == r.content
    assert t.headers["content-type"] == "video/mp4"
    cd = t.headers["content-disposition"]
    assert cd.startswith("attachment") and f"sada-clip-{cid}-v" in cd and "/" not in cd
    assert client(env).get(f"/media/clip/{cid}.mp4?download=1").status_code == 401


def test_imam_voit_ses_khutbas_seulement(env):
    kid, cid = env["kid"], clip_ids(env)[0]
    mine = client(env, "imam")
    assert mine.get(f"/api/v1/sermons/{kid}").status_code == 200
    assert "La patience" in mine.get("/api/v1/sermons").text
    other = client(env, "imam2")
    assert other.get(f"/api/v1/sermons/{kid}").status_code == 404
    assert other.get(f"/api/v1/clips/{cid}").status_code == 404
    assert other.get(f"/media/clip/{cid}.mp4").status_code == 404
    assert other.get("/api/v1/sermons").json()["count"] == 0
    assert other.ecrire(f"/clips/{cid}/decision", {"decision": "valide"}).status_code == 404


def test_cloisonnement_entre_mosquees(env):
    conn = env["conn"]
    mid2 = db.create_mosque(conn, "autre", "Autre mosquée")
    auth.create_user(conn, mid2, "admin2", PW, "admin")
    c = client(env, "admin2")
    assert c.get(f"/api/v1/sermons/{env['kid']}").status_code == 404
    assert c.get(f"/api/v1/clips/{clip_ids(env)[0]}").status_code == 404
    assert c.get("/api/v1/sermons").json()["count"] == 0
    r = c.ecrire(f"/users/{env['ids']['imam']}", {"role": "admin", "active": "1"}, methode="PATCH")
    assert r.status_code == 404


def test_imam_ne_peut_pas_publier(env):
    cid = clip_ids(env)[0]
    content.decide(
        env["conn"],
        one(env["conn"], models.Clip, id=cid),
        "valide",
        one(env["conn"], models.User, username="responsable"),
    )
    c = client(env, "imam")
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    assert r.status_code == 403
    assert one(env["conn"], models.Clip, id=cid)["status"] == "valide"


def test_admin_cree_un_imam(env):
    c = client(env, "admin")
    r = c.ecrire("/users", {"username": "nouvel", "display_name": "Imam Nouvel", "role": "imam", "password": PW})
    assert r.status_code == 200, r.text
    u = one(env["conn"], models.User, username="nouvel")
    assert u and u["role"] == "imam" and u["mosque_id"] == env["mid"]
    assert login(client(env), "nouvel").status_code == 200


def test_permissions_par_compte(env):
    import json as _json

    c = client(env, "admin")
    uid = env["ids"]["imam"]
    # l'imam reçoit le droit de publier en plus de son rôle
    perms = _json.dumps(["live", "validate", "publish"])
    r = c.ecrire(f"/users/{uid}", {"permissions": perms}, methode="PATCH")
    assert r.status_code == 200, r.text
    u = one(env["conn"], models.User, id=uid)
    assert auth.can(u, "publish") and not auth.can(u, "history_all")
    d = c.get("/api/v1/users").json()
    row = next(x for x in d["items"] if x["id"] == uid)
    assert row["custom"] and row["permissions"] == ["live", "validate", "publish"]
    assert d["role_perms"]["imam"] == ["live", "validate"] and "publish" in d["perms"]
    imam = client(env, "imam")
    assert "publish" in imam.get("/api/v1/session").json()["user"]["permissions"]
    # retour aux droits du rôle
    assert c.ecrire(f"/users/{uid}", {"permissions": ""}, methode="PATCH").status_code == 200
    u = one(env["conn"], models.User, id=uid)
    assert not auth.can(u, "publish") and auth.custom_permissions(u) is None
    # permission inconnue refusée ; un admin ne peut pas se retirer l'administration
    assert c.ecrire(f"/users/{uid}", {"permissions": '["voler"]'}, methode="PATCH").status_code == 422
    me = env["ids"]["admin"]
    assert c.ecrire(f"/users/{me}", {"permissions": '["live"]'}, methode="PATCH").status_code == 422
    # création avec des permissions choisies
    r = c.ecrire(
        "/users",
        {"username": "benevole", "role": "imam", "password": PW, "permissions": '["journal"]'},
    )
    assert r.status_code == 200, r.text
    u = one(env["conn"], models.User, username="benevole")
    assert auth.permissions(u) == ["journal"]


def test_compte_cree_par_admin_doit_changer_son_mot_de_passe(env):
    admin = client(env, "admin")
    assert admin.ecrire("/users", {"username": "nouvel", "role": "responsable", "password": PW}).status_code == 200
    c = client(env, "nouvel")
    s = c.get("/api/v1/session").json()
    assert s["user"]["must_change_password"] and s["user"]["permissions"] == []
    assert c.get("/api/v1/audit").status_code == 403
    # même mot de passe refusé
    assert c.ecrire("/account/password", {"current": PW, "new": PW, "confirm": PW}).status_code == 422
    nouveau = "un-autre-mot-de-passe"
    r = c.ecrire("/account/password", {"current": PW, "new": nouveau, "confirm": nouveau})
    assert r.status_code == 200, r.text
    # même session, droits rendus
    s = c.get("/api/v1/session").json()
    assert not s["user"]["must_change_password"] and "journal" in s["user"]["permissions"]
    assert c.get("/api/v1/audit").status_code == 200
    # mot de passe refixé par l'admin : la séquence recommence
    uid = one(env["conn"], models.User, username="nouvel")["id"]
    assert admin.ecrire(f"/users/{uid}/password", {"password": PW}).status_code == 200
    assert one(env["conn"], models.User, id=uid)["must_change_password"] == 1


# ------------------------------------------------------------------ live (real orchestrator API)


class FakeOrch:
    def __init__(self):
        self.calls = []

    def manual(self, cmd, arg=None):
        self.calls.append((cmd, arg))
        return {"message": f"ok {cmd}", "state": "KHUTBA"}

    def status(self):
        return {
            "state": "KHUTBA",
            "clock": "13:42:10",
            "stream_on": True,
            "mic": True,
            "override": False,
            "next_session": None,
            "last_reason": "voix détectée",
            "sessions": [
                {"planned": "13:30", "status": "live", "start_clock": "13:31"},
                {"planned": "14:30", "status": "pending"},
            ],
        }


@pytest.fixture
def orch(env):
    from sada.api import make_server

    fake = FakeOrch()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = make_server(fake, "127.0.0.1", port, "jeton")
    set_mosque(env["conn"], env["mid"], orchestrator_url=f"http://127.0.0.1:{port}", orchestrator_token="jeton")
    yield fake
    srv.shutdown()


def test_tableau_de_bord_et_commandes_live(env, orch):
    c = client(env, "imam")
    d = c.get("/api/v1/dashboard").json()
    assert d["live"]["clock"] == "13:42:10" and d["live"]["state"] == "KHUTBA"
    assert "state_label" not in d["live"] and "sessions" not in d["live"]  # keys, not screen text
    assert d["live"]["session"] == {"n": 1, "total": 2, "planned": "13:30"}
    assert [c["key"] for c in d["live_commands"]] == [
        "start",
        "pause",
        "resume",
        "auto",
        "extend",
        "skip",
        "scene",
        "janaza",
        "stop",
        "cancel",
    ]
    for cmd in ("extend", "janaza", "stop", "cancel"):
        assert c.ecrire(f"/live/{cmd}").status_code == 200
    assert orch.calls == [("extend", "5"), ("janaza", None), ("stop", None), ("cancel", None)]
    log = audit_rows(env["conn"], action="live.commande")
    assert len(log) == 4 and all(a["user_id"] == env["ids"]["imam"] for a in log)


def test_fiche_janaza_depuis_le_tableau_de_bord(env, orch):
    """The janaza sheet (name + note) goes to the orchestrator, empty if the family allowed nothing."""
    c = client(env, "imam")
    r = c.ecrire("/live/janaza", {"name": " Ahmed Benali ", "info": "67 ans"})
    assert r.status_code == 200
    assert orch.calls[-1] == ("janaza", "Ahmed Benali|67 ans")
    # a name without a note, then a note alone (ignored without a name)
    c.ecrire("/live/janaza", {"name": "Karim", "info": ""})
    c.ecrire("/live/janaza", {"name": "", "info": "67 ans"})
    assert orch.calls[-2:] == [("janaza", "Karim"), ("janaza", None)]


def test_regeneration_refusee_sans_correction():
    """The button is only offered on a corrected clip; a direct POST must not overwrite a validation."""
    from sada.backoffice import content

    clip = {"id": 1, "mosque_id": 1, "version": 1, "needs_regen": 0, "regen_state": ""}
    with pytest.raises(ValueError, match="no correction to apply"):
        content.start_regeneration(None, "x.db", clip, {"id": 1})


def test_reglage_du_modele_depuis_le_backoffice(env, tmp_path, monkeypatch):
    """Settings → Language model: writes the file the pipeline reads, with no secret on the web side."""
    from sada.pipeline import llm

    cfg = tmp_path / "llm.json"
    monkeypatch.setenv("SADA_LLM_CONFIG", str(cfg))
    for var in ("SADA_LLM_ENABLED", "SADA_LLM_URL", "SADA_LLM_MODEL", "SADA_LLM_PROTOCOL"):
        monkeypatch.delenv(var, raising=False)
    c = client(env, "admin")
    assert c.get("/api/v1/platform/settings").json()["model"]["label"]

    r = c.ecrire(
        "/platform/model",
        {"enabled": "1", "url": "http://127.0.0.1:9/v1", "model": "petit", "protocol": "openai"},
        methode="PUT",
    )
    assert r.status_code == 200, r.text
    assert json.loads(cfg.read_text()) == {
        "enabled": True,
        "url": "http://127.0.0.1:9/v1",
        "model": "petit",
        "protocol": "openai",
    }
    assert llm.enabled() and llm.label() == "openai · petit"  # the pipeline would read this

    r = c.ecrire("/platform/model", {"url": "", "model": "", "protocol": ""}, methode="PUT")
    assert r.status_code == 200
    assert json.loads(cfg.read_text())["enabled"] is False and llm.enabled() is False

    r = c.ecrire("/platform/model", {"enabled": "1", "url": "file:///etc/passwd"}, methode="PUT")
    assert r.status_code == 422
    assert json.loads(cfg.read_text())["enabled"] is False  # rejected: the file did not move

    for who in ("imam", "responsable"):
        ci = client(env, who)
        assert ci.ecrire("/platform/model", {"enabled": "1"}, methode="PUT").status_code == 403


def test_nombre_d_extraits_reglable_depuis_le_backoffice(env, tmp_path, monkeypatch):
    """Réglages → Traitement : le nombre d'extraits écrit dans le fichier que lit le pipeline."""
    from sada.pipeline import settings as reglages

    cfg = tmp_path / "pipeline.json"
    monkeypatch.setenv("SADA_PIPELINE_CONFIG", str(cfg))
    monkeypatch.delenv("SADA_CLIPS", raising=False)
    c = client(env, "admin")
    assert c.get("/api/v1/settings").json()["processing"]["clips"] == reglages.DEFAULT_CLIPS

    r = c.ecrire("/settings/processing", {"clips": "5"}, methode="PUT")
    assert r.status_code == 200, r.text
    assert json.loads(cfg.read_text())["clips"] == 5
    assert reglages.clip_count() == 5  # ce que le pipeline lirait
    assert reglages.clip_count(7) == 7  # la ligne de commande reste prioritaire
    assert c.get("/api/v1/settings").json()["processing"]["clips"] == 5

    assert c.ecrire("/settings/processing", {"clips": "0"}, methode="PUT").json()["clips"] == reglages.MIN_CLIPS
    assert c.ecrire("/settings/processing", {"clips": "99"}, methode="PUT").json()["clips"] == reglages.MAX_CLIPS
    assert c.ecrire("/settings/processing", {"clips": "beaucoup"}, methode="PUT").status_code == 422
    assert json.loads(cfg.read_text())["clips"] == reglages.MAX_CLIPS  # refusé : le fichier n'a pas bougé

    # décoché : plus aucun extrait, mais le nombre saisi reste pour la réactivation
    r = c.ecrire("/settings/processing", {"clips": "4", "enabled": "0"}, methode="PUT")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert reglages.clip_count() == 0
    assert reglages.clip_count(2) == 2  # la ligne de commande reste maîtresse
    vue = c.get("/api/v1/settings").json()["processing"]
    assert vue["enabled"] is False and vue["clips"] == 4 and vue["effective"] == 0
    assert c.ecrire("/settings/processing", {"clips": "4", "enabled": "1"}, methode="PUT").json()["enabled"] is True
    assert reglages.clip_count() == 4

    for who in ("imam", "responsable"):
        assert client(env, who).ecrire("/settings/processing", {"clips": "2"}, methode="PUT").status_code == 403


def test_le_pipeline_prend_le_nombre_d_extraits_regle(tmp_path, monkeypatch):
    """Le pipeline sans --clips suit le réglage du back-office ; --no-clips et --clips restent maîtres."""
    from argparse import Namespace

    from sada.pipeline import run
    from sada.pipeline import settings as reglages

    monkeypatch.setenv("SADA_PIPELINE_CONFIG", str(tmp_path / "pipeline.json"))
    monkeypatch.delenv("SADA_CLIPS", raising=False)
    assert run.nclips_option(Namespace(no_clips=False, clips=None)) == reglages.DEFAULT_CLIPS
    reglages.save_config(5)
    assert run.nclips_option(Namespace(no_clips=False, clips=None)) == 5
    assert run.nclips_option(Namespace(no_clips=False, clips=4)) == 4
    assert run.nclips_option(Namespace(no_clips=True, clips=None)) == 0
    reglages.save_config(5, enabled=False)
    assert run.nclips_option(Namespace(no_clips=False, clips=None)) == 0
    monkeypatch.setenv("SADA_CLIPS", "6")  # l'environnement passe avant le fichier
    assert run.nclips_option(Namespace(no_clips=False, clips=None)) == 6
    monkeypatch.setenv("SADA_CLIPS", "0")
    assert run.nclips_option(Namespace(no_clips=False, clips=None)) == 0


def test_test_de_connexion_du_modele(env, tmp_path, monkeypatch):
    """The “Test” button queries the saved address and reports the failure without breaking the screen."""
    monkeypatch.setenv("SADA_LLM_CONFIG", str(tmp_path / "llm.json"))
    monkeypatch.delenv("SADA_LLM_ENABLED", raising=False)
    c = client(env, "admin")
    c.ecrire(
        "/platform/model",
        {"enabled": "1", "url": "http://127.0.0.1:9/v1", "model": "petit", "protocol": "openai"},
        methode="PUT",
    )
    r = c.ecrire("/platform/model/test")
    assert r.status_code == 422
    assert "unreachable" in r.json()["message"]


def test_commande_live_orchestrateur_absent(env):
    c = client(env, "responsable")
    assert c.get("/api/v1/dashboard").json()["live"]["ok"] is False
    r = c.ecrire("/live/stop")
    assert r.status_code == 503
    assert audit_rows(env["conn"], like="live.%")[0]["action"] == "live.echec"


def test_commande_live_role_requis(env, orch, monkeypatch):
    monkeypatch.setitem(auth.PERMS, "live", {"admin", "responsable"})
    c = client(env, "imam")
    assert c.ecrire("/live/stop").status_code == 403
    assert orch.calls == []


# ------------------------------------------------------------------ import


def test_import_idempotent_et_janaza_ignoree(env):
    conn = env["conn"]
    m = one(conn, models.Mosque, id=env["mid"])
    res = importer.import_run(conn, m, RUN, extract_video=False, log=lambda *a: None)
    assert res["khutbas_new"] == 0 and res["clips_new"] == 0 and res["skipped"] == ["session2"]
    k = one(conn, models.Khutba)
    assert count(conn, models.Khutba) == 1
    assert k["theme"] == "La patience" and k["duration_s"] == 20.0 and k["verses_count"] == 1
    c = one(conn, models.Clip, idx=1)
    assert c["source_offset"] == 100.0 and c["status"] == "a_valider"


def test_fiche_khutba_par_l_api(env):
    d = client(env, "responsable").get(f"/api/v1/sermons/{env['kid']}").json()
    refs = [r["verse"]["ref"] for r in d["transcript"] if r["verse"]]
    assert "65:3" in refs
    assert any("At-Talaaq" == (r["verse"] or {}).get("surah") for r in d["transcript"])
    assert "La patience est une lumière." in json.dumps(d["transcript"], ensure_ascii=False)
    assert d["sermon"]["duration_s"] == 20.0
    assert d["notes"].startswith("# Notes") and len(d["clips"]) == 2 and d["has_video"] is False
    # Assignment is only offered to those who can write it (the “assign” permission).
    assert client(env, "imam").get(f"/api/v1/sermons/{env['kid']}").json()["imams"] == []
    assert d["imams"]


# ------------------------------------------------------------------ validation


def test_correction_puis_regeneration_puis_validation(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "imam")
    c.ecrire(
        f"/sermons/{env['kid']}/revisions",
        {"seg": "1", "field": "text", "value": "La patience est une lumière, dit le Prophète ﷺ."},
    )
    clip = one(conn, models.Clip, id=cid)
    assert clip["needs_regen"] == 1
    assert one(conn, models.Clip, idx=2)["needs_regen"] == 0  # segment outside clip 2
    # validating is refused as long as the video has not been regenerated
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    assert one(conn, models.Clip, id=cid)["status"] == "a_valider"
    # regeneration: the pipeline does receive the correction (real build_ass, simulated ffmpeg render)
    from sada.pipeline import clips as clipmod

    seen = {}

    def fake_render(src, off, w, ass, out, log=print, crop=None, fade_out=None):
        seen.update(off=off, w=w, ass=(Path(out).parent / ass).read_text())
        Path(out).write_bytes(b"clip v2")

    monkeypatch.setattr(clipmod, "render_clip", fake_render)
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.ecrire(f"/clips/{cid}/regenerate")
    clip = one(conn, models.Clip, id=cid)
    assert clip["version"] == 2 and clip["needs_regen"] == 0 and clip["file_path"].endswith("_v2_A-VALIDER.mp4")
    assert seen["off"] == 100.0 and "Prophète" in seen["ass"]
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    clip = one(conn, models.Clip, id=cid)
    assert clip["status"] == "valide" and clip["decided_by"] == env["ids"]["imam"]
    acts = [a["action"] for a in audit_rows(conn, user_id=env["ids"]["imam"])]
    assert acts[-3:] == ["correction.sous-titre", "clip.regeneration", "clip.valide"]
    # the clip sheet shows who did what
    d = c.get(f"/api/v1/clips/{cid}").json()
    actions = [h["action"] for h in d["history"]]
    assert "correction.sous-titre" in actions and "clip.valide" in actions
    assert any(h["who"] == "Imam" for h in d["history"])
    assert d["clip"]["status"] == "valide" and "status_labels" not in d


def test_correction_traduction_et_verset_protege(env):
    conn = env["conn"]
    k = one(conn, models.Khutba)
    u = one(conn, models.User, username="responsable")
    with pytest.raises(ValueError):  # segment 2 = verse: the Hamidullah translation cannot be changed
        content.save_correction(conn, k, 2, "fr", "autre traduction", u)
    with pytest.raises(ValueError):  # French segment: no “translation” field
        content.save_correction(conn, k, 0, "fr", "x", u)
    content.save_correction(conn, k, 0, "text", "Chers frères et sœurs, parlons de la patience.", u)
    rows = content.transcript_rows(content.load(conn, k))
    assert rows[0]["fr"] == "Chers frères et sœurs, parlons de la patience." and rows[0]["corrected"]
    corr = one(conn, models.Correction)
    assert corr["original"].startswith("Chers frères, aujourd'hui") and corr["user_id"] == u["id"]


def test_sous_titre_ajoute_a_un_instant_precis(env, monkeypatch):
    """A passage the transcription skipped gets a subtitle at a chosen instant, then reaches the clip."""
    conn = env["conn"]
    k = one(conn, models.Khutba)
    tr = Path(k["output_dir"]) / "transcript.json"
    data = json.loads(tr.read_text())
    data["segments"] = data["segments"][:2]  # 9–20 s: nothing transcribed (Arabic skipped)
    tr.write_text(json.dumps(data))
    c = client(env, "imam")
    cid1, cid2 = clip_ids(env)[:2]
    # over a passage that already shows something: refused (correct it instead)
    r = c.ecrire(f"/sermons/{env['kid']}/subtitles", {"start": "2", "end": "3", "text": "doublon"})
    assert r.status_code == 422 and not rows(conn, models.AddedSubtitle)
    r = c.ecrire(
        f"/sermons/{env['kid']}/subtitles",
        {"start": "15,5", "end": "18", "text": "  Ô Allah,  accorde-nous la patience. "},
    )
    assert r.json()["to_regenerate"] == 1
    a = one(conn, models.AddedSubtitle)
    assert (a["start"], a["end"], a["text"]) == (15.5, 18.0, "Ô Allah, accorde-nous la patience.")
    assert one(conn, models.Clip, id=cid2)["needs_regen"] == 1
    assert one(conn, models.Clip, id=cid1)["needs_regen"] == 0
    segs = c.get(f"/api/v1/clips/{cid2}").json()["segments"]
    ajout = next(x for x in segs if x.get("added"))
    assert ajout["rel"] == pytest.approx(3.5) and ajout["fr"].startswith("Ô Allah")
    # corrections stay keyed on the original segments despite the insertion
    content.save_correction(
        conn,
        k,
        0,
        "text",
        "Chers frères, parlons de patience.",
        one(conn, models.User, username="imam"),
    )
    assert content.transcript_rows(content.load(conn, k))[0]["fr"] == "Chers frères, parlons de patience."
    # the regenerated clip carries the added subtitle
    from sada.pipeline import clips as clipmod

    seen = {}

    def fake_render(src, off, w, ass, out, log=print, crop=None, fade_out=None):
        seen["ass"] = (Path(out).parent / ass).read_text()
        Path(out).write_bytes(b"clip v2")

    monkeypatch.setattr(clipmod, "render_clip", fake_render)
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.ecrire(f"/clips/{cid2}/regenerate")
    assert "accorde-nous" in seen["ass"]
    # removal
    c.ecrire(f"/sermons/{env['kid']}/subtitles/{a['id']}/delete")
    assert not rows(conn, models.AddedSubtitle)
    assert not any(x.get("added") for x in c.get(f"/api/v1/clips/{cid2}").json()["segments"])
    acts = [x["action"] for x in audit_rows(conn, like="correction.%")]
    assert "correction.ajout" in acts and "correction.retrait" in acts


def test_rejet_avec_motif(env):
    c = client(env, "responsable")
    cid = clip_ids(env)[1]
    c.ecrire(f"/clips/{cid}/decision", {"decision": "rejete", "reason": "coupé en plein milieu"})
    clip = one(env["conn"], models.Clip, id=cid)
    assert clip["status"] == "rejete" and clip["reject_reason"] == "coupé en plein milieu"
    d = c.get("/api/v1/clips", params={"status": "rejete"}).json()
    assert [i["id"] for i in d["items"]] == [cid]
    assert d["counts"]["rejete"] == 1 and d["tabs"][-1] == "rejete"


# ------------------------------------------------------------------ publication


def test_rien_ne_se_publie_sans_validation(env):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    assert r.status_code == 422
    assert not (env["tmp"] / "pub").exists()
    p = one(conn, models.Publication)
    assert p["status"] == "refuse" and "not approved" in p["message"]
    assert one(conn, models.Clip, id=cid)["status"] == "a_valider"


def test_publication_locale_apres_validation(env):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "Khutba — extrait"})
    assert r.status_code == 200, r.text
    p = one(conn, models.Publication)
    assert p["status"] == "publie", p
    out = Path(p["location"])
    assert out.exists() and out.read_bytes() == Path(one(conn, models.Clip, id=cid)["file_path"]).read_bytes()
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["legende"] == "Khutba — extrait" and meta["valide_par"] == "Responsable" and meta["musique"] is False
    assert one(conn, models.Clip, id=cid)["status"] == "publie"
    d = c.get("/api/v1/publications").json()
    assert d["publications"] and d["publications"][0]["status"] == "publie"
    assert any(i["id"] == cid for i in d["publications"])


def test_correction_apres_publication_ne_reecrit_pas_l_historique(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    c.ecrire(f"/sermons/{env['kid']}/revisions", {"seg": "1", "field": "text", "value": "Texte corrigé."})
    clip = one(conn, models.Clip, id=cid)
    assert clip["status"] == "publie" and clip["needs_regen"] == 1
    from sada.pipeline import clips as clipmod

    monkeypatch.setattr(
        clipmod,
        "render_clip",
        lambda src, off, w, ass, out, log=print, crop=None, fade_out=None: Path(out).write_bytes(b"v2"),
    )
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.ecrire(f"/clips/{cid}/regenerate")
    clip = one(conn, models.Clip, id=cid)
    assert (
        clip["version"] == 2 and clip["status"] == "a_valider" and clip["decided_by"] is None
    )  # v2 goes back to validation
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    assert r.status_code == 422
    assert rows(conn, models.Publication, order_by=models.publications.c.id.desc())[0]["status"] == "refuse"


def test_connecteurs_reseaux_desactives_sans_appel_externe(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    resp = one(conn, models.User, username="responsable")
    content.decide(conn, one(conn, models.Clip, id=cid), "valide", resp)

    def no_network(*a, **k):
        raise AssertionError("appel réseau interdit")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    connecteurs = {r["key"]: r for r in publish.connector_rows(conn, one(conn, models.Mosque, id=env["mid"]))}
    assert connecteurs["local"]["available"]
    for key in ("facebook", "youtube", "instagram"):
        assert not connecteurs[key]["available"] and "missing credentials" in connecteurs[key]["why"]
        res = publish.publish(conn, one(conn, models.Clip, id=cid), key, resp, "x")
        assert not res.ok
    # with credentials the connector is usable; the network is down here, so the attempt is logged as such
    admin = one(conn, models.User, username="admin")
    publish.save_connector(conn, env["mid"], "facebook", True, {"page_id": "123", "page_token": "secret"}, admin)
    row = next(
        r for r in publish.connector_rows(conn, one(conn, models.Mosque, id=env["mid"])) if r["key"] == "facebook"
    )
    assert row["available"] and {f["name"]: f["value"] for f in row["fields"]}["page_token"] == db.MASK

    def down(*_a, **_k):
        raise publish.social.PublishError("réseau injoignable")

    import sada.backoffice.social as social_mod

    saved = social_mod.SESSION
    social_mod.SESSION = type("S", (), {"post": staticmethod(down)})
    try:
        res = publish.publish(conn, one(conn, models.Clip, id=cid), "facebook", resp, "x")
    finally:
        social_mod.SESSION = saved
    assert not res.ok and "réseau injoignable" in res.message
    assert one(conn, models.Clip, id=cid)["status"] == "valide"
    assert {p["status"] for p in rows(conn, models.Publication)} == {"refuse", "erreur"}


def test_cli_cree_admin_et_imam(tmp_path, monkeypatch, capsys):
    from sada.backoffice.__main__ import main

    path = tmp_path / "cli.db"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    assert (
        main(
            [
                "--db",
                str(path),
                "create-admin",
                "--username",
                "chef",
                "--mosque",
                "ma-mosquee",
                "--mosque-name",
                "Mosquée Exemple",
                "--password-stdin",
            ]
        )
        == 0
    )
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    assert main(["--db", str(path), "create-user", "--username", "imam", "--role", "imam", "--password-stdin"]) == 0
    conn = db.connect(path)
    us = rows(conn, models.User, order_by=models.users.c.id)
    assert [(u["username"], u["role"]) for u in us] == [("chef", "admin"), ("imam", "imam")]
    assert one(conn, models.Mosque)["slug"] == "ma-mosquee"
    assert auth.authenticate(conn, "chef", PW)


# ------------------------------------------------------------------ khutbas / dars séparés

DARS_RUN = "20261110_151315"


def _ajoute_dars(env, kind: str | None = "dars") -> dict:
    """Importe une seconde séance enregistrée ; `kind` tel que l'écrit l'orchestrateur dans sessions.json."""
    tmp = env["tmp"]
    f = tmp / "state" / DARS_RUN / "sessions.json"
    if not f.exists():
        make_run(tmp, run=DARS_RUN)
    d = json.loads(f.read_text())
    d["sessions"][0] = {k: v for k, v in d["sessions"][0].items() if k not in ("kind", "title", "speaker")}
    if kind:
        d["sessions"][0].update(kind=kind, title="Explication de la Lamiyyah al-Wardi (2)", speaker="Mosquée Avicenne")
    f.write_text(json.dumps(d))
    m = one(env["conn"], models.Mosque, id=env["mid"])
    importer.import_run(env["conn"], m, DARS_RUN, extract_video=False, log=lambda *a: None)
    return one(env["conn"], models.Khutba, run_id=DARS_RUN)


def test_listes_khutbas_et_dars_separees(env):
    dars = _ajoute_dars(env)
    assert dars["kind"] == "dars" and dars["label"].startswith("Dars — ")
    c = client(env, "admin")

    tout = c.get("/api/v1/sermons").json()
    assert tout["total"] == 2  # sans filtre : rien ne change

    kh = c.get("/api/v1/sermons?kind=khutba").json()
    assert [k["kind"] for k in kh["items"]] == ["khutba"] and kh["total"] == 1
    assert kh["pending"] == 1  # le compteur suit la nature

    da = c.get("/api/v1/sermons?kind=dars").json()
    assert [k["id"] for k in da["items"]] == [dars["id"]] and da["total"] == 1 and da["pending"] == 1

    assert c.get("/api/v1/sermons?kind=dars&pending=1").json()["total"] == 1
    assert c.get("/api/v1/sermons?kind=conference").json()["total"] == 0
    assert c.get("/api/v1/sermons?kind=dars,conference").json()["total"] == 1
    assert c.get("/api/v1/sermons/mois?kind=dars").json()["items"] == [{"mois": "2026-11", "n": 1}]
    assert c.get("/api/v1/sermons?kind=n_importe_quoi").status_code == 422

    # Une séance d'avant la colonne `kind` (vide) reste une khutba.
    set_khutba(env["conn"], env["kid"], kind="")
    assert c.get("/api/v1/sermons?kind=khutba").json()["total"] == 1

    d = c.get("/api/v1/dashboard").json()["counts"]
    assert d["sermons"] == 1 and d["dars"] == 1


def test_import_corrige_la_nature_d_une_seance_deja_importee(env):
    """Séance importée comme khutba, puis l'orchestrateur précise « dars » : la réimportation range
    la séance dans les dars et refait le libellé automatique, sans toucher aux clips."""
    avant = _ajoute_dars(env, kind=None)
    assert avant["kind"] == "khutba" and avant["label"].startswith("Session 1")
    clips = [(c["id"], c["status"]) for c in rows(env["conn"], models.Clip, khutba_id=avant["id"])]

    apres = _ajoute_dars(env)
    assert apres["id"] == avant["id"] and apres["kind"] == "dars"
    assert apres["title"] == "Explication de la Lamiyyah al-Wardi (2)"
    assert apres["label"] == "Dars — Explication de la Lamiyyah al-Wardi (2)"
    assert [(c["id"], c["status"]) for c in rows(env["conn"], models.Clip, khutba_id=avant["id"])] == clips

    # Un libellé retouché par un humain est conservé.
    set_khutba(env["conn"], avant["id"], kind="khutba", label="Mon libellé")
    assert _ajoute_dars(env)["label"] == "Mon libellé"


def test_import_garde_l_orateur_saisi(env):
    """L'orateur saisi à l'import (sessions.json) arrive dans la fiche."""
    assert _ajoute_dars(env)["speaker"] == "Mosquée Avicenne"
