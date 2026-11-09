"""Notifications à l'imam : messages, destinations, échec réseau sans conséquence sur le direct."""
import datetime as dt

from minbar.notify import Notifier, message_for
from minbar.state import Machine, MachineParams, State

D = dt.date(2026, 11, 10)
META = {"kind": "dars", "title": "Tafsir Al-Kahf", "speaker": "Imam Abdallah"}


def mk(kind="khutba", meta=None):
    return Machine([dt.datetime.combine(D, dt.time(19, 0))], MachineParams(),
                   kinds=[kind], meta=[meta if meta is not None else {}])


def test_message_par_etat():
    m = mk()
    assert "khutba de 19:00" in message_for(State.ARME, m)
    assert "a commencé" in message_for(State.KHUTBA, m)
    assert "terminé" in message_for(State.FIN, m)
    assert message_for(State.AVANT, m) is None          # pas de bruit


def test_message_dars_porte_le_titre():
    m = mk("dars", META)
    txt = message_for(State.DARS, m)
    assert "dars" in txt and "Tafsir Al-Kahf" in txt


def test_sans_destination_tout_part_au_journal():
    logs = []
    n = Notifier(log=logs.append)
    assert n.enabled is False and n.destination == "à blanc"
    assert n.send("bonjour") is False
    assert any("à blanc" in l and "bonjour" in l for l in logs)


def test_webhook_envoie_le_texte():
    calls = []
    n = Notifier(webhook="https://exemple.test/hook", post=lambda u, p: calls.append((u, p)))
    assert n.enabled and n.destination == "webhook"
    assert n.send("la khutba commence") is True
    assert calls == [("https://exemple.test/hook", {"text": "la khutba commence"})]


def test_telegram_utilise_le_jeton_et_la_conversation():
    calls = []
    n = Notifier(telegram_token="T", telegram_chat="42", post=lambda u, p: calls.append((u, p)))
    assert n.destination == "telegram"
    n.send("coucou")
    url, payload = calls[0]
    assert url.endswith("/botT/sendMessage") and payload["chat_id"] == "42"


def test_echec_reseau_ne_leve_pas():
    logs = []

    def boom(u, p):
        raise OSError("réseau coupé")

    n = Notifier(webhook="https://exemple.test/hook", log=logs.append, post=boom)
    assert n.send("x") is False
    assert any("notification non envoyée" in l for l in logs)


def test_notify_state_ignore_les_etats_sans_message():
    sent = []
    n = Notifier(webhook="https://exemple.test/hook", post=lambda u, p: sent.append(p["text"]))
    m = mk()
    assert n.notify_state(State.AVANT, m) is False
    assert n.notify_state(State.ARME, m) is True
    assert len(sent) == 1
