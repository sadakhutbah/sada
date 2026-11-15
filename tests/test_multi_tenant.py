"""Multi-tenant sign-in: hosted mode opens no mosque by itself; the operator enters as support."""

from __future__ import annotations

import pytest
from sqlalchemy import select as sa_select
from test_backoffice import PW, client, login  # noqa: F401

from sada.backoffice import db, orm
from sada.backoffice.models import Audit


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("SADA_MODE", "hosted")


@pytest.fixture
def second(env):
    """A second mosque; the `imam` account also belongs to it, `admin` is the operator."""
    mid2 = db.create_mosque(env["conn"], "autre", "Autre Mosquée")
    from sada.backoffice.models import Membership

    env["conn"].add(Membership(user_id=env["ids"]["imam"], mosque_id=mid2))
    env["conn"].commit()
    return mid2


def select(c, mid):
    return c.ecrire("/mosques/select", {"mosque_id": mid})


def audit_actions(env, mid):
    stmt = sa_select(Audit.action).where(Audit.mosque_id == mid).order_by(Audit.id)
    return [r["action"] for r in orm.rows(env["conn"], stmt)]


def test_self_hosted_login_still_opens_the_mosque(env):
    c = client(env, "imam")
    assert c.last_login.json()["mosque"]["slug"] == "ma-mosquee"
    assert c.get("/api/v1/sermons").status_code == 200


def test_hosted_login_opens_nothing_even_with_one_mosque(env, hosted):
    c = client(env, "responsable")
    body = c.last_login.json()
    assert body["mosque"] is None
    assert [m["slug"] for m in body["mosques"]] == ["ma-mosquee"]
    r = c.get("/api/v1/sermons")
    assert r.status_code == 409 and r.json()["error"] == "mosque_required"
    assert c.get("/api/v1/session").json()["mosque"] is None


def test_hosted_member_picks_a_mosque_then_works(env, hosted, second):
    c = client(env, "imam")
    assert {m["slug"] for m in c.last_login.json()["mosques"]} == {"ma-mosquee", "autre"}
    r = select(c, env["mid"])
    assert r.status_code == 200 and r.json()["mosque"]["slug"] == "ma-mosquee"
    assert c.get("/api/v1/sermons").status_code == 200
    assert "mosque.ouverte" in audit_actions(env, env["mid"])


def test_cannot_open_a_mosque_one_does_not_belong_to(env, hosted):
    other = db.create_mosque(env["conn"], "autre", "Autre Mosquée")
    c = client(env, "responsable")
    assert select(c, other).status_code == 403
    assert c.get("/api/v1/sermons").status_code == 409


def test_switching_mosque_shows_only_that_mosque(env, hosted, second):
    c = client(env, "imam")
    select(c, second)
    assert c.get("/api/v1/sermons").json() == [] or c.get("/api/v1/sermons").json().get("items", []) == []
    c.ecrire("/mosques/leave")
    assert c.get("/api/v1/sermons").status_code == 409
    select(c, env["mid"])
    assert c.get("/api/v1/sermons").status_code == 200


def test_operator_enters_a_mosque_as_support_and_it_is_traced(env, hosted):
    c = client(env, "admin")
    body = c.last_login.json()
    assert body["user"]["operator"] is True and body["mosque"] is None
    r = select(c, env["mid"])
    assert r.status_code == 200 and r.json()["support"] is True
    assert "support.entree" in audit_actions(env, env["mid"])
    before = audit_actions(env, env["mid"]).count("support.action")
    r2 = c.ecrire("/settings/mosque", {"name": "Renommée"}, methode="PATCH")
    assert r2.status_code != 409, r2.text
    assert audit_actions(env, env["mid"]).count("support.action") == before + 1
    c.ecrire("/mosques/leave")
    assert "support.sortie" in audit_actions(env, env["mid"])


def test_a_plain_account_is_never_support(env, hosted):
    c = client(env, "imam2")
    other = db.create_mosque(env["conn"], "x", "X")
    assert select(c, other).status_code == 403
    assert "support.entree" not in audit_actions(env, other)


def test_select_needs_csrf(env, hosted):
    c = client(env, "responsable")
    c.csrf = "faux"
    assert select(c, env["mid"]).status_code == 403
