"""Back-office : authentification, droits, cloisonnement des mosquées, validation des clips, publication."""
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
    """Run de pipeline minimal : 1 session (+ 1 janaza éventuelle), 2 clips."""
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
    mid = db.create_mosque(conn, "exemple", "Mosquée Exemple", state_dir=str(tmp_path / "state"),
                           output_dir=str(tmp_path / "output"), publish_dir=str(tmp_path / "pub"),
                           orchestrator_url="http://127.0.0.1:9")
    ids = {r: auth.create_user(conn, mid, r, PW, r, r.capitalize()) for r in auth.ROLES}
    ids["imam2"] = auth.create_user(conn, mid, "imam2", PW, "imam", "Autre imam")
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", mid)
    importer.import_run(conn, m, RUN, extract_video=False, log=lambda *a: None)
    kid = db.one(conn, "SELECT id FROM khutbas")["id"]
    conn.execute("UPDATE khutbas SET imam_id = ? WHERE id = ?", (ids["imam"], kid))
    app = create_app(path)
    app.state.regen_background = False
    yield {"app": app, "conn": conn, "path": path, "mid": mid, "ids": ids, "kid": kid, "tmp": tmp_path}
    conn.close()


def client(env, username=None) -> TestClient:
    c = TestClient(env["app"], follow_redirects=False)
    if username:
        login(c, username)
    return c


def login(c: TestClient, username: str, password: str = PW):
    page = c.get("/connexion")
    pre = re.search(r'name="pre" value="([^"]+)"', page.text).group(1)
    return c.post("/connexion", data={"username": username, "password": password, "pre": pre, "suite": "/"})


def csrf(c: TestClient, url: str = "/compte") -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get(url).text).group(1)


def clip_ids(env):
    return [r["id"] for r in db.all_(env["conn"], "SELECT id FROM clips ORDER BY idx")]


# ------------------------------------------------------------------ authentification

def test_rien_sans_connexion(env):
    c = client(env)
    for url in ("/", "/khutbas", f"/khutbas/{env['kid']}", "/clips", "/journal", "/publication", "/admin/mosquee"):
        r = c.get(url)
        assert r.status_code == 303 and r.headers["location"].startswith("/connexion"), url
    assert c.get("/api/live").status_code == 401
    assert c.get(f"/media/clip/{clip_ids(env)[0]}.mp4").status_code == 401
    assert c.post(f"/clips/{clip_ids(env)[0]}/decision", data={"decision": "valide"}).headers["location"] == "/connexion"


def test_mots_de_passe_haches_argon2(env):
    rows = db.all_(env["conn"], "SELECT password_hash FROM users")
    assert all(r["password_hash"].startswith("$argon2id$") and PW not in r["password_hash"] for r in rows)
    with pytest.raises(ValueError):
        auth.create_user(env["conn"], env["mid"], "court", "123", "imam")


def test_connexion_cookie_et_deconnexion(env):
    c = client(env)
    assert login(c, "admin", "mauvais-mot-de-passe").status_code == 401
    assert login(c, "inconnu", PW).status_code == 401
    r = login(c, "admin")
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "minbar_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = c.cookies.get(auth.COOKIE)
    assert db.one(env["conn"], "SELECT COUNT(*) n FROM auth_sessions WHERE token_hash = ?", token)["n"] == 0   # jeton non stocké en clair
    assert c.get("/").status_code == 200
    c.post("/deconnexion", data={"csrf": csrf(c)})
    c.cookies.set(auth.COOKIE, token)                        # rejouer l'ancien cookie ne marche plus
    assert c.get("/").status_code == 303


def test_compte_desactive_refuse(env):
    env["conn"].execute("UPDATE users SET active = 0 WHERE username = 'imam2'")
    assert login(client(env), "imam2").status_code == 401


def test_csrf_obligatoire(env):
    c = client(env, "admin")
    cid = clip_ids(env)[0]
    r = c.post(f"/clips/{cid}/decision", data={"decision": "rejete"})
    assert r.status_code == 403
    r = c.post(f"/clips/{cid}/decision", data={"decision": "rejete", "csrf": "faux"})
    assert r.status_code == 403
    assert db.one(env["conn"], "SELECT status FROM clips WHERE id = ?", cid)["status"] == "a_valider"


