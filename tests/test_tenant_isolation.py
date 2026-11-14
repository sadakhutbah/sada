"""Tenant isolation: an account from another mosque cannot reach any resource by its id."""

import pytest
import rawsql
from test_backoffice import PW, client

from sada.backoffice import auth, db


@pytest.fixture
def intruder(env):
    conn = env["conn"]
    mid2 = db.create_mosque(conn, "other", "Other mosque")
    auth.create_user(conn, mid2, "admin2", PW, "admin")
    return client(env, "admin2")


def _ids(env):
    conn = env["conn"]
    cid = rawsql.one(conn, "SELECT id FROM clips ORDER BY idx")["id"]
    return {"kid": env["kid"], "cid": cid, "uid": env["ids"]["imam"]}


READS = [
    "/api/v1/sermons/{kid}",
    "/api/v1/sermons/{kid}/habillage",
    "/api/v1/sermons/{kid}/habillage/apercu.jpg",
    "/api/v1/sermons/{kid}/habillage/video.mp4",
    "/api/v1/sermons/{kid}/habillage/sous-titres",
    "/api/v1/sermons/{kid}/habillage/sous-titres/fr.srt",
    "/api/v1/sermons/{kid}/habillage/sous-titres/fr.vtt",
    "/api/v1/clips/{cid}",
    "/media/sermon/{kid}.mp4",
    "/media/clip/{cid}.mp4",
    "/logo/ma-mosquee",
    "/screens/ma-mosquee/ecrans.html",
]

WRITES = [
    ("POST", "/sermons/{kid}/speaker", {"speaker": "x"}),
    ("POST", "/sermons/{kid}/revisions", {"text": "x"}),
    ("POST", "/sermons/{kid}/subtitles", {"text": "x"}),
    ("POST", "/sermons/{kid}/subtitles/1/delete", {}),
    ("POST", "/sermons/{kid}/habillage", {}),
    ("POST", "/sermons/{kid}/habillage/apercu", {}),
    ("POST", "/sermons/{kid}/habillage/sous-titres", {}),
    ("POST", "/sermons/{kid}/clips", {"start": 0, "end": 30}),
    ("POST", "/clips/{cid}/decision", {"decision": "valide"}),
    ("POST", "/clips/{cid}/regenerate", {}),
    ("POST", "/clips/{cid}/publish", {}),
    ("POST", "/clips/{cid}/bounds", {"start": 0, "end": 30}),
    ("POST", "/users/{uid}/password", {"password": "Zz9-zz9-zz9-zz9"}),
    ("PATCH", "/users/{uid}", {"role": "admin", "active": "1"}),
]


@pytest.mark.parametrize("url", READS)
def test_cannot_read_another_mosque(env, intruder, url):
    r = intruder.get(url.format(**_ids(env)))
    assert r.status_code in (403, 404), f"{url} -> {r.status_code}"


@pytest.mark.parametrize(("method", "url", "body"), WRITES)
def test_cannot_write_another_mosque(env, intruder, method, url, body):
    r = intruder.ecrire(url.format(**_ids(env)), body, methode=method)
    assert r.status_code in (403, 404, 422), f"{url} -> {r.status_code} {r.text[:200]}"


def test_lists_are_empty_for_another_mosque(intruder):
    for url in ("/api/v1/sermons", "/api/v1/clips", "/api/v1/audit", "/api/v1/users", "/api/v1/events"):
        r = intruder.get(url)
        if r.status_code != 200:
            continue
        body = r.json()
        rows = body.get("items") or body.get("rows") or body.get("users") or body.get("events") or []
        names = {str(x.get("username", "")) for x in rows if isinstance(x, dict)}
        assert names <= {"admin2", ""}, f"{url} leaks {names}"
        if "count" in body and url in ("/api/v1/sermons", "/api/v1/clips"):
            assert body["count"] == 0, url


