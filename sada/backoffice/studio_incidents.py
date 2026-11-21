"""Sanity checks of the studio: incidents seen by the server, and the e-mails that warn the operators.

Everything is computed on the server from what the device already says (the heartbeat) and from the
schedule; the device does nothing more. A periodic light job (`tick`, once a minute, a handful of indexed
queries per mosque that has a device, and one relay call only while a scheduled live is running) walks the
**checks**: each one is a small object that yields the problems it sees (Strategy). The result is compared
with the open incidents (`reconcile`): a new problem opens an incident, a problem that is gone resolves it.

Mails (`dispatch`) obey the anti-spam rules:

* one mail per open problem, plus at most `MAX_REMINDERS` reminders, `REMIND_S` apart;
* simultaneous problems are grouped in a single mail (each recipient gets one);
* a problem has to last a short `GRACE_S` before it is mailed (except a missed start and the pre-slot check),
  so a blink does not wake anyone;
* when the problems that were mailed are gone, one “resolved” mail follows;
* only the accounts of that mosque with the `live` right, an address, and who did not unsubscribe.

Without an SMTP server configured (`sada.mailer`), the incident is still opened, shown in the console and
written to the journal; the mail goes to the log (dry run).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import mailer
from . import auth, db, instance, live_watch, orm, studio_devices, studio_plan, studio_publisher, studio_token
from .models import Device, Membership, Mosque, StudioIncident, User

GRACE_S = 120
REMIND_S = 6 * 3600
MAX_REMINDERS = 2
START_GRACE_S = 180  # a scheduled live that is not on the relay this long after its start has failed
IMMEDIATE = {"preflight", "start_failed"}

# kind -> (title, what to do). French, for people: shown in the console and in the mails.
KINDS: dict[str, tuple[str, str]] = {
    "offline": (
        "L'appareil est hors ligne",
        "Vérifiez que le PC est allumé, que l'application Sada Studio est ouverte et que la connexion internet "
        "fonctionne. Si le PC a redémarré, il doit se rouvrir tout seul : sinon, contrôlez la connexion "
        "automatique de Windows.",
    ),
    "camera": (
        "La caméra n'est pas détectée",
        "Vérifiez que la caméra est branchée (câble USB, alimentation) et qu'aucun autre logiciel ne "
        "l'utilise. Débrancher puis rebrancher la caméra règle souvent le problème.",
    ),
    "microphone": (
        "Le micro n'est pas détecté",
        "Vérifiez que le micro ou la table de mixage est branché et allumé, et que son niveau n'est pas à zéro.",
    ),
    "health_auto_login": (
        "La connexion automatique de Windows n'est pas active",
        "Après une coupure de courant, le PC resterait sur l'écran de connexion et le direct ne démarrerait "
        "pas. Activez la connexion automatique de la session Windows qui lance Sada Studio.",
    ),
    "health_update_hours": (
        "Les heures d'activité de Windows Update ne couvrent pas les créneaux",
        "Windows pourrait redémarrer pendant un direct. Dans Windows Update, réglez les heures d'activité "
        "pour qu'elles couvrent les horaires de khutba et de dars.",
    ),
    "health_sleep": (
        "La mise en veille du PC n'est pas désactivée",
        "Le PC risque de s'endormir avant un direct. Dans les paramètres d'alimentation de Windows, réglez la "
        "mise en veille sur « Jamais » quand le PC est branché.",
    ),
    "preflight": (
        "Contrôle avant le direct : l'appareil n'est pas prêt",
        "Un direct est prévu bientôt et l'appareil est absent ou incomplet. Allez sur place si possible, ou "
        "demandez à quelqu'un d'allumer le PC et de vérifier la caméra et le micro.",
    ),
    "start_failed": (
        "Le direct prévu n'a pas démarré",
        "Le créneau a commencé mais aucun flux n'arrive. Sur le PC, vérifiez que l'application affiche bien "
        "« en direct ». À défaut, lancez le direct à la main depuis l'application ; en dernier recours, "
        "utilisez OBS avec la clé de diffusion de la mosquée.",
    ),
}


@dataclass(frozen=True)
class Problem:
    kind: str
    device_id: int | None
    slot_key: str = ""
    detail: str = ""

    def key(self, mosque_id: int) -> str:
        return f"{mosque_id}:{self.device_id or 0}:{self.kind}:{self.slot_key}"


@dataclass
class Eval:
    conn: object
    mosque: dict
    now: float
    devices: list[dict]
    cfg: dict
    order: studio_plan.Order
    slots: list


class Check(Protocol):
    def problems(self, e: Eval) -> list[Problem]: ...


def _created_ts(dev: dict) -> float:
    try:
        return dt.datetime.fromisoformat(dev["created_at"]).replace(tzinfo=dt.UTC).timestamp()
    except (ValueError, TypeError):
        return 0.0


class OfflineCheck:
    def problems(self, e: Eval) -> list[Problem]:
        out = []
        for d in e.devices:
            ref = d["last_seen_at"] if d["last_seen_at"] is not None else _created_ts(d)
            gap = e.now - ref
            if gap > e.cfg["offline_alert_min"] * 60:
                out.append(Problem("offline", d["id"], detail=f"{d['name']} : hors ligne depuis {_ago(gap)}"))
        return out


class MediaCheck:
    def problems(self, e: Eval) -> list[Problem]:
        out = []
        for d in e.devices:
            if not studio_devices.is_online(d, e.now):
                continue
            if d["camera_ok"] == 0:
                out.append(Problem("camera", d["id"], detail=f"{d['name']} : caméra absente"))
            if d["mic_ok"] == 0:
                out.append(Problem("microphone", d["id"], detail=f"{d['name']} : micro absent"))
        return out


class HostHealthCheck:
    FIELDS = {
        "auto_login": "health_auto_login",
        "update_hours": "health_update_hours",
        "sleep_disabled": "health_sleep",
    }

    def problems(self, e: Eval) -> list[Problem]:
        out = []
        for d in e.devices:
            if not studio_devices.is_online(d, e.now):
                continue
            h = studio_devices.health_of(d)
            for field_, kind in self.FIELDS.items():
                if h.get(field_) is False:  # only an explicit failure: a missing field is not a problem
                    out.append(Problem(kind, d["id"], detail=f"{d['name']} : {KINDS[kind][0].lower()}"))
        return out


class PreflightCheck:
    """Some minutes before a slot: no device online, or the ones online have no camera / no micro."""

    def problems(self, e: Eval) -> list[Problem]:
        nxt = e.order.next
        if not nxt or not 0 < nxt["starts_in_s"] <= e.cfg["preflight_min"] * 60:
            return []
        online = [d for d in e.devices if studio_devices.is_online(d, e.now)]
        complete = [d for d in online if d["camera_ok"] != 0 and d["mic_ok"] != 0]
        if complete:
            return []
        why = "aucun appareil en ligne" if not online else "caméra ou micro absent"
        return [Problem("preflight", None, nxt["slot_id"], f"{nxt['title']} à {_hhmm(nxt['starts_at'], e)} : {why}")]


class StartFailedCheck:
    """A scheduled live is ordered, its start is past, and the relay receives nothing."""

    def problems(self, e: Eval) -> list[Problem]:
        cur = e.order.current
        if e.order.source != "schedule" or not cur:
            return []
        slot = studio_plan.running_slot(e.order, e.slots)
        if slot is None or e.now < slot.starts.timestamp() + START_GRACE_S:
            return []
        reported_error = any(d["last_state"] == "error" for d in e.devices if studio_devices.is_online(d, e.now))
        info = live_watch.publisher_info(studio_publisher.path_of(e.mosque))
        if reported_error or (info is not None and not info["ready"]):
            return [Problem("start_failed", None, slot.key, f"{cur['title']} : aucun flux reçu")]
        return []


CHECKS: tuple[Check, ...] = (OfflineCheck(), MediaCheck(), HostHealthCheck(), PreflightCheck(), StartFailedCheck())


def _ago(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h"


def _hhmm(iso: str, e: Eval) -> str:
    when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(db.timezone(e.mosque))
    return when.strftime("%H:%M")


# ------------------------------------------------------------------ computing and recording


def evaluate(conn, mosque: dict, now: float, checks: tuple[Check, ...] = CHECKS) -> dict[str, Problem]:
    devices = orm.rows(conn, select(Device).where(Device.mosque_id == mosque["id"], Device.revoked_at.is_(None)))
    if not devices:
        return {}  # a mosque without device is not watched
    order, slots = studio_plan.current_order(conn, mosque, now)
    e = Eval(conn, mosque, now, devices, studio_plan.settings(conn, mosque["id"]), order, slots)
    found: dict[str, Problem] = {}
    for check in checks:
        for p in check.problems(e):
            found[p.key(mosque["id"])] = p
    return found


def open_incidents(conn, mosque_id: int) -> list[dict]:
    return orm.rows(
        conn,
        select(StudioIncident)
        .where(StudioIncident.mosque_id == mosque_id, StudioIncident.resolved_at.is_(None))
        .order_by(StudioIncident.opened_at, StudioIncident.id),
    )


def reconcile(conn, mosque: dict, found: dict[str, Problem], now: float) -> tuple[list[dict], list[dict]]:
    """Opens the new incidents and resolves the gone ones: (opened, resolved)."""
    mid = mosque["id"]
    opened, resolved = [], []
    current = {i["open_key"]: i for i in open_incidents(conn, mid)}
    for key, p in found.items():
        if key in current:
            if p.detail != current[key]["detail"]:
                conn.execute(
                    update(StudioIncident).where(StudioIncident.id == current[key]["id"]).values(detail=p.detail)
                )
                orm.done(conn)
            continue
        try:
            conn.add(
                StudioIncident(
                    mosque_id=mid, device_id=p.device_id, kind=p.kind, slot_key=p.slot_key, open_key=key,
                    detail=p.detail, opened_at=now,
                )
            )  # fmt: skip
            orm.done(conn)
        except IntegrityError:  # another replica opened it first
            conn.rollback()
            continue
        db.audit(conn, mid, None, "studio.incident.ouvert", "appareil", p.device_id, type=p.kind, detail=p.detail)
        opened.append(p)
    for key, inc in current.items():
        if key not in found:
            conn.execute(
                update(StudioIncident).where(StudioIncident.id == inc["id"]).values(resolved_at=now, open_key=None)
            )
            orm.done(conn)
            db.audit(conn, mid, None, "studio.incident.resolu", "appareil", inc["device_id"], type=inc["kind"])
            resolved.append(inc)
    return opened, resolved


# ------------------------------------------------------------------ recipients and mails


def recipients(conn, mosque_id: int) -> list[dict]:
    """Accounts of THIS mosque (home or membership) that manage the live, with an address, not unsubscribed."""
    ids = {mosque_id}
    member = set(conn.scalars(select(Membership.user_id).where(Membership.mosque_id == mosque_id)))
    users = orm.rows(
        conn,
        select(User).where(
            User.active == 1,
            User.email != "",
            User.studio_mail_optout == 0,
            (User.mosque_id.in_(ids)) | User.id.in_(member),
        ),
    )
    return [u for u in users if can_manage(u)]


def can_manage(user: dict) -> bool:
    """Same rule as the Devices page: the live right, as administrator or manager."""
    return not user.get("must_change_password") and auth.can(user, "live") and user["role"] in ("admin", "responsable")


def unsubscribe_token(user_id: int) -> str:
    return hmac.new(studio_token._secret(), f"studio-unsubscribe:{int(user_id)}".encode(), hashlib.sha256).hexdigest()[
        :32
    ]


def base_url() -> str:
    import os

    return instance.public_url() or os.environ.get("SADA_PUBLIC_URL", "").strip().rstrip("/")


def _bullets(items: list[dict]) -> str:
    return "\n".join(f"  - {KINDS[i['kind']][0]}" + (f" ({i['detail']})" if i["detail"] else "") for i in items)


def _steps(items: list[dict]) -> str:
    seen, out = set(), []
    for i in items:
        if i["kind"] not in seen:
            seen.add(i["kind"])
            out.append(f"* {KINDS[i['kind']][0]}\n  {KINDS[i['kind']][1]}")
    return "\n\n".join(out)


def compose(mosque: dict, user: dict, new: list[dict], already: list[dict], resolved: list[dict]) -> tuple[str, str]:
    """(subject, text) in French. `new`: to report; `already`: still open, reported before; `resolved`: back to normal."""
    base = base_url()
    page = f"{base}/appareils" if base else "la page « Appareils » de la console Sada"
    unsub = f"{base}/api/v1/studio/unsubscribe?u={user['id']}&t={unsubscribe_token(user['id'])}" if base else ""
    name = mosque["name"]
    who = user["display_name"] or user["username"]
    if resolved and not new:
        subject = f"[Sada] {name} : problème résolu"
        body = f"Bonjour {who},\n\nTout est revenu à la normale pour le studio de {name} :\n{_bullets(resolved)}\n"
        if already:
            body += f"\nIl reste ouvert :\n{_bullets(already)}\n"
    else:
        n = len(new) + len(already)
        subject = f"[Sada] {name} : {'1 problème' if n == 1 else f'{n} problèmes'} sur l'appareil Studio"
        body = f"Bonjour {who},\n\nLe contrôle automatique du studio de {name} a relevé :\n{_bullets(new)}\n"
        if already:
            body += f"\nDéjà signalés et toujours présents :\n{_bullets(already)}\n"
        body += f"\nQue faire :\n\n{_steps(new + already)}\n"
    body += (
        f"\nVoir l'état de l'appareil et de ses contrôles : {page}\n"
        "\n-- \nMessage automatique de Sada, envoyé aux responsables du direct de la mosquée.\n"
    )
    if unsub:
        body += f"Ne plus recevoir ces messages : {unsub}\n"
    else:
        body += "Pour ne plus recevoir ces messages : page « Appareils », rubrique « Mes alertes ».\n"
    return subject, body


def _send_group(conn, mosque, new, already, resolved, transport) -> int:
    sent = 0
    base = base_url()
    for u in recipients(conn, mosque["id"]):
        subject, text = compose(mosque, u, new, already, resolved)
        headers = {}
        if base:
            link = f"{base}/api/v1/studio/unsubscribe?u={u['id']}&t={unsubscribe_token(u['id'])}"
            headers = {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if mailer.send(mailer.Mail(u["email"], subject, text, headers), transport):
            sent += 1
    return sent


def dispatch(conn, mosque: dict, now: float, transport: mailer.Transport | None = None) -> dict:
    """Anti-spam mailing for one mosque. Returns {mails, new, reminders, resolved}."""
    mid = mosque["id"]
    stats = {"mails": 0, "new": 0, "reminders": 0, "resolved": 0}
    openi = open_incidents(conn, mid)
    due_new = [
        i for i in openi if i["mail_count"] == 0 and now - i["opened_at"] >= (0 if i["kind"] in IMMEDIATE else GRACE_S)
    ]
    due_rem = [
        i for i in openi
        if 1 <= i["mail_count"] <= MAX_REMINDERS and now - (i["mailed_at"] or 0) >= REMIND_S
    ]  # fmt: skip
    due = due_new + due_rem
    if due:
        due_ids = {i["id"] for i in due}
        already = [i for i in openi if i["id"] not in due_ids and i["mail_count"] >= 1]
        sent = _send_group(conn, mosque, due, already, [], transport)
        recips = len(recipients(conn, mid))
        if sent or recips == 0:  # nobody to write to: do not retry forever, the console shows it
            for i in due:
                conn.execute(
                    update(StudioIncident)
                    .where(StudioIncident.id == i["id"])
                    .values(mailed_at=now, mail_count=i["mail_count"] + 1)
                )
            orm.done(conn)
        db.audit(
            conn, mid, None,
            "studio.mail.envoye" if sent else ("studio.mail.aucun_destinataire" if not recips else "studio.mail.echec"),
            "mosque", mid, destinataires=sent, incidents=[i["kind"] for i in due], rappel=bool(due_rem and not due_new),
        )  # fmt: skip
        stats.update(mails=sent, new=len(due_new), reminders=len(due_rem))
    # resolved: one mail for the problems that had been reported, none for the ones nobody heard of
    done = orm.rows(
        conn,
        select(StudioIncident).where(
            StudioIncident.mosque_id == mid,
            StudioIncident.resolved_at.is_not(None),
            StudioIncident.resolved_mailed == 0,
        ),
    )
    if done:
        told = [i for i in done if i["mail_count"] >= 1]
        if told:
            still = [i for i in open_incidents(conn, mid) if i["mail_count"] >= 1]
            sent = _send_group(conn, mosque, [], still, told, transport)
            db.audit(
                conn, mid, None, "studio.mail.resolu" if sent else "studio.mail.echec", "mosque", mid,
                destinataires=sent, incidents=[i["kind"] for i in told],
            )  # fmt: skip
            if not sent and recipients(conn, mid):
                return stats  # retried at the next tick
            stats["mails"] += sent
            stats["resolved"] = len(told)
        conn.execute(
            update(StudioIncident).where(StudioIncident.id.in_([i["id"] for i in done])).values(resolved_mailed=1)
        )
        orm.done(conn)
    return stats


def tick(conn, now: float | None = None, transport: mailer.Transport | None = None) -> dict:
    """One pass over every mosque that has a device. Light: never touches a mosque without device."""
    now = time.time() if now is None else now
    total = {"mosques": 0, "opened": 0, "resolved": 0, "mails": 0}
    ids = set(conn.scalars(select(Device.mosque_id).where(Device.revoked_at.is_(None)).distinct()))
    ids |= set(conn.scalars(select(StudioIncident.mosque_id).where(StudioIncident.resolved_at.is_(None)).distinct()))
    for mosque in orm.rows(conn, select(Mosque).where(Mosque.id.in_(ids))) if ids else []:
        opened, resolved = reconcile(conn, mosque, evaluate(conn, mosque, now), now)
        stats = dispatch(conn, mosque, now, transport)
        total["mosques"] += 1
        total["opened"] += len(opened)
        total["resolved"] += len(resolved)
        total["mails"] += stats["mails"]
    return total


def public(inc: dict) -> dict:
    title, todo = KINDS.get(inc["kind"], (inc["kind"], ""))
    return {
        "id": inc["id"],
        "kind": inc["kind"],
        "title": title,
        "todo": todo,
        "detail": inc["detail"],
        "device_id": inc["device_id"],
        "opened_at": inc["opened_at"],
        "mailed": inc["mail_count"] >= 1,
    }