def test_limitation_des_essais(env):
    c = client(env)
    for _ in range(5):
        assert login(c, "admin", "mauvais-mot-de-passe").status_code == 401
    assert login(c, "admin").status_code == 429                 # même le bon mot de passe est bloqué


def test_journal_des_connexions(env):
    c = client(env)
    login(c, "admin", "mauvais-mot-de-passe")
    login(c, "admin")
    acts = [a["action"] for a in db.all_(env["conn"], "SELECT action FROM audit WHERE action LIKE 'connexion%' ORDER BY id")]
    assert acts == ["connexion.echec", "connexion"]


# ------------------------------------------------------------------ droits

@pytest.mark.parametrize("role,url,code", [
    ("imam", "/journal", 403), ("imam", "/publication", 403), ("imam", "/admin/mosquee", 403), ("imam", "/admin/utilisateurs", 403),
    ("responsable", "/admin/mosquee", 403), ("responsable", "/admin/utilisateurs", 403),
    ("responsable", "/journal", 200), ("responsable", "/publication", 200),
    ("admin", "/admin/mosquee", 200), ("admin", "/admin/utilisateurs", 200), ("admin", "/journal", 200),
])
def test_matrice_des_droits(env, role, url, code):
    assert client(env, role).get(url).status_code == code


def test_imam_voit_ses_khutbas_seulement(env):
    kid, cid = env["kid"], clip_ids(env)[0]
    mine = client(env, "imam")
    assert mine.get(f"/khutbas/{kid}").status_code == 200
    assert "La patience" in mine.get("/khutbas").text
    other = client(env, "imam2")
    assert other.get(f"/khutbas/{kid}").status_code == 404
    assert other.get(f"/clips/{cid}").status_code == 404
    assert other.get(f"/media/clip/{cid}.mp4").status_code == 404
    assert "Aucune khutba" in other.get("/khutbas").text
    r = other.post(f"/clips/{cid}/decision", data={"decision": "valide", "csrf": csrf(other)})
    assert r.status_code == 404


def test_cloisonnement_entre_mosquees(env):
    conn = env["conn"]
    mid2 = db.create_mosque(conn, "autre", "Autre mosquée")
    auth.create_user(conn, mid2, "admin2", PW, "admin")
    c = client(env, "admin2")
    assert c.get(f"/khutbas/{env['kid']}").status_code == 404
    assert c.get(f"/clips/{clip_ids(env)[0]}").status_code == 404
    assert "Aucune khutba" in c.get("/khutbas").text
    r = c.post(f"/admin/utilisateurs/{env['ids']['imam']}", data={"role": "admin", "active": "1", "csrf": csrf(c)})
    assert r.status_code == 404


def test_imam_ne_peut_pas_publier(env):
    cid = clip_ids(env)[0]
    content.decide(env["conn"], db.one(env["conn"], "SELECT * FROM clips WHERE id = ?", cid), "valide",
                   db.one(env["conn"], "SELECT * FROM users WHERE username = 'responsable'"))
    c = client(env, "imam")
    r = c.post(f"/clips/{cid}/publier", data={"connector": "local", "caption": "x", "csrf": csrf(c)})
    assert r.status_code == 403
    assert db.one(env["conn"], "SELECT status FROM clips WHERE id = ?", cid)["status"] == "valide"


def test_admin_cree_un_imam(env):
    c = client(env, "admin")
    c.post("/admin/utilisateurs", data={"username": "nouvel", "display_name": "Imam Nouvel", "role": "imam", "password": PW,
                                        "csrf": csrf(c)})
    u = db.one(env["conn"], "SELECT * FROM users WHERE username = 'nouvel'")
    assert u and u["role"] == "imam" and u["mosque_id"] == env["mid"]
    assert login(client(env), "nouvel").status_code == 303


