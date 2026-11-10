"""Real publication to Facebook, YouTube and Instagram.

Nothing happens without credentials: no destination, no network call. Every call goes through
`SESSION` (a `requests.Session`) and every wait through `SLEEP`, so the tests exercise the whole
protocol against a fake session instead of mocking our own code.

Deliberately small: one function per network, returning the public address of what was published,
raising `PublishError` with the platform's own message when it refuses. The caller logs it.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import requests

from .. import runs
from ..events import kind_label
from . import db

GRAPH = "https://graph.facebook.com/v21.0"
GRAPH_VIDEO = "https://graph-video.facebook.com/v21.0"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"

UPLOAD_TIMEOUT_S = 1800          # a clip is tens of megabytes on a mosque's connection
SESSION = requests.Session()
SLEEP = time.sleep
POLL_ATTEMPTS = 24               # Instagram transcodes: up to two minutes of polling
POLL_DELAY_S = 5.0


class PublishError(Exception):
    """The platform refused: its message is shown as is, nothing is invented."""


def _message(r) -> str:
    """The platform's error, verbatim if possible: an operator has to be able to act on it."""
    try:
        d = r.json()
    except Exception:                                  # noqa: BLE001 - plain text or HTML error page
        return (r.text or "").strip()[:300]
    err = d.get("error") if isinstance(d, dict) else None
    if isinstance(err, dict):
        parts = [err.get("message") or "", (err.get("error_user_msg") or "")]
        detail = (err.get("error_data") or {}).get("details") if isinstance(err.get("error_data"), dict) else ""
        return " ".join(p for p in (*parts, detail) if p)[:400]
    return str(err or d)[:300]


def _ok(r, what: str):
    """A response, or a `PublishError` naming the step that failed."""
    if r.status_code >= 400:
        raise PublishError(f"{what} : HTTP {r.status_code} — {_message(r)}")
    return r


def _json(r, what: str) -> dict:
    body = _ok(r, what).json()
    if not isinstance(body, dict):
        raise PublishError(f"{what} : réponse inattendue")
    return body


def published_name(item) -> str:
    """Stable file name of a published clip: the same in the folder and behind the public address."""
    k, c, m = item.khutba, item.clip, item.mosque
    raw = f"{m['slug']}_{k['run_id']}_s{runs.session_number(k['session_index'])}_clip{c['idx']}_{c['kind']}_v{c['version']}"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw)


