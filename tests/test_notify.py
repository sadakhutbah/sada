"""Notifications to the imam: messages, destinations, network failure with no consequence on the live."""

import datetime as dt

from sada.notify import CHANNELS, Notifier, message_for, should_send
from sada.state import Machine, MachineParams, State

D = dt.date(2026, 11, 10)
META = {"kind": "dars", "title": "Tafsir Al-Kahf", "speaker": "Imam Abdallah"}


def mk(kind="khutba", meta=None):
    extra = {"kind": kind, **(meta or {})}
    return Machine([dt.datetime.combine(D, dt.time(19, 0))], MachineParams(), extras=[extra])


def test_message_par_etat():
    m = mk()
    assert "the khutba at 19:00" in message_for(State.ARMED, m)
    assert "has started" in message_for(State.KHUTBA, m)
    assert "is over" in message_for(State.STOPPED, m)
    assert message_for(State.BEFORE, m) is None  # no noise


def test_message_dars_porte_le_titre():
    m = mk("dars", META)
    txt = message_for(State.DARS, m)
    assert "dars" in txt and "Tafsir Al-Kahf" in txt


def test_sans_destination_tout_part_au_journal():
    logs = []
    n = Notifier(log=logs.append)
    assert n.enabled is False and n.destination == "à blanc"
    assert n.send("bonjour") is False
    assert any("dry run" in ligne and "bonjour" in ligne for ligne in logs)


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
    assert any("notification not sent" in ligne for ligne in logs)


def test_notify_state_ignore_les_etats_sans_message():
    sent = []
    n = Notifier(webhook="https://exemple.test/hook", post=lambda u, p: sent.append(p["text"]))
    m = mk()
    assert n.notify_state(State.BEFORE, m) is False
    assert n.notify_state(State.ARMED, m) is True
    assert len(sent) == 1


def test_rappel_seulement_pour_une_session_a_venir():
    m = mk()
    assert should_send(State.ARMED, m, dt.datetime(2026, 11, 10, 18, 50)) is True
    assert should_send(State.ARMED, m, dt.datetime(2026, 11, 10, 19, 5)) is False  # already past: no reminder
    assert should_send(State.BEFORE, m, dt.datetime(2026, 11, 10, 18, 50)) is False
    assert should_send(State.DARS, m, dt.datetime(2026, 11, 10, 19, 5)) is True


def test_channel_registry_keys_and_order():
    from sada.notify import TelegramChannel, WebhookChannel

    assert list(CHANNELS) == ["webhook", "telegram"]
    assert isinstance(CHANNELS["webhook"], WebhookChannel)
    assert isinstance(CHANNELS["telegram"], TelegramChannel)
    assert all(key == ch.key for key, ch in CHANNELS.items())


def test_channel_requirements_and_requests():
    web, tg = CHANNELS["webhook"], CHANNELS["telegram"]
    assert web.is_configured({"webhook": "https://x.test"}) and not web.is_configured({})
    assert tg.is_configured({"telegram_token": "t", "telegram_chat": "c"})
    assert not tg.is_configured({"telegram_token": "t"})
    assert web.request({"webhook": "https://x.test"}, "hi") == ("https://x.test", {"text": "hi"})
    url, payload = tg.request({"telegram_token": "t", "telegram_chat": "c"}, "hi")
    assert url == "https://api.telegram.org/bott/sendMessage"
    assert payload == {"chat_id": "c", "text": "hi", "disable_notification": False}
    assert web.missing_message() and tg.missing_message()


def test_notifier_delegates_to_channel_and_webhook_wins():
    n = Notifier(webhook="https://x.test", telegram_token="t", telegram_chat="c")
    assert n.channel is CHANNELS["webhook"]
    assert n.destination == "webhook"
    assert Notifier().channel is None and Notifier().destination == "à blanc"


def test_from_settings_unknown_mode_is_noop():
    from sada.notify import from_settings

    assert from_settings({"mode": "sms", "webhook": "https://x.test"}).enabled is False
    assert from_settings({"mode": "telegram", "webhook": "https://x.test"}).enabled is False