# ------------------------------------------------------------------ live (vraie API de l'orchestrateur)

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
    page = c.get("/").text
    assert "13:42:10" in page and "Khutba en direct" in page and "Session 1/2 — 13:30" in page
    for cmd in ("extend", "janaza", "stop", "cancel"):
        assert c.post(f"/live/{cmd}", data={"csrf": csrf(c, "/")}).status_code == 303
    assert orch.calls == [("extend", "5"), ("janaza", None), ("stop", None), ("cancel", None)]
    log = db.all_(env["conn"], "SELECT * FROM audit WHERE action = 'live.commande' ORDER BY id")
    assert len(log) == 4 and all(a["user_id"] == env["ids"]["imam"] for a in log)


def test_commande_live_orchestrateur_absent(env):
    c = client(env, "responsable")
    assert "injoignable" in c.get("/").text
    c.post("/live/stop", data={"csrf": csrf(c, "/")})
    assert db.one(env["conn"], "SELECT action FROM audit WHERE action LIKE 'live.%'")["action"] == "live.echec"


def test_commande_live_role_requis(env, orch, monkeypatch):
    monkeypatch.setitem(auth.PERMS, "live", {"admin", "responsable"})
    c = client(env, "imam")
    assert c.post("/live/stop", data={"csrf": csrf(c, "/")}).status_code == 403
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


def test_page_detail_khutba(env):
    t = client(env, "responsable").get(f"/khutbas/{env['kid']}").text
    assert "65:3" in t and "At-Talaaq" in t and "Et quiconque place sa confiance" in t
    assert "La patience est une lumière." in t and "00:20" in t


# ------------------------------------------------------------------ validation

def test_correction_puis_regeneration_puis_validation(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "imam")
    c.post(f"/khutbas/{env['kid']}/correction",
           data={"seg": "1", "field": "text", "value": "La patience est une lumière, dit le Prophète ﷺ.", "csrf": csrf(c), "next": f"/clips/{cid}"})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["needs_regen"] == 1
    assert db.one(conn, "SELECT needs_regen FROM clips WHERE idx = 2")["needs_regen"] == 0     # segment hors du clip 2
    # valider est refusé tant que la vidéo n'est pas régénérée
    c.post(f"/clips/{cid}/decision", data={"decision": "valide", "csrf": csrf(c)})
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "a_valider"
    # régénération : le pipeline reçoit bien la correction (build_ass réel, rendu ffmpeg simulé)
    from minbar.pipeline import clips as clipmod
    seen = {}

    def fake_render(src, off, w, ass, out, log=print, crop=None):
        seen.update(off=off, w=w, ass=(Path(out).parent / ass).read_text())
        Path(out).write_bytes(b"clip v2")
    monkeypatch.setattr(clipmod, "render_clip", fake_render)
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.post(f"/clips/{cid}/regenerer", data={"csrf": csrf(c)})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["version"] == 2 and clip["needs_regen"] == 0 and clip["file_path"].endswith("_v2_A-VALIDER.mp4")
    assert seen["off"] == 100.0 and "Prophète" in seen["ass"]
    c.post(f"/clips/{cid}/decision", data={"decision": "valide", "csrf": csrf(c)})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["status"] == "valide" and clip["decided_by"] == env["ids"]["imam"]
    acts = [a["action"] for a in db.all_(conn, "SELECT action FROM audit WHERE user_id = ? ORDER BY id", env["ids"]["imam"])]
    assert acts[-3:] == ["correction.sous-titre", "clip.regeneration", "clip.valide"]
    # l'historique du clip montre qui a fait quoi
    page = c.get(f"/clips/{cid}").text
    assert "Sous-titre corrigé" in page and "Clip validé" in page and "Imam" in page


def test_correction_traduction_et_verset_protege(env):
    conn = env["conn"]
    k = db.one(conn, "SELECT * FROM khutbas")
    u = db.one(conn, "SELECT * FROM users WHERE username = 'responsable'")
    with pytest.raises(ValueError):                  # segment 2 = verset : traduction Hamidullah non modifiable
        content.save_correction(conn, k, 2, "fr", "autre traduction", u)
    with pytest.raises(ValueError):                  # segment français : pas de champ « traduction »
        content.save_correction(conn, k, 0, "fr", "x", u)
    content.save_correction(conn, k, 0, "text", "Chers frères et sœurs, parlons de la patience.", u)
    rows = content.transcript_rows(content.load(conn, k))
    assert rows[0]["fr"] == "Chers frères et sœurs, parlons de la patience." and rows[0]["corrected"]
    corr = db.one(conn, "SELECT * FROM corrections")
    assert corr["original"].startswith("Chers frères, aujourd'hui") and corr["user_id"] == u["id"]


