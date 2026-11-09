"""Notifications à l'imam : webhook générique ou Telegram.

Rien n'est publié : ces messages partent vers un canal privé choisi par la mosquée (webhook
Discord/Slack/n8n, ou bot Telegram). Sans destination configurée, les notifications s'affichent
dans le journal (« à blanc ») et le reste du direct n'est pas affecté.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Callable, Optional

from .state import Machine, State

# états qui méritent un message (les allers-retours ARME/IDLE sont du bruit)
NOTIFY_STATES = {State.ARME, State.KHUTBA, State.DARS, State.FIN}


def message_for(state: State, m: Machine) -> Optional[str]:
    """Texte à envoyer à l'imam pour ce changement d'état ; None si rien à dire."""
    ref = m.session.strftime("%H:%M") if m.session else "—"
    ev = m.rec
    quoi = "le dars" if m.kind == "dars" else "la khutba"
    if state == State.ARME:
        return f"Rappel : {quoi} de {ref} approche, le direct se prépare."
    if state == State.KHUTBA:
        return f"Le direct a commencé ({quoi} de {ref})."
    if state == State.DARS:
        titre = f" — {ev.title}" if ev is not None and ev.title else ""
        return f"Le direct a commencé ({quoi} de {ref}{titre})."
    if state == State.FIN:
        fin = m.rec.end_reason if m.rec is not None else None
        return f"Le direct est terminé{f' ({fin})' if fin else ''}."
    return None


def _http_post(url: str, payload: dict) -> None:      # pragma: no cover (réseau)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read(1)


class Notifier:
    """Envoie un message court à une destination privée. `post` est injectable pour les tests."""

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
        """Envoie le message ; renvoie False si l'envoi a échoué (jamais d'exception vers le direct)."""
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
        except Exception as exc:                       # réseau, DNS, quota : on ne bloque rien
            self.log(f"notification non envoyée ({self.destination}) : {exc}")
            return False

    def notify_state(self, state: State, m: Machine) -> bool:
        text = message_for(state, m)
        return bool(text) and self.send(text)


def from_config(cfg, log: Callable[[str], None] = print) -> Notifier:
    """Notifier réglé par la configuration (webhook ou Telegram, jamais dans le dépôt)."""
    return Notifier(getattr(cfg, "notify_url", ""), getattr(cfg, "telegram_token", ""),
                    getattr(cfg, "telegram_chat", ""), log=log)
