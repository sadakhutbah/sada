"""Notifications to the imam: generic webhook or Telegram.

Nothing is published: these messages go to a private channel chosen by the mosque (webhook
Discord/Slack/n8n, or Telegram bot). Without a configured destination, notifications are printed
in the log ("dry run") and the rest of the live is unaffected.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request
from collections.abc import Callable

from .i18n import _, translate
from .state import Machine, State

# states that deserve a message (ARMED/IDLE round trips are noise)
NOTIFY_STATES = {State.ARMED, State.KHUTBA, State.DARS, State.TARAWIH, State.STOPPED}
# wording of a session type in a sentence (translated where the sentence is built)
KIND_NOUNS = {"khutba": "the khutba", "dars": "the dars", "conference": "the conference", "tarawih": "the tarawih"}


def should_send(state: State, m: Machine, now: dt.datetime) -> bool:
    """Should this state transition be notified? A reminder only concerns a session still to come."""
    if state not in NOTIFY_STATES:
        return False
    return state is not State.ARMED or (m.session is not None and m.session > now)


def message_for(state: State, m: Machine) -> str | None:
    """Text to send to the imam for this state change; None if nothing to say."""
    ref = m.session.strftime("%H:%M") if m.session else "—"
    quoi = translate(KIND_NOUNS.get(m.kind, "the {kind}"), kind=m.kind)
    titre = f" · {m.rec.title}" if m.rec is not None and m.rec.title else ""
    if state == State.ARMED:
        return _("Reminder: {what} at {time} is approaching; the live stream is being prepared.", what=quoi, time=ref)
    if state in (State.KHUTBA, State.DARS, State.TARAWIH):
        return _("The live stream has started ({what} at {time}{title}).", what=quoi, time=ref, title=titre)
    if state == State.STOPPED:
        fin = m.rec.end_reason if m.rec is not None else None
        return _("The live stream is over{end}.", end=f" ({fin})" if fin else "")
    return None


def _http_post(url: str, payload: dict) -> None:  # pragma: no cover (network)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read(1)


class Channel:
    """A notification destination. Subclasses declare their settings and how to post a message."""

    key: str = ""
    fields: tuple[str, ...] = ()  # Notifier/settings attribute names this channel reads

    def is_configured(self, values: dict) -> bool:
        raise NotImplementedError

    def missing_message(self) -> str:
        """Translated error shown when the channel is chosen but its required fields are empty."""
        raise NotImplementedError

    def request(self, values: dict, text: str) -> tuple[str, dict]:
        """(url, JSON payload) to post for this message."""
        raise NotImplementedError


class WebhookChannel(Channel):
    key = "webhook"
    fields = ("webhook",)

    def is_configured(self, values: dict) -> bool:
        return bool(values.get("webhook"))

    def missing_message(self) -> str:
        return _("Enter the webhook address.")

    def request(self, values: dict, text: str) -> tuple[str, dict]:
        return values["webhook"], {"text": text}


class TelegramChannel(Channel):
    key = "telegram"
    fields = ("telegram_token", "telegram_chat")

    def is_configured(self, values: dict) -> bool:
        return bool(values.get("telegram_token") and values.get("telegram_chat"))

    def missing_message(self) -> str:
        return _("Telegram requires the bot token and the chat identifier.")

    def request(self, values: dict, text: str) -> tuple[str, dict]:
        url = f"https://api.telegram.org/bot{values['telegram_token']}/sendMessage"
        return url, {"chat_id": values["telegram_chat"], "text": text, "disable_notification": False}


# registry by key; order is the precedence when several channels are configured (webhook first)
CHANNELS: dict[str, Channel] = {c.key: c for c in (WebhookChannel(), TelegramChannel())}


class Notifier:
    """Sends a short message to a private destination. `post` is injectable for tests."""

    def __init__(
        self,
        webhook: str = "",
        telegram_token: str = "",
        telegram_chat: str = "",
        log: Callable[[str], None] = print,
        post: Callable[[str, dict], None] | None = None,
    ):
        self.webhook = (webhook or "").strip()
        self.telegram_token = (telegram_token or "").strip()
        self.telegram_chat = (telegram_chat or "").strip()
        self.log = log
        self.post = post or _http_post
        self.sent: list[str] = []

    @property
    def values(self) -> dict:
        return {"webhook": self.webhook, "telegram_token": self.telegram_token, "telegram_chat": self.telegram_chat}

    @property
    def channel(self) -> Channel | None:
        """The channel in use: the first configured one in registry order."""
        values = self.values
        return next((c for c in CHANNELS.values() if c.is_configured(values)), None)

    @property
    def enabled(self) -> bool:
        return self.channel is not None

    @property
    def destination(self) -> str:
        ch = self.channel
        return ch.key if ch else "à blanc"

    def send(self, text: str) -> bool:
        """Sends the message; returns False if sending failed (never raises into the live)."""
        self.sent.append(text)
        ch = self.channel
        if ch is None:
            self.log(_("notification (dry run): {message}", message=text))
            return False
        try:
            url, payload = ch.request(self.values, text)
            self.post(url, payload)
            return True
        except Exception as exc:  # network, DNS, quota: we block nothing
            self.log(_("notification not sent ({destination}): {error}", destination=self.destination, error=exc))
            return False

    def notify_state(self, state: State, m: Machine) -> bool:
        text = message_for(state, m)
        return bool(text) and self.send(text)


def from_config(cfg, log: Callable[[str], None] = print, fetch: Callable[[str, str], dict] | None = None) -> Notifier:
    """Build the notifier from the configuration (webhook or Telegram, never stored in the repository).

    The orchestrator configuration takes precedence; without a destination, ask the back-office for
    the settings an administrator saved (Publications screen), once at startup.
    """
    n = Notifier(
        getattr(cfg, "notify_url", ""), getattr(cfg, "telegram_token", ""), getattr(cfg, "telegram_chat", ""), log=log
    )
    if n.enabled:
        return n
    row = _backoffice_settings(getattr(cfg, "backoffice_url", ""), getattr(cfg, "api_port", None), fetch=fetch)
    return from_settings(row, log=log) if row else n


def _backoffice_settings(backoffice_url: str, api_port, fetch: Callable[[str, str], dict] | None = None) -> dict | None:
    """Notification settings saved in the interface, asked over HTTP to the back-office.

    Authenticated by the local token (`sada.backoffice.internal`): the orchestrator never opens the
    back-office database. Any failure means "no destination": the live stream must never be affected.
    """
    if not backoffice_url:
        return None
    from urllib.parse import urlencode

    from .backoffice.internal import token

    url = backoffice_url.rstrip("/") + "/api/v1/internal/notification-settings?" + urlencode({"port": api_port or 0})
    try:
        return (fetch or _http_get)(url, token()) or None
    except Exception:
        return None


def _http_get(url: str, token: str) -> dict:  # pragma: no cover (network)
    req = urllib.request.Request(url, headers={"X-Internal-Token": token})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read(4096))


def from_settings(row: dict | None, log: Callable[[str], None] = print) -> Notifier:
    """Build a notifier from the Publications screen settings (back-office `notifications` table).

    Only the chosen channel (`mode`) is used; with no settings the notifier is a no-op ("à blanc").
    """
    row = row or {}
    ch = CHANNELS.get(row.get("mode"))
    if ch is None:
        return Notifier(log=log)
    return Notifier(**{f: row.get(f, "") for f in ch.fields}, log=log)