def caption_for(item, limit: int) -> str:
    """The caption is the one typed by the mosque, cut to what the platform accepts."""
    text = (item.caption or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def archive(item, folder: str | Path) -> Path:
    """Copy of the clip and its record in the publication folder; returns the video path.

    The local connector publishes it; Instagram needs it too, because the API fetches the video from
    a public address, which is that folder served over HTTP.
    """
    k, c, m = item.khutba, item.clip, item.mosque
    dest = Path(folder) / (k["date"] or "sans-date")
    dest.mkdir(parents=True, exist_ok=True)
    base = published_name(item)
    src = Path(c["file_path"])
    out = dest / f"{base}.mp4"
    shutil.copy2(src, out)
    meta = {
        "mosquee": m["name"], "khutba": k["label"], "date": k["date"], "run": k["run_id"],
        "clip": {"numero": c["idx"], "type": c["kind"], "version": c["version"], "debut_s": c["start"],
                 "fin_s": c["end"], "pourquoi": c["why"], "source": src.name},
        "legende": item.caption, "valide_par": item.validated_by, "publie_par": item.published_by,
        "publie_le": db.now_iso(), "musique": False, "sous_titres": "français seul",
    }
    (dest / f"{base}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def facebook(item, settings: dict) -> str:
    """Video on the mosque's page. The description is the caption; returns the post's address."""
    path = Path(item.clip["file_path"])
    with path.open("rb") as f:
        r = _ok(SESSION.post(
            f"{GRAPH_VIDEO}/{settings['page_id']}/videos",
            data={"description": caption_for(item, 5000), "access_token": settings["page_token"],
                  "published": "true"},
            files={"source": (path.name, f, "video/mp4")}, timeout=UPLOAD_TIMEOUT_S),
            "envoi de la vidéo à Facebook")
    d = r.json() if r.content else {}
    return d.get("permalink_url") or f"https://www.facebook.com/{d.get('id', '')}"


def google_token(settings: dict) -> str:
    """Access token from the refresh token the mosque generated once (OAuth, offline access)."""
    d = _json(SESSION.post(GOOGLE_TOKEN, data={
        "client_id": settings["client_id"], "client_secret": settings["client_secret"],
        "refresh_token": settings["refresh_token"], "grant_type": "refresh_token"}, timeout=30),
        "jeton Google")
    if not d.get("access_token"):
        raise PublishError("jeton Google : réponse sans access_token")
    return d["access_token"]


def youtube_title(item) -> str:
    """A title YouTube accepts: 100 characters, and the clip's own subject when it has one."""
    k, m = item.khutba, item.mosque
    kind = k.get("kind") or "khutba"
    head = f"{kind_label(kind)} : {k['title']}" if kind != "khutba" and k.get("title") else m["clip_title"]
    title = f"{head} — {m['name']}" + (f" ({k['date']})" if k.get("date") else "")
    return title if len(title) <= 100 else title[:99].rstrip() + "…"


def youtube(item, settings: dict) -> str:
    """Resumable upload to the mosque's channel; returns the short address."""
    token = google_token(settings)
    body = {
        "snippet": {"title": youtube_title(item), "description": caption_for(item, 4900),
                    "categoryId": settings.get("category_id") or "27", "defaultLanguage": "fr"},
        "status": {"privacyStatus": settings.get("privacy_status") or "public",
                   "selfDeclaredMadeForKids": False},
    }
    init = _ok(SESSION.post(f"{YOUTUBE_UPLOAD}?uploadType=resumable&part=snippet,status",
                            headers={"Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json; charset=UTF-8"},
                            data=json.dumps(body), timeout=60),
               "ouverture de l'envoi YouTube")
    where = init.headers.get("location") or init.headers.get("Location")
    if not where:
        raise PublishError("ouverture de l'envoi YouTube : adresse d'envoi absente de la réponse")
    with Path(item.clip["file_path"]).open("rb") as f:
        d = _json(SESSION.put(where, data=f, headers={"Content-Type": "video/mp4"},
                              timeout=UPLOAD_TIMEOUT_S),
                  "envoi de la vidéo à YouTube")
    if not d.get("id"):
        raise PublishError("envoi de la vidéo à YouTube : réponse sans identifiant")
    return f"https://youtu.be/{d['id']}"


def _wait_finished(creation_id: str, settings: dict) -> None:
    """Instagram transcodes asynchronously: the Reel is only publishable once FINISHED."""
    url = f"{GRAPH}/{creation_id}"
    for attempt in range(POLL_ATTEMPTS):
        d = _json(SESSION.get(url, params={"fields": "status_code,status",
                                           "access_token": settings["access_token"]}, timeout=30),
                  "état du Reel Instagram")
        status = d.get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram a refusé la vidéo : {d.get('status') or status}")
        if attempt + 1 < POLL_ATTEMPTS:
            SLEEP(POLL_DELAY_S)
    raise PublishError("Instagram n'a pas fini de préparer la vidéo (délai dépassé)")


def instagram(item, settings: dict) -> str:
    """Reel on the professional account.

    The API does not accept a file: it downloads the video from a public address. The clip is
    therefore archived in the publication folder first, and `public_base_url` is that folder as
    served by the mosque's website.
    """
    archive(item, settings.get("folder") or db.mosque_dirs(item.mosque)["publish"])
    base = (settings.get("public_base_url") or "").rstrip("/")
    k = item.khutba
    video_url = f"{base}/{k['date'] or 'sans-date'}/{published_name(item)}.mp4"
    d = _json(SESSION.post(f"{GRAPH}/{settings['ig_user_id']}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption_for(item, 2200),
        "share_to_feed": "true", "access_token": settings["access_token"]}, timeout=120),
        "création du Reel Instagram")
    creation = d.get("id")
    if not creation:
        raise PublishError("création du Reel Instagram : réponse sans identifiant")
    _wait_finished(creation, settings)
    pub = _json(SESSION.post(f"{GRAPH}/{settings['ig_user_id']}/media_publish",
                             data={"creation_id": creation, "access_token": settings["access_token"]},
                             timeout=60), "publication du Reel Instagram")
    return f"https://www.instagram.com/reel/{pub.get('id', '')}"
