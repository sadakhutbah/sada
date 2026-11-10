"""Live commands: all those of the state machine are exposed, with validated parameters."""
import pytest

from minbar.backoffice import live
from minbar.state import STATE_OF_SCENE, Machine

MOSQUE = {"orchestrator_url": "http://127.0.0.1:9"}


@pytest.fixture
def appels(monkeypatch):
    """Replaces the HTTP call to the orchestrator and keeps the requests sent."""
    vus = []
    monkeypatch.setattr(live, "_call", lambda mosque, methode, chemin, params=None, timeout=3.0:
                        vus.append((methode, chemin, params)) or {"ok": True})
    return vus


def test_toutes_les_commandes_de_la_machine_sont_exposees():
    assert set(live.COMMANDS) == set(Machine.COMMANDS)


def test_les_scenes_proposees_existent_dans_la_machine():
    valeurs = {v for v, _ in live.COMMANDS["scene"].choices}
    assert valeurs == set(STATE_OF_SCENE) - {"janaza"}


@pytest.mark.parametrize("cmd", ["start", "stop", "pause", "resume", "auto", "skip", "cancel"])
def test_commande_simple(appels, cmd):
    live.command(MOSQUE, cmd)
    assert appels == [("POST", cmd, None)]


def test_extend_5_par_defaut_et_15(appels):
    live.command(MOSQUE, "extend")
    live.command(MOSQUE, "extend", valeur="15")
    assert [a[2] for a in appels] == [{"min": "5"}, {"min": "15"}]


def test_extend_refuse_une_duree_libre(appels):
    with pytest.raises(ValueError):
        live.command(MOSQUE, "extend", valeur="9999")
    assert appels == []


def test_scene_valide_et_refus(appels):
    live.command(MOSQUE, "scene", valeur="priere")
    assert appels == [("POST", "scene", {"name": "priere"})]
    for mauvaise in (None, "janaza", "../stop"):
        with pytest.raises(ValueError):
            live.command(MOSQUE, "scene", valeur=mauvaise)
    assert len(appels) == 1


def test_janaza_fiche(appels):
    live.command(MOSQUE, "janaza", "Nom", "67 ans")
    live.command(MOSQUE, "janaza", None, "mention seule")
    assert appels[0][2] == {"name": "Nom", "info": "67 ans"}
    assert appels[1][2] is None


def test_commande_inconnue():
    with pytest.raises(live.OrchestratorError):
        live.command(MOSQUE, "rm")


def test_libelle_de_variante():
    assert live.choice_label("extend", "15") == "+15 min"
    assert live.choice_label("stop", None) == "Arrêter"
