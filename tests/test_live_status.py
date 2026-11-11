"""Live status: a powered-off orchestrator must not make every display wait."""
import time

import pytest

from sada.backoffice import live

MOSQUE = {"orchestrator_url": "http://127.0.0.1:9"}


@pytest.fixture(autouse=True)
def cache_vide(monkeypatch):
    """The cache is module state: we clear it around each test."""
    live._caches.clear()
    live._en_cours.clear()
    yield
    live._caches.clear()
    live._en_cours.clear()


def test_le_statut_est_servi_depuis_un_instantane(monkeypatch):
    """Two close displays do not fire a new network call."""
    appels = []
    monkeypatch.setattr(live, "_lire", lambda mosque, timeout=1.5: appels.append(1) or {"ok": False, "error": "x"})
    assert live.status_cached(MOSQUE)["ok"] is False
    assert live.status_cached(MOSQUE)["ok"] is False
    assert len(appels) == 1


def test_un_statut_perime_est_servi_puis_rafraichi(monkeypatch):
    """Once the delay has passed, return the old snapshot immediately and refresh behind."""
    reponses = [{"ok": True, "state": "idle"}, {"ok": True, "state": "live"}]
    appels = []
    monkeypatch.setattr(live, "_lire", lambda mosque, timeout=1.5: appels.append(1) or reponses[min(len(appels), 2) - 1])
    assert live.status_cached(MOSQUE, ttl=0)["state"] == "idle"      # first call, blocking
    assert live.status_cached(MOSQUE, ttl=0)["state"] == "idle"      # stale: old snapshot
    for _ in range(50):                                             # refresh in the background
        if live._caches[MOSQUE["orchestrator_url"]][1]["state"] == "live":
            break
        time.sleep(0.02)
    assert live._caches[MOSQUE["orchestrator_url"]][1]["state"] == "live"
    assert len(appels) == 2


def test_lecture_forcee_apres_une_commande(monkeypatch):
    """After a command, we want the real state, not the snapshot from ten seconds ago."""
    appels = []
    monkeypatch.setattr(live, "_lire", lambda mosque, timeout=1.5: appels.append(1) or {"ok": True})
    live.status_cached(MOSQUE)
    live.status_cached(MOSQUE, forcer=True)
    assert len(appels) == 2
