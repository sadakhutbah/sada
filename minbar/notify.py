"""Notifications to the imam: generic webhook or Telegram.

Nothing is published: these messages go to a private channel chosen by the mosque (webhook
Discord/Slack/n8n, or Telegram bot). Without a configured destination, notifications are printed
in the log ("dry run") and the rest of the live is unaffected.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .state import Machine, State

# states that deserve a message (ARME/IDLE round trips are noise)
NOTIFY_STATES = {State.ARME, State.KHUTBA, State.DARS, State.FIN}
# wording of a session type in a sentence
KIND_NOUNS = {"khutba": "la khutba", "dars": "le dars", "conference": "la conférence"}


def should_send(state: State, m: Machine, now: dt.datetime) -> bool:
    """Should this state transition be notified? A reminder only concerns a session still to come."""
    if state not in NOTIFY_STATES:
        return False
    return state is not State.ARME or (m.session is not None and m.session > now)


def message_for(state: State, m: Machine) -> Optional[str]:
    """Text to send to the imam for this state change; None if nothing to say."""
    ref = m.session.strftime("%H:%M") if m.session else "—"
    quoi = KIND_NOUNS.get(m.kind, f"le {m.kind}")
    titre = f" · {m.rec.title}" if m.rec is not None and m.rec.title else ""
    if state == State.ARME:
        return f"Rappel : {quoi} de {ref} approche, le direct se prépare."
    if state in (State.KHUTBA, State.DARS):
        return f"Le direct a commencé ({quoi} de {ref}{titre})."
    if state == State.FIN:
        fin = m.rec.end_reason if m.rec is not None else None
        return f"Le direct est terminé{f' ({fin})' if fin else ''}."
    return None


def _http_post(url: str, payload: dict) -> None:      # pragma: no cover (network)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read(1)


class Notifier:
    """Sends a short message to a private destination. `post` is injectable for tests."""

    def __init__(self, webhook: str = "", telegram_token: str = "", telegram_chat: str = "",
                 log: Callable[[str], None] = print, post: Callable[[str, dict], None] | None = None):
        self.webhook = (webhook or "").strip()
        self.telegram_token = (telegram_token or "").strip()
        self.telegram_chat = (telegram_chat or "").strip()
        self.log = log
        self.post = post or _http_post
        self.sent: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.webhook or (self.telegram_token and self.telegram_chat))

    @property
    def destination(self) -> str:
        if self.webhook:
            return "webhook"
        if self.telegram_token and self.telegram_chat:
            return "telegram"
        return "à blanc"

    def send(self, text: str) -> bool:
        """Sends the message; returns False if sending failed (never raises into the live)."""
        self.sent.append(text)
        if not self.enabled:
            self.log(f"notification (à blanc) : {text}")
            return False
        try:
            if self.webhook:
                self.post(self.webhook, {"text": text})
            else:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                self.post(url, {"chat_id": self.telegram_chat, "text": text,
                                "disable_notification": False})
            return True
        except Exception as exc:                       # network, DNS, quota: we block nothing
            self.log(f"notification non envoyée ({self.destination}) : {exc}")
            return False

    def notify_state(self, state: State, m: Machine) -> bool:
        text = message_for(state, m)
        return bool(text) and self.send(text)


def from_config(cfg, log: Callable[[str], None] = print, db_path=None) -> Notifier:
    """Build the notifier from the configuration (webhook or Telegram, never stored in the repository).

    The orchestrator configuration takes precedence; without a destination, fall back to the one an
    administrator entered in the back-office (Publications screen), read once at startup.
    """
    n = Notifier(getattr(cfg, "notify_url", ""), getattr(cfg, "telegram_token", ""),
                 getattr(cfg, "telegram_chat", ""), log=log)
    if n.enabled:
        return n
    row = _backoffice_row(getattr(cfg, "api_port", None), db_path)
    return from_settings(row, log=log) if row else n


def _backoffice_row(api_port, db_path=None) -> dict | None:
    """Notification settings of the mosque whose orchestrator listens on this port (or of the only mosque)."""
    import sqlite3
    from urllib.parse import urlparse

    from . import paths

    path = db_path or paths.BACKOFFICE_DB
    try:
        if not path or not Path(path).exists():
            return None
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            mosques = conn.execute("SELECT id, orchestrator_url FROM mosques").fetchall()
            ids = [m["id"] for m in mosques if urlparse(m["orchestrator_url"] or "").port == api_port]
            if not ids and len(mosques) == 1:
                ids = [mosques[0]["id"]]
            if len(ids) != 1:
                return None
            r = conn.execute("SELECT * FROM notifications WHERE mosque_id = ?", (ids[0],)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()
    except Exception:                                   # missing, locked or older database: the live stream must never be affected
        return None


def from_settings(row: dict | None, log: Callable[[str], None] = print) -> Notifier:
    """Build a notifier from the Publications screen settings (back-office `notifications` table).

    Only the chosen channel (`mode`) is used; with no settings the notifier is a no-op ("à blanc").
    """
    row = row or {}
    if row.get("mode") == "webhook":
        return Notifier(webhook=row.get("webhook", ""), log=log)
    if row.get("mode") == "telegram":
        return Notifier(telegram_token=row.get("telegram_token", ""), telegram_chat=row.get("telegram_chat", ""), log=log)
    return Notifier(log=log)
