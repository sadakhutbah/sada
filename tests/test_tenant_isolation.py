"""Tenant isolation: an account from another mosque cannot reach any resource by its id."""

import pytest
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
    cid = db.one(conn, "SELECT id FROM clips ORDER BY idx")["id"]
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
