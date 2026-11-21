"""Sending e-mail (SMTP), the one place that knows how.

Sada had no e-mail before the studio checks. The transport is a small Strategy: `SmtpTransport` talks to the
SMTP server the installation is given through the environment, `DryRunTransport` (used while nothing is
configured) writes the message to the log, like the notifier does without a destination. Tests inject their own.

Environment (the password is only ever read from here, never stored or shown):

* ``SADA_SMTP_HOST``      server name; empty = nothing is sent (dry run)
* ``SADA_SMTP_PORT``      default 587 (465 for ``ssl``)
* ``SADA_SMTP_SECURITY``  ``starttls`` (default), ``ssl`` or ``none``
* ``SADA_SMTP_USER`` / ``SADA_SMTP_PASSWORD``  login, optional
* ``SADA_MAIL_FROM``      sender address (default: the login)
* ``SADA_SMTP_TIMEOUT``   seconds, default 10
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

log = logging.getLogger("sada.mailer")


@dataclass
class Mail:
    to: str
    subject: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def send(self, mail: Mail) -> None: ...


def sender() -> str:
    return (
        os.environ.get("SADA_MAIL_FROM", "").strip() or os.environ.get("SADA_SMTP_USER", "").strip() or "sada@localhost"
    )


def build(mail: Mail) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender(), mail.to, mail.subject
    m["Date"], m["Message-ID"] = formatdate(localtime=False), make_msgid(domain="sada")
    for k, v in mail.headers.items():
        m[k] = v
    m.set_content(mail.text)
    return m


class SmtpTransport:
    def __init__(self, host: str, port: int, security: str, user: str, password: str, timeout: float):
        self.host, self.port, self.security = host, port, security
        self.user, self.password, self.timeout = user, password, timeout

    def send(self, mail: Mail) -> None:
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            cm = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ctx)
        else:
            cm = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        with cm as smtp:
            if self.security == "starttls":
                smtp.starttls(context=ctx)
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(build(mail))


class DryRunTransport:
    def __init__(self):
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> None:
        self.sent.append(mail)
        log.info("mail (dry run) to %s: %s", mail.to, mail.subject)


def from_env() -> Transport:
    host = os.environ.get("SADA_SMTP_HOST", "").strip()
    if not host:
        return DryRunTransport()
    security = os.environ.get("SADA_SMTP_SECURITY", "starttls").strip().lower()
    if security not in ("starttls", "ssl", "none"):
        security = "starttls"
    try:
        port = int(os.environ.get("SADA_SMTP_PORT", "") or (465 if security == "ssl" else 587))
        timeout = float(os.environ.get("SADA_SMTP_TIMEOUT", "") or 10)
    except ValueError:
        port, timeout = 587, 10.0
    return SmtpTransport(
        host, port, security, os.environ.get("SADA_SMTP_USER", ""), os.environ.get("SADA_SMTP_PASSWORD", ""), timeout
    )


def configured() -> bool:
    return bool(os.environ.get("SADA_SMTP_HOST", "").strip())


def send(mail: Mail, transport: Transport | None = None) -> bool:
    """True when the message was handed to the server. Never raises: a mail problem must not touch the live."""
    try:
        (transport or from_env()).send(mail)
        return True
    except Exception as e:  # noqa: BLE001 — network, TLS, login, refused recipient
        log.warning("mail not sent to %s: %s", mail.to, e)
        return False