def test_clip_settings_are_per_mosque(env, intruder, monkeypatch, tmp_path):
    from sada import paths
    from sada.backoffice import tenant_config, traitement

    monkeypatch.delenv("SADA_PIPELINE_CONFIG", raising=False)
    monkeypatch.delenv("SADA_LLM_CONFIG", raising=False)
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    shared = tmp_path / "data" / "pipeline.json"
    monkeypatch.setattr(tenant_config.pipeline_settings, "config_path", lambda: shared)
    monkeypatch.setitem(tenant_config.FILES, "pipeline", ("SADA_PIPELINE_CONFIG", lambda: shared))

    owner = client(env, "admin")
    assert owner.ecrire("/settings/processing", {"clips": "7", "enabled": "1"}, methode="PUT").status_code == 200
    assert intruder.ecrire("/settings/processing", {"clips": "2", "enabled": "1"}, methode="PUT").status_code == 200

    assert owner.get("/api/v1/settings").json()["processing"]["clips"] == 7
    assert intruder.get("/api/v1/settings").json()["processing"]["clips"] == 2
    assert not shared.exists()  # nothing written to the shared file

    m = rawsql.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    run_env = traitement._env(m)
    assert run_env["SADA_PIPELINE_CONFIG"] == str(tmp_path / "data" / "mosques" / "ma-mosquee" / "pipeline.json")


def test_platform_settings_are_hidden_from_a_mosque_admin(env, intruder):
    d = intruder.get("/api/v1/settings").json()
    assert "model" not in d and "transcription" not in d
    assert "model" not in d["sections"] and "transcription" not in d["sections"]
    for method, url in (
        ("PUT", "/settings/model"),
        ("PUT", "/settings/transcription"),
        ("POST", "/settings/model/test"),
        ("POST", "/settings/transcription/test"),
    ):
        assert intruder.ecrire(url, {"mode": "local"}, methode=method).status_code == 403, url
    owner = client(env, "admin")
    assert "model" in owner.get("/api/v1/settings").json()["sections"]


def test_platform_permission_cannot_be_granted_by_a_form(env):
    owner = client(env, "admin")
    uid = env["ids"]["responsable"]
    r = owner.ecrire(f"/users/{uid}", {"permissions": '["admin", "platform"]'}, methode="PATCH")
    assert r.status_code in (400, 422)


def test_migration_keeps_a_single_mosque_admin_in_charge(tmp_path):
    from sada.backoffice import migrations

    path = tmp_path / "old.db"
    db.init(path)
    conn = db.connect(path)
    mid = db.create_mosque(conn, "seule", "Seule")
    uid = auth.create_user(conn, mid, "chef", PW, "admin")
    rawsql.run(conn, "DELETE FROM schema_migrations WHERE version = 11")
    raw = db.connect_raw(path)
    migrations._users_plateforme(raw)
    raw.close()
    assert rawsql.one(conn, "SELECT platform_admin FROM users WHERE id = ?", uid)["platform_admin"] == 1


def test_first_cli_admin_is_platform_admin(tmp_path, monkeypatch):
    from sada.backoffice import __main__ as cli

    path = tmp_path / "cli.db"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    cli.main(["--db", str(path), "create-admin", "--username", "boss", "--password-stdin"])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PW + "\n"))
    cli.main(["--db", str(path), "create-admin", "--username", "other", "--password-stdin"])
    conn = db.connect(path)
    flags = {
        r["username"]: r["platform_admin"] for r in rawsql.all_(conn, "SELECT username, platform_admin FROM users")
    }
    assert flags == {"boss": 1, "other": 0}


def _agent(env, url, token):
    from sada.backoffice import internal

    c = client(env)
    return c.get(url, headers={internal.HEADER: token} if token else {})


