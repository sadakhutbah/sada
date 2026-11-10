"""Statut du live : un orchestrateur éteint ne doit pas faire attendre chaque affichage."""
import time

import pytest

from minbar.backoffice import live

MOSQUE = {"orchestrator_url": "http://127.0.0.1:9"}


@pytest.fixture(autouse=True)
def cache_vide(monkeypatch):
    """Le cache est un état de module : on le vide autour de chaque test."""
    live._caches.clear()
    live._en_cours.clear()
    yield
    live._caches.clear()
    live._en_cours.clear()


def test_le_statut_est_servi_depuis_un_instantane(monkeypatch):
    """Deux affichages rapprochés ne relancent pas d'appel réseau."""
    appels = []
    monkeypatch.setattr(live, "_lire", lambda mosque, timeout=1.5: appels.append(1) or {"ok": False, "error": "x"})
    assert live.status_cached(MOSQUE)["ok"] is False
    assert live.status_cached(MOSQUE)["ok"] is False
    assert len(appels) == 1


def test_un_statut_perime_est_servi_puis_rafraichi(monkeypatch):
    """Passé le délai, on rend l'ancien instantané tout de suite et on rafraîchit derrière."""
    reponses = [{"ok": True, "state": "idle"}, {"ok": True, "state": "live"}]
    appels = []
    monkeypatch.setattr(live, "_lire", lambda mosque, timeout=1.5: appels.append(1) or reponses[min(len(appels), 2) - 1])
    assert live.status_cached(MOSQUE, ttl=0)["state"] == "idle"      # premier appel, bloquant
    assert live.status_cached(MOSQUE, ttl=0)["state"] == "idle"      # périmé : ancien instantané
    for _ in range(50):                                             # rafraîchissement en arrière-plan
        if live._caches[MOSQUE["orchestrator_url"]][1]["state"] == "live":
            break
        time.sleep(0.02)
    assert live._caches[MOSQUE["orchestrator_url"]][1]["state"] == "live"
    assert len(appels) == 2


def test_lecture_forcee_apres_une_commande(monkeypatch):
    """Après une commande, on veut l'état réel, pas l'instantané d'il y a dix secondes."""
    appels = []
    monkeypatch.setattr(live, "_lire", lambda mosque, timeout=1.5: appels.append(1) or {"ok": True})
    live.status_cached(MOSQUE)
    live.status_cached(MOSQUE, forcer=True)
    assert len(appels) == 2