def test_rejet_avec_motif(env):
    c = client(env, "responsable")
    cid = clip_ids(env)[1]
    c.post(f"/clips/{cid}/decision", data={"decision": "rejete", "reason": "coupé en plein milieu", "csrf": csrf(c)})
    clip = db.one(env["conn"], "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["status"] == "rejete" and clip["reject_reason"] == "coupé en plein milieu"
    assert "Rejeté" in c.get("/clips?statut=rejete").text


# ------------------------------------------------------------------ publication

def test_rien_ne_se_publie_sans_validation(env):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    r = c.post(f"/clips/{cid}/publier", data={"connector": "local", "caption": "x", "csrf": csrf(c)})
    assert r.status_code == 303
    assert not (env["tmp"] / "pub").exists()
    p = db.one(conn, "SELECT * FROM publications")
    assert p["status"] == "refuse" and "non validé" in p["message"]
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "a_valider"


def test_publication_locale_apres_validation(env):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    c.post(f"/clips/{cid}/decision", data={"decision": "valide", "csrf": csrf(c)})
    c.post(f"/clips/{cid}/publier", data={"connector": "local", "caption": "Khutba — extrait", "csrf": csrf(c)})
    p = db.one(conn, "SELECT * FROM publications")
    assert p["status"] == "publie", p
    out = Path(p["location"])
    assert out.exists() and out.read_bytes() == Path(db.one(conn, "SELECT file_path FROM clips WHERE id = ?", cid)["file_path"]).read_bytes()
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["legende"] == "Khutba — extrait" and meta["valide_par"] == "Responsable" and meta["musique"] is False
    assert db.one(conn, "SELECT status FROM clips WHERE id = ?", cid)["status"] == "publie"
    assert "Publié" in c.get("/publication").text


def test_correction_apres_publication_ne_reecrit_pas_l_historique(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    c = client(env, "responsable")
    c.post(f"/clips/{cid}/decision", data={"decision": "valide", "csrf": csrf(c)})
    c.post(f"/clips/{cid}/publier", data={"connector": "local", "caption": "x", "csrf": csrf(c)})
    c.post(f"/khutbas/{env['kid']}/correction", data={"seg": "1", "field": "text", "value": "Texte corrigé.", "csrf": csrf(c)})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["status"] == "publie" and clip["needs_regen"] == 1
    from minbar.pipeline import clips as clipmod
    monkeypatch.setattr(clipmod, "render_clip", lambda src, off, w, ass, out, log=print, crop=None: Path(out).write_bytes(b"v2"))
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    c.post(f"/clips/{cid}/regenerer", data={"csrf": csrf(c)})
    clip = db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)
    assert clip["version"] == 2 and clip["status"] == "a_valider" and clip["decided_by"] is None   # v2 repasse en validation
    r = c.post(f"/clips/{cid}/publier", data={"connector": "local", "caption": "x", "csrf": csrf(c)})
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
    # même avec des identifiants, aucune publication réelle en V1
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
    assert main(["--db", str(path), "create-admin", "--username", "chef", "--mosque", "exemple",
                 "--mosque-name", "Mosquée Exemple", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    assert main(["--db", str(path), "create-user", "--username", "imam", "--role", "imam", "--password-stdin"]) == 0
    conn = db.connect(path)
    us = db.all_(conn, "SELECT username, role, mosque_id FROM users ORDER BY id")
    assert [(u["username"], u["role"]) for u in us] == [("chef", "admin"), ("imam", "imam")]
    assert db.one(conn, "SELECT slug FROM mosques")["slug"] == "exemple"
    assert auth.authenticate(conn, "chef", PW)