INTERNAL = [
    "/api/v1/internal/notification-settings",
    "/api/v1/internal/live-targets",
    "/api/v1/internal/storage",
    "/api/v1/internal/screens",
    "/api/v1/internal/gabarit",
    "/api/v1/internal/live-input",
    "/api/v1/internal/events",
]
# /internal/times is covered by test_times_cached_per_mosque (it needs a fake Mawaqit)


@pytest.mark.parametrize("url", INTERNAL)
def test_agent_token_names_its_mosque(env, url):
    from sada.backoffice import internal

    conn = env["conn"]
    mid2 = db.create_mosque(conn, "other", "Other mosque", orchestrator_url="http://127.0.0.1:9")
    t1 = internal.new_agent_token(conn, env["mid"])
    t2 = internal.new_agent_token(conn, mid2)
    assert _agent(env, url, t1).json()["mosque_id"] == env["mid"]
    # same port as the first mosque: the token decides, not the port
    assert _agent(env, url + "?port=9", t2).json()["mosque_id"] == mid2
    assert _agent(env, url, "sada_wrong").status_code == 403
    assert _agent(env, url, None).status_code == 403


@pytest.mark.parametrize("url", INTERNAL)
def test_shared_token_refused_once_there_are_several_mosques(env, url):
    from sada.backoffice import internal

    shared = internal.local_token()
    assert _agent(env, url, shared).status_code == 200
    db.create_mosque(env["conn"], "other", "Other mosque")
    assert _agent(env, url, shared).status_code == 403


def test_agent_token_rotation_and_revocation(env):
    from sada.backoffice import internal

    conn = env["conn"]
    old = internal.new_agent_token(conn, env["mid"])
    new = internal.new_agent_token(conn, env["mid"])
    assert internal.agent_mosque(conn, old) is None
    assert internal.agent_mosque(conn, new) == env["mid"]
    internal.revoke_agent_token(conn, env["mid"])
    assert internal.agent_mosque(conn, new) is None
    assert internal.agent_mosque(conn, "") is None
    stored = rawsql.one(conn, "SELECT agent_token_hash FROM mosques WHERE id = ?", env["mid"])["agent_token_hash"]
    assert new not in stored


def test_orchestrator_presents_the_agent_token(monkeypatch, tmp_path):
    from sada.backoffice import internal

    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    local = internal.token()
    monkeypatch.setenv(internal.AGENT_ENV, "sada_abc")
    assert internal.token() == "sada_abc"
    assert internal.local_token() == local
    assert not internal.check("sada_abc")


def test_programme_is_per_mosque(env, intruder):
    dars = {"kind": "dars", "title": "Fiqh", "mode": "once", "date": "2099-01-01", "time": "19:00", "publish": "1"}
    owner = client(env, "admin")
    uid = owner.ecrire("/events", dars).json()["id"]
    assert intruder.get("/api/v1/events").json()["items"] == []
    assert intruder.ecrire(f"/events/{uid}", {**dars, "title": "Hijacked"}, methode="PUT").status_code == 404
    assert intruder.ecrire(f"/events/{uid}", {}, methode="DELETE").status_code == 404
    assert [i["title"] for i in owner.get("/api/v1/events").json()["items"]] == ["Fiqh"]
    # the orchestrator of each mosque only receives its own programme
    from sada.backoffice import internal

    conn = env["conn"]
    other = rawsql.one(conn, "SELECT id FROM mosques WHERE id != ?", env["mid"])["id"]
    t_own, t_other = internal.new_agent_token(conn, env["mid"]), internal.new_agent_token(conn, other)
    assert [e["title"] for e in _agent(env, "/api/v1/internal/events", t_own).json()["events"]] == ["Fiqh"]
    assert _agent(env, "/api/v1/internal/events", t_other).json()["events"] == []


