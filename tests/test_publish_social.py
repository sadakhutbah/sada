"""Real publication to the three networks, exercised end to end against a fake HTTP session.

No network: `social.SESSION` and `social.SLEEP` are replaced. What is checked is the protocol we send
(addresses, methods, credentials, form fields, file body, polling) and how a refusal is reported.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from minbar.backoffice import publish, social


class Reply:
    def __init__(self, status: int = 200, payload=None, headers: dict | None = None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = b"1" if payload is not None else b""
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("pas de JSON")
        return self._payload


class FakeSession:
    """Records every call and answers from a queue; a reply may be an exception to simulate a failure."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[SimpleNamespace] = []

    def _answer(self, method, url, **kw):
        # file handles are closed as soon as the caller returns: the bytes are read now
        if "data" in kw and hasattr(kw["data"], "read"):
            kw["data"] = kw["data"].read()
        if "files" in kw:
            kw["files"] = {k: (v[0], v[1].read(), v[2]) for k, v in kw["files"].items()}
        self.calls.append(SimpleNamespace(method=method, url=url, **kw))
        if not self.answers:
            raise AssertionError(f"appel inattendu : {method} {url}")
        r = self.answers.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(method, url, **kw)
        return r

    def post(self, url, **kw):
        return self._answer("POST", url, **kw)

    def put(self, url, **kw):
        return self._answer("PUT", url, **kw)

    def get(self, url, **kw):
        return self._answer("GET", url, **kw)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(social, "SLEEP", lambda _s: None)


