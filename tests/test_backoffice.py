"""Back-office: authentication, permissions, mosque isolation, clip validation, publication.

The interface is the React front end: every route tested here is an `/api/v1` JSON API route
(plus media and live), exactly what the browser calls.
"""
import json
import re
import socket
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from minbar.backoffice import auth, content, db, importer, publish  # noqa: E402
from minbar.backoffice.app import create_app  # noqa: E402

PW = "motdepasse-solide"
RUN = "20261106_220224"


def make_run(root: Path, run: str = RUN, janaza: bool = False) -> None:
    """Minimal pipeline run: 1 session (+ 1 optional janaza), 2 clips."""
    out = root / "output" / run / "session1"
    (out / "clips").mkdir(parents=True)
    segs = [
        {"start": 0.0, "end": 4.0, "text": "Chers frères, aujourd'hui nous parlons de la patience."},
        {"start": 4.0, "end": 9.0, "text": "La patience est une lumière."},
        {"start": 9.0, "end": 14.0, "text": "ومن يتوكل على الله فهو حسبه"},
        {"start": 14.0, "end": 20.0, "text": "Qu'Allah nous accorde la patience."},
    ]
    (out / "transcript.json").write_text(json.dumps({"segments": segs, "duration": 20.0, "model": "test"}))
    (out / "verses.json").write_text(json.dumps([{"surah": 65, "ayah": 3, "surah_name": "At-Talaaq", "score": 98, "start": 9.0,
                                                   "end": 14.0, "heard": "ومن يتوكل على الله فهو حسبه", "verse_text": "ومن يتوكل على الله فهو حسبه", "ref": "65:3",
                                                   "confidence": "sûr", "verse_fr": "Et quiconque place sa confiance en Allah, Il lui suffit."}]))
    (out / "translations.json").write_text(json.dumps({"seg": {}, "verse_fr": {"65:3@9.0": "Et quiconque place sa confiance en Allah, Il lui suffit."}}))
    (out / "notes.md").write_text("# Notes\n\n## Thème (résumé généré)\n\nLa patience\n")
    man = []
    for i, (a, b, kind) in enumerate([(0.0, 10.0, "verset"), (12.0, 20.0, "conclusion")], 1):
        f = f"clip{i}_{kind}_A-VALIDER.mp4"
        (out / "clips" / f).write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))
        man.append({"idx": i, "kind": kind, "start": a, "end": b, "why": "test", "file": f, "video_source": str(root / "rec.mkv"),
                    "source_offset": 100.0, "crop": None})
    (out / "clips" / "manifest.json").write_text(json.dumps(man))
    sessions = [{"index": 0, "planned": "13:30", "status": "done", "start_clock": "13:31", "end_clock": "13:52",
                 "start_pos": 100.0, "end_pos": 120.0}]
    if janaza:
        j = root / "output" / run / "session2"
        j.mkdir()
        (j / "transcript.json").write_text(json.dumps({"segments": [], "duration": 60}))
        sessions.append({"index": 1, "planned": "janaza", "status": "janaza"})
    st = root / "state" / run
    st.mkdir(parents=True)
    (st / "sessions.json").write_text(json.dumps({"sessions": sessions, "recordings": []}))


@pytest.fixture
def env(tmp_path):
    make_run(tmp_path, janaza=True)
    path = tmp_path / "bo.db"
    db.init(path)
    conn = db.connect(path)
    mid = db.create_mosque(conn, "ma-mosquee", "Mosquée Exemple", state_dir=str(tmp_path / "state"),
                           output_dir=str(tmp_path / "output"), publish_dir=str(tmp_path / "pub"),
                           orchestrator_url="http://127.0.0.1:9")
    ids = {r: auth.create_user(conn, mid, r, PW, r, r.capitalize()) for r in auth.ROLES}
    ids["imam2"] = auth.create_user(conn, mid, "imam2", PW, "imam", "Autre imam")
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", mid)
    importer.import_run(conn, m, RUN, extract_video=False, log=lambda *a: None)
    kid = db.one(conn, "SELECT id FROM khutbas")["id"]
    conn.execute("UPDATE khutbas SET imam_id = ? WHERE id = ?", (ids["imam"], kid))
    app = create_app(path, regen_background=False)
    yield {"app": app, "conn": conn, "path": path, "mid": mid, "ids": ids, "kid": kid, "tmp": tmp_path}
    conn.close()