def test_times_cached_per_mosque(env, intruder):
    from sada.backoffice import mawaqit_store

    conn = env["conn"]
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return {"name": url, "jumua": "13:00", "times": ["06:00", "12:30"]}

    a = rawsql.one(conn, "SELECT * FROM mosques WHERE id = ?", env["mid"])
    b = rawsql.one(conn, "SELECT * FROM mosques WHERE id != ?", env["mid"])
    a["mawaqit_url"], b["mawaqit_url"] = "https://m/a", "https://m/b"
    assert mawaqit_store.times(conn, a, fetch=fetch, now=1000)["name"] == "https://m/a"
    assert mawaqit_store.times(conn, b, fetch=fetch, now=1000)["name"] == "https://m/b"
    # fresh: no new fetch; each mosque keeps its own cache
    assert mawaqit_store.times(conn, a, fetch=fetch, now=2000)["origin"] == "cache (fresh)"
    assert calls == ["https://m/a", "https://m/b"]

    def down(url, timeout):
        raise OSError("down")

    stale = mawaqit_store.times(conn, b, fetch=down, now=1000 + 30 * 3600)
    assert stale["name"] == "https://m/b" and stale["jumua_list"] == ["13:00"]


def test_orchestrator_reads_times_from_backoffice():
    from sada import mawaqit

    d = mawaqit.from_backoffice("http://bo", 0, fetch=lambda u, t: {"times": {"jumua": "13:15", "origin": "live"}})
    assert d["jumua_list"] == ["13:15"]

    def down(u, t):
        raise OSError("down")

    assert mawaqit.from_backoffice("http://bo", 0, fetch=down) is None


def test_stream_key_per_mosque(env, intruder):
    from sada.backoffice import internal

    conn = env["conn"]
    slugs = [r["slug"] for r in rawsql.all_(conn, "SELECT slug FROM mosques ORDER BY id")]
    mids = [r["id"] for r in rawsql.all_(conn, "SELECT id FROM mosques ORDER BY id")]
    ka = internal.new_publish_key(conn, mids[0])
    c = client(env)

    def ask(**q):
        return c.post("/api/v1/internal/publish-auth", json=q).status_code

    assert ask(action="publish", path=f"live/{slugs[0]}", password=ka) == 200
    assert ask(action="publish", path=f"live/{slugs[1]}", password=ka) == 401  # not its mosque
    assert ask(action="publish", path=f"live/{slugs[1]}", password="") == 401  # no key set = closed
    assert ask(action="publish", path=f"live/{slugs[0]}", password="wrong") == 401
    assert ask(action="read", path=f"live/{slugs[0]}", ip="172.18.0.5") == 200
    assert ask(action="read", path=f"live/{slugs[0]}", ip="8.8.8.8") == 401
    assert ask(action="api", path="", ip="172.18.0.5") == 401
    internal.new_publish_key(conn, mids[0])
    assert ask(action="publish", path=f"live/{slugs[0]}", password=ka) == 401  # replaced


def test_stream_key_from_backoffice_shown_once(env, intruder):
    from sada.backoffice import internal

    owner = client(env, "admin")
    r1 = owner.ecrire("/settings/input/key", {}, methode="POST").json()
    slug = rawsql.one(env["conn"], "SELECT slug FROM mosques WHERE id = ?", env["mid"])["slug"]
    assert internal.publish_allowed(env["conn"], slug, r1["key"])
    view = owner.get("/api/v1/settings").json()["input"]
    assert view["has_key"] and r1["key"] not in str(view)  # never readable again
    r2 = owner.ecrire("/settings/input/key", {}, methode="POST").json()
    assert not internal.publish_allowed(env["conn"], slug, r1["key"])  # re-showing = new key
    assert internal.publish_allowed(env["conn"], slug, r2["key"])
    # another mosque's admin only ever gets a key for its own mosque
    r3 = intruder.ecrire("/settings/input/key", {}, methode="POST").json()
    assert not internal.publish_allowed(env["conn"], slug, r3["key"])
    assert internal.publish_allowed(env["conn"], slug, r2["key"])
    assert client(env, "imam").ecrire("/settings/input/key", {}, methode="POST").status_code == 403