@pytest.fixture
def item(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    clip = {"id": 7, "idx": 2, "kind": "passage", "version": 3, "start": 30.0, "end": 75.0,
            "why": "verset", "file_path": str(video)}
    khutba = {"label": "Khutba du 6 novembre", "date": "2026-11-06", "run_id": "20261106_191913",
              "session_index": 1, "kind": "khutba", "title": ""}
    mosque = {"slug": "exemple", "name": "Mosquée Exemple", "clip_title": "Khutba du vendredi"}
    return SimpleNamespace(clip=clip, khutba=khutba, mosque=mosque, caption="Extrait du vendredi",
                           validated_by="Imam Abdallah", published_by="Essai")


def use(monkeypatch, *answers):
    s = FakeSession(*answers)
    monkeypatch.setattr(social, "SESSION", s)
    return s


# --- Facebook ---------------------------------------------------------------------------------

def test_facebook_envoie_le_fichier_et_rend_le_lien(item, monkeypatch):
    s = use(monkeypatch, Reply(200, {"id": "123_456", "permalink_url": "https://fb.example/123_456"}))
    where = social.facebook(item, {"page_id": "123", "page_token": "TOK"})
    assert where == "https://fb.example/123_456"
    call = s.calls[0]
    assert call.url == "https://graph-video.facebook.com/v21.0/123/videos"
    assert call.data["access_token"] == "TOK" and call.data["published"] == "true"
    assert call.data["description"] == "Extrait du vendredi"
    name, body, mime = call.files["source"]
    assert name == "clip.mp4" and mime == "video/mp4" and body.startswith(b"\x00\x00\x00\x18")
    assert call.timeout >= 600                     # a clip on a mosque's connection is not a JSON call


def test_facebook_erreur_de_l_api_reprise_telle_quelle(item, monkeypatch):
    use(monkeypatch, Reply(400, {"error": {"message": "Invalid OAuth access token.",
                                           "error_user_msg": "Jeton expiré"}}))
    with pytest.raises(social.PublishError) as e:
        social.facebook(item, {"page_id": "123", "page_token": "TOK"})
    assert "HTTP 400" in str(e.value) and "Invalid OAuth access token." in str(e.value)
    assert "Jeton expiré" in str(e.value)


# --- YouTube ----------------------------------------------------------------------------------

def test_youtube_oauth_puis_envoi_resumable(item, monkeypatch):
    def init(method, url, **kw):
        assert "uploadType=resumable" in url and kw["headers"]["Authorization"] == "Bearer AT"
        body = json.loads(kw["data"])
        assert body["snippet"]["title"].startswith("Khutba du vendredi — Mosquée Exemple")
        assert len(body["snippet"]["title"]) <= 100
        assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
        return Reply(200, None, {"location": "https://upload.example/session/1"})

    s = use(monkeypatch,
            Reply(200, {"access_token": "AT"}),
            init,
            Reply(200, {"id": "abC123"}))
    where = social.youtube(item, {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt",
                                  "privacy_status": "unlisted"})
    assert where == "https://youtu.be/abC123"
    token_call, upload_init, put = s.calls
    assert token_call.url == "https://oauth2.googleapis.com/token"
    assert token_call.data["grant_type"] == "refresh_token" and token_call.data["refresh_token"] == "rt"
    assert upload_init.method == "POST"
    assert put.method == "PUT" and put.url == "https://upload.example/session/1"
    assert put.headers["Content-Type"] == "video/mp4" and put.data[:4] == b"\x00\x00\x00\x18"


def test_youtube_refus_du_jeton(item, monkeypatch):
    use(monkeypatch, Reply(400, {"error": "invalid_grant"}))
    with pytest.raises(social.PublishError) as e:
        social.youtube(item, {"client_id": "cid", "client_secret": "sec", "refresh_token": "vieux"})
    assert "jeton Google" in str(e.value)


# --- Instagram --------------------------------------------------------------------------------

def test_instagram_archive_puis_attend_la_transcodage(item, tmp_path, monkeypatch):
    folder = tmp_path / "publie"
    s = use(monkeypatch,
            Reply(200, {"id": "creation1"}),
            Reply(200, {"status_code": "IN_PROGRESS"}),
            Reply(200, {"status_code": "FINISHED"}),
            Reply(200, {"id": "reel42"}))
    where = social.instagram(item, {"ig_user_id": "1789", "access_token": "IGT",
                                    "public_base_url": "https://mosquee.example/clips/",
                                    "folder": str(folder)})
    assert where == "https://www.instagram.com/reel/reel42"
    created = folder / "2026-11-06" / f"{social.published_name(item)}.mp4"
    assert created.exists() and created.read_bytes().startswith(b"\x00\x00\x00\x18")
    assert (created.parent / f"{social.published_name(item)}.json").exists()

    create, poll1, poll2, pub = s.calls
    assert create.url == "https://graph.facebook.com/v21.0/1789/media"
    assert create.data["media_type"] == "REELS" and create.data["share_to_feed"] == "true"
    assert create.data["video_url"] == f"https://mosquee.example/clips/2026-11-06/{social.published_name(item)}.mp4"
    assert create.data["caption"] == "Extrait du vendredi"
    assert poll1.method == poll2.method == "GET" and "creation1" in poll2.url
    assert pub.url == "https://graph.facebook.com/v21.0/1789/media_publish"
    assert pub.data["creation_id"] == "creation1"


def test_instagram_erreur_de_transcodage(item, tmp_path, monkeypatch):
    use(monkeypatch,
        Reply(200, {"id": "c1"}),
        Reply(200, {"status_code": "ERROR", "status": "Le format de la vidéo est refusé"}))
    with pytest.raises(social.PublishError) as e:
        social.instagram(item, {"ig_user_id": "1789", "access_token": "IGT",
                                "public_base_url": "https://mosquee.example/clips",
                                "folder": str(tmp_path)})
    assert "format de la vidéo" in str(e.value)


def test_instagram_sans_adresse_publique_pas_d_appel(item, monkeypatch):
    """The Reel cannot work without a public address: the connector says so and calls nothing."""
    s = use(monkeypatch)
    row = {"settings": json.dumps({"ig_user_id": "1789", "access_token": "IGT"}), "enabled": 1}
    ok, why = publish.Instagram().availability(row)
    assert not ok and "public_base_url" in why
    # an address that is not an http(s) URL would make the API fetch nothing
    row["settings"] = json.dumps({"ig_user_id": "1789", "access_token": "IGT",
                                  "public_base_url": "/srv/clips"})
    ok, why = publish.Instagram().availability(row)
    assert not ok and "adresse publique" in why
    # with a real address it becomes available, without any call
    row["settings"] = json.dumps({"ig_user_id": "1789", "access_token": "IGT",
                                  "public_base_url": "https://mosquee.example/clips"})
    assert publish.Instagram().availability(row) == (True, "")
    assert s.calls == []


# --- availability and wiring ------------------------------------------------------------------

def test_jeton_absent_desactive_le_connecteur():
    ok, why = publish.YouTube().availability(None)
    assert not ok and "client_id" in why and "refresh_token" in why


def test_connecteurs_reseau_sont_reels():
    assert all(c.implemented for c in publish.CONNECTORS.values())
    assert set(publish.CONNECTORS) == {"local", "facebook", "youtube", "instagram"}