class Client(TestClient):
    """Test client that keeps its session's anti-replay token, as the front end does."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.csrf = ""
        self.login_cookie = ""
        self.last_login = None

    def ecrire(self, chemin: str, corps: dict | None = None, methode: str = "POST"):
        """JSON write on the API, with the session token in a header."""
        return self.request(methode, f"/api/v1{chemin}", json=corps or {}, headers={"X-CSRF": self.csrf})


def client(env, username=None, headers=None) -> Client:
    c = Client(env["app"], follow_redirects=False, headers=headers or {})
    if username:
        login(c, username)
    return c


def login(c: Client, username: str, password: str = PW, expect_ok: bool | None = None):
    pre = c.get("/api/v1/login-challenge").json()["pre"]
    r = c.post("/api/v1/login", data={"username": username, "password": password, "pre": pre})
    if r.status_code == 200:
        c.csrf = r.json()["csrf"]
    c.login_cookie = r.headers.get("set-cookie", "")
    c.last_login = r
    if expect_ok if expect_ok is not None else password == PW:
        assert r.status_code == 200, f"connexion refusée : {r.status_code} {r.text[:300]}"
    return r


def csrf(c: Client, url: str = "/api/v1/session") -> str:
    return c.csrf


def clip_ids(env):
    return [r["id"] for r in db.all_(env["conn"], "SELECT id FROM clips ORDER BY idx")]


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
    rows = db.all_(env["conn"], "SELECT password_hash FROM users")
    assert all(r["password_hash"].startswith("$argon2id$") and PW not in r["password_hash"] for r in rows)
    with pytest.raises(ValueError):
        auth.create_user(env["conn"], env["mid"], "court", "123", "imam")


def test_connexion_cookie_et_deconnexion(env):
    c = client(env)
    assert login(c, "admin", "mauvais-mot-de-passe").status_code == 401
    assert login(c, "inconnu", PW, expect_ok=False).status_code == 401
    r = login(c, "admin")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "minbar_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = c.cookies.get(auth.COOKIE)
    assert db.one(env["conn"], "SELECT COUNT(*) n FROM auth_sessions WHERE token_hash = ?", token)["n"] == 0   # token not stored in clear text
    assert c.get("/api/v1/session").status_code == 200
    assert c.ecrire("/logout").status_code == 200
    c.cookies.set(auth.COOKIE, token)                        # replaying the old cookie no longer works
    assert c.get("/api/v1/session").status_code == 401


def test_compte_desactive_refuse(env):
    env["conn"].execute("UPDATE users SET active = 0 WHERE username = 'imam2'")
    assert login(client(env), "imam2", expect_ok=False).status_code == 401


def test_csrf_obligatoire(env):
    c = client(env, "admin")
    cid = clip_ids(env)[0]
    r = c.post(f"/api/v1/clips/{cid}/decision", json={"decision": "rejete"})
    assert r.status_code == 403
    r = c.post(f"/api/v1/clips/{cid}/decision", json={"decision": "rejete"}, headers={"X-CSRF": "faux"})
    assert r.status_code == 403
    assert db.one(env["conn"], "SELECT status FROM clips WHERE id = ?", cid)["status"] == "a_valider"


def test_limitation_des_essais(env):
    c = client(env)
    for _ in range(5):
        assert login(c, "admin", "mauvais-mot-de-passe").status_code == 401
    assert login(c, "admin", expect_ok=False).status_code == 429   # even the right password is blocked


def test_journal_des_connexions(env):
    c = client(env)
    login(c, "admin", "mauvais-mot-de-passe")
    login(c, "admin")
    acts = [a["action"] for a in db.all_(env["conn"], "SELECT action FROM audit WHERE action LIKE 'connexion%' ORDER BY id")]
    assert acts == ["connexion.echec", "connexion"]


# ------------------------------------------------------------------ permissions

@pytest.mark.parametrize("role,url,code", [
    ("imam", "/audit", 403), ("imam", "/publications", 403), ("imam", "/settings", 403), ("imam", "/users", 403),
    ("responsable", "/settings", 403), ("responsable", "/users", 403),
    ("responsable", "/audit", 200), ("responsable", "/publications", 200),
    ("admin", "/settings", 200), ("admin", "/users", 200), ("admin", "/audit", 200),
])
def test_matrice_des_droits(env, role, url, code):
    assert client(env, role).get(f"/api/v1{url}").status_code == code


def test_le_journal_se_lit_page_par_page(env):
    """Paginated log: the filtered total comes from the server; the next page does not repeat the previous one."""
    from minbar.backoffice import db as _db
    c = client(env, "admin")
    for i in range(40):
        _db.audit(env["conn"], env["mid"], None, "live.commande", commande=f"cmd{i}")
    d = c.get("/api/v1/audit?limit=10").json()
    assert d["total"] >= 40 and d["count"] == 10 and d["offset"] == 0
    assert "labels" not in d and "live.commande" in d["actions"]   # keys only, the front translates
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
    assert "labels" not in d          # history labels are translated by the front end, not sent
    # The context accompanies the clip: the khutba theme and what precedes the extract.
    assert d["contexte"]["theme"]
    assert isinstance(d["contexte"]["avant"], list)   # empty if the clip opens the khutba
    assert d["clip"]["status"] == "a_valider" and "status_labels" not in d   # keys, not screen text
    assert len(d["segments"]) >= 1
    assert c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"}).status_code == 200
    assert [a["action"] for a in c.get(f"/api/v1/clips/{cid}").json()["history"]][:1] == ["clip.valide"]

    r = c.get(f"/media/clip/{cid}.mp4")
    assert r.status_code == 200 and r.content[4:12] == b"ftypmp42"


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
    content.decide(env["conn"], db.one(env["conn"], "SELECT * FROM clips WHERE id = ?", cid), "valide",
                   db.one(env["conn"], "SELECT * FROM users WHERE username = 'responsable'"))
    c = client(env, "imam")
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    assert r.status_code == 403
    assert db.one(env["conn"], "SELECT status FROM clips WHERE id = ?", cid)["status"] == "valide"


def test_admin_cree_un_imam(env):
    c = client(env, "admin")
    r = c.ecrire("/users", {"username": "nouvel", "display_name": "Imam Nouvel", "role": "imam", "password": PW})
    assert r.status_code == 200, r.text
    u = db.one(env["conn"], "SELECT * FROM users WHERE username = 'nouvel'")
    assert u and u["role"] == "imam" and u["mosque_id"] == env["mid"]
    assert login(client(env), "nouvel").status_code == 200


# ------------------------------------------------------------------ live (real orchestrator API)

class FakeOrch:
    def __init__(self):
        self.calls = []

    def manual(self, cmd, arg=None):
        self.calls.append((cmd, arg))
        return {"message": f"ok {cmd}", "state": "KHUTBA"}

    def status(self):
        return {"state": "KHUTBA", "clock": "13:42:10", "stream_on": True, "mic": True, "override": False,
                "next_session": None, "last_reason": "voix détectée",
                "sessions": [{"planned": "13:30", "status": "live", "start_clock": "13:31"}, {"planned": "14:30", "status": "pending"}]}


@pytest.fixture
def orch(env):
    from minbar.api import make_server
    fake = FakeOrch()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = make_server(fake, "127.0.0.1", port, "jeton")
    env["conn"].execute("UPDATE mosques SET orchestrator_url = ?, orchestrator_token = 'jeton' WHERE id = ?",
                        (f"http://127.0.0.1:{port}", env["mid"]))
    yield fake
    srv.shutdown()


def test_tableau_de_bord_et_commandes_live(env, orch):
    c = client(env, "imam")
    d = c.get("/api/v1/dashboard").json()
    assert d["live"]["clock"] == "13:42:10" and d["live"]["state"] == "KHUTBA"
    assert "state_label" not in d["live"] and "sessions" not in d["live"]   # keys, not screen text
    assert d["live"]["session"] == {"n": 1, "total": 2, "planned": "13:30"}
    assert [c["key"] for c in d["live_commands"]] == [
        "start", "pause", "resume", "auto", "extend", "skip", "scene", "janaza", "stop", "cancel"]
    for cmd in ("extend", "janaza", "stop", "cancel"):
        assert c.ecrire(f"/live/{cmd}").status_code == 200
    assert orch.calls == [("extend", "5"), ("janaza", None), ("stop", None), ("cancel", None)]
    log = db.all_(env["conn"], "SELECT * FROM audit WHERE action = 'live.commande' ORDER BY id")
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
    from minbar.backoffice import content
    clip = {"id": 1, "mosque_id": 1, "version": 1, "needs_regen": 0, "regen_state": ""}
    with pytest.raises(ValueError, match="aucune correction"):
        content.start_regeneration(None, "x.db", clip, {"id": 1})


def test_reglage_du_modele_depuis_le_backoffice(env, tmp_path, monkeypatch):
    """Settings → Language model: writes the file the pipeline reads, with no secret on the web side."""
    from minbar.pipeline import llm
    cfg = tmp_path / "llm.json"
    monkeypatch.setenv("MINBAR_LLM_CONFIG", str(cfg))
    for var in ("MINBAR_LLM_ENABLED", "MINBAR_LLM_URL", "MINBAR_LLM_MODEL", "MINBAR_LLM_PROTOCOL"):
        monkeypatch.delenv(var, raising=False)
    c = client(env, "admin")
    assert c.get("/api/v1/settings").json()["model"]["label"]

    r = c.ecrire("/settings/model", {"enabled": "1", "url": "http://127.0.0.1:9/v1", "model": "petit",
                                     "protocol": "openai"}, methode="PUT")
    assert r.status_code == 200, r.text
    assert json.loads(cfg.read_text()) == {"enabled": True, "url": "http://127.0.0.1:9/v1",
                                           "model": "petit", "protocol": "openai"}
    assert llm.enabled() and llm.label() == "openai · petit"      # the pipeline would read this

    r = c.ecrire("/settings/model", {"url": "", "model": "", "protocol": ""}, methode="PUT")
    assert r.status_code == 200
    assert json.loads(cfg.read_text())["enabled"] is False and llm.enabled() is False

    r = c.ecrire("/settings/model", {"enabled": "1", "url": "file:///etc/passwd"}, methode="PUT")
    assert r.status_code == 422
    assert json.loads(cfg.read_text())["enabled"] is False         # rejected: the file did not move

    for who in ("imam", "responsable"):
        ci = client(env, who)
        assert ci.ecrire("/settings/model", {"enabled": "1"}, methode="PUT").status_code == 403


def test_test_de_connexion_du_modele(env, tmp_path, monkeypatch):
    """The “Test” button queries the saved address and reports the failure without breaking the screen."""
    monkeypatch.setenv("MINBAR_LLM_CONFIG", str(tmp_path / "llm.json"))
    monkeypatch.delenv("MINBAR_LLM_ENABLED", raising=False)
    c = client(env, "admin")
    c.ecrire("/settings/model", {"enabled": "1", "url": "http://127.0.0.1:9/v1", "model": "petit",
                                 "protocol": "openai"}, methode="PUT")
    r = c.ecrire("/settings/model/test")
    assert r.status_code == 422
    assert "injoignable" in r.json()["message"]


def test_commande_live_orchestrateur_absent(env):
    c = client(env, "responsable")
    assert c.get("/api/v1/dashboard").json()["live"]["ok"] is False
    r = c.ecrire("/live/stop")
    assert r.status_code == 503
    assert db.one(env["conn"], "SELECT action FROM audit WHERE action LIKE 'live.%'")["action"] == "live.echec"


def test_commande_live_role_requis(env, orch, monkeypatch):
    monkeypatch.setitem(auth.PERMS, "live", {"admin", "responsable"})
    c = client(env, "imam")
    assert c.ecrire("/live/stop").status_code == 403
    assert orch.calls == []


# ------------------------------------------------------------------ import

def test_import_idempotent_et_janaza_ignoree(env):
    conn = env["conn"]
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", env["mid"])
    res = importer.import_run(conn, m, RUN, extract_video=False, log=lambda *a: None)
    assert res["khutbas_new"] == 0 and res["clips_new"] == 0 and res["skipped"] == ["session2"]
    k = db.one(conn, "SELECT * FROM khutbas")
    assert db.one(conn, "SELECT COUNT(*) n FROM khutbas")["n"] == 1
    assert k["theme"] == "La patience" and k["duration_s"] == 20.0 and k["verses_count"] == 1
    c = db.one(conn, "SELECT * FROM clips WHERE idx = 1")
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
    c.ecrire(f"/sermons/{env['kid']}/revisions",
             {"seg": "1", "field": "text", "value": "La patience est une lumière, dit le Prophète ﷺ."})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["needs_regen"] == 1
    assert db.one(conn, "SELECT needs_regen FROM clips WHERE idx = 2")["needs_regen"] == 0     # segment outside clip 2
    # validating is refused as long as the video has not been regenerated
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "a_valider"
    # regeneration: the pipeline does receive the correction (real build_ass, simulated ffmpeg render)
    from minbar.pipeline import clips as clipmod
    seen = {}

    def fake_render(src, off, w, ass, out, log=print, crop=None):
        seen.update(off=off, w=w, ass=(Path(out).parent / ass).read_text())
        Path(out).write_bytes(b"clip v2")
    monkeypatch.setattr(clipmod, "render_clip", fake_render)
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.ecrire(f"/clips/{cid}/regenerate")
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["version"] == 2 and clip["needs_regen"] == 0 and clip["file_path"].endswith("_v2_A-VALIDER.mp4")
    assert seen["off"] == 100.0 and "Prophète" in seen["ass"]
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["status"] == "valide" and clip["decided_by"] == env["ids"]["imam"]
    acts = [a["action"] for a in db.all_(conn, "SELECT action FROM audit WHERE user_id = ? ORDER BY id", env["ids"]["imam"])]
    assert acts[-3:] == ["correction.sous-titre", "clip.regeneration", "clip.valide"]
    # the clip sheet shows who did what
    d = c.get(f"/api/v1/clips/{cid}").json()
    actions = [h["action"] for h in d["history"]]
    assert "correction.sous-titre" in actions and "clip.valide" in actions
    assert any(h["who"] == "Imam" for h in d["history"])
    assert d["clip"]["status"] == "valide" and "status_labels" not in d


def test_correction_traduction_et_verset_protege(env):
    conn = env["conn"]
    k = db.one(conn, "SELECT * FROM khutbas")
    u = db.one(conn, "SELECT * FROM users WHERE username = 'responsable'")
    with pytest.raises(ValueError):                  # segment 2 = verse: the Hamidullah translation cannot be changed
        content.save_correction(conn, k, 2, "fr", "autre traduction", u)
    with pytest.raises(ValueError):                  # French segment: no “translation” field
        content.save_correction(conn, k, 0, "fr", "x", u)
    content.save_correction(conn, k, 0, "text", "Chers frères et sœurs, parlons de la patience.", u)
    rows = content.transcript_rows(content.load(conn, k))
    assert rows[0]["fr"] == "Chers frères et sœurs, parlons de la patience." and rows[0]["corrected"]
    corr = db.one(conn, "SELECT * FROM corrections")
    assert corr["original"].startswith("Chers frères, aujourd'hui") and corr["user_id"] == u["id"]


def test_rejet_avec_motif(env):
    c = client(env, "responsable")
    cid = clip_ids(env)[1]
    c.ecrire(f"/clips/{cid}/decision", {"decision": "rejete", "reason": "coupé en plein milieu"})
    clip = db.one(env["conn"], "SELECT * FROM clips WHERE id = ?", cid)
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
    p = db.one(conn, "SELECT * FROM publications")
    assert p["status"] == "refuse" and "non validé" in p["message"]
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "a_valider"


def test_publication_locale_apres_validation(env):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "Khutba — extrait"})
    assert r.status_code == 200, r.text
    p = db.one(conn, "SELECT * FROM publications")
    assert p["status"] == "publie", p
    out = Path(p["location"])
    assert out.exists() and out.read_bytes() == Path(db.one(conn, "SELECT file_path FROM clips WHERE id = ?", cid)["file_path"]).read_bytes()
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["legende"] == "Khutba — extrait" and meta["valide_par"] == "Responsable" and meta["musique"] is False
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "publie"
    d = c.get("/api/v1/publications").json()
    assert d["publications"] and d["publications"][0]["status"] == "publie"
    assert any(i["id"] == cid for i in d["publications"])


def test_correction_apres_publication_ne_reecrit_pas_l_historique(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"})
    c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    c.ecrire(f"/sermons/{env['kid']}/revisions", {"seg": "1", "field": "text", "value": "Texte corrigé."})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["status"] == "publie" and clip["needs_regen"] == 1
    from minbar.pipeline import clips as clipmod
    monkeypatch.setattr(clipmod, "render_clip", lambda src, off, w, ass, out, log=print, crop=None: Path(out).write_bytes(b"v2"))
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.ecrire(f"/clips/{cid}/regenerate")
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["version"] == 2 and clip["status"] == "a_valider" and clip["decided_by"] is None   # v2 goes back to validation
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    assert r.status_code == 422
    assert db.one(conn, "SELECT status FROM publications ORDER BY id DESC")["status"] == "refuse"


def test_connecteurs_reseaux_desactives_sans_appel_externe(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    resp = db.one(conn, "SELECT * FROM users WHERE username = 'responsable'")
    content.decide(conn, db.one(conn, "SELECT * FROM clips WHERE id = ?", cid), "valide", resp)

    def no_network(*a, **k):
        raise AssertionError("appel réseau interdit")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    rows = {r["key"]: r for r in publish.connector_rows(conn, db.one(conn, "SELECT * FROM mosques WHERE id = ?", env["mid"]))}
    assert rows["local"]["available"]
    for key in ("facebook", "youtube", "instagram"):
        assert not rows[key]["available"] and "identifiants absents" in rows[key]["why"]
        res = publish.publish(conn, db.one(conn, "SELECT * FROM clips WHERE id = ?", cid), key, resp, "x")
        assert not res.ok
    # even with credentials, no real publication in V1
    admin = db.one(conn, "SELECT * FROM users WHERE username = 'admin'")
    publish.save_connector(conn, env["mid"], "facebook", True, {"page_id": "123", "page_token": "secret"}, admin)
    row = next(r for r in publish.connector_rows(conn, db.one(conn, "SELECT * FROM mosques WHERE id = ?", env["mid"])) if r["key"] == "facebook")
    assert not row["available"] and "non implémentée" in row["why"] and {f["name"]: f["value"] for f in row["fields"]}["page_token"] == db.MASK
    res = publish.publish(conn, db.one(conn, "SELECT * FROM clips WHERE id = ?", cid), "facebook", resp, "x")
    assert not res.ok
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "valide"
    assert {p["status"] for p in db.all_(conn, "SELECT status FROM publications")} == {"refuse"}


def test_cli_cree_admin_et_imam(tmp_path, monkeypatch, capsys):
    from minbar.backoffice.__main__ import main
    path = tmp_path / "cli.db"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    assert main(["--db", str(path), "create-admin", "--username", "chef", "--mosque", "ma-mosquee",
                 "--mosque-name", "Mosquée Exemple", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    assert main(["--db", str(path), "create-user", "--username", "imam", "--role", "imam", "--password-stdin"]) == 0
    conn = db.connect(path)
    us = db.all_(conn, "SELECT username, role, mosque_id FROM users ORDER BY id")
    assert [(u["username"], u["role"]) for u in us] == [("chef", "admin"), ("imam", "imam")]
    assert db.one(conn, "SELECT slug FROM mosques")["slug"] == "ma-mosquee"
    assert auth.authenticate(conn, "chef", PW)
