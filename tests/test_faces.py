"""Face at the minbar: pure decision (no camera) and effect on the state machine."""
import datetime as dt

import numpy as np

from minbar.faces import FaceGate
from minbar.state import Machine, MachineParams, Signals, State

W, H = 40, 20


def image() -> np.ndarray:
    return np.zeros((H, W, 3), dtype=np.uint8)


def detector(scores):
    """Fake detector: returns the next score from the list on each call."""
    it = iter(scores)

    def detect(zone, _roi=None):
        s = next(it, 0.0)
        return [(0, 0, 5, 5, s)] if s else []

    return detect


def test_premiere_image_sans_visage_ne_dit_rien():
    g = FaceGate(detect=detector([0.0]))
    assert g.update(image(), now=0.0) is False
    assert g.absence_run(0.0) is None          # never seen: no absence duration


def test_visage_detecte_puis_perdu():
    g = FaceGate(detect=detector([0.9, 0.0]), threshold=0.6, hold_s=4.0)
    assert g.update(image(), now=0.0) is True
    assert g.update(image(), now=1.0) is True   # no longer seen, but within the hold
    assert g.present(3.9) is True
    assert g.present(4.1) is False
    assert g.absence_run(4.1) == 4.1            # since the last image with a face


def test_score_sous_le_seuil_ignore():
    g = FaceGate(detect=detector([0.4]), threshold=0.6)
    assert g.update(image(), now=0.0) is False
    assert g.last_score == 0.4


def test_zone_du_minbar_restreinte():
    seen = {}

    def detect(zone, _roi=None):
        seen["shape"] = zone.shape
        return []

    g = FaceGate(detect=detect, roi=(0.0, 0.0, 0.5, 0.5))
    g.update(image(), now=0.0)
    assert seen["shape"][:2] == (H // 2, W // 2)


def test_zone_invalide_refusee():
    g = FaceGate(detect=detector([]), roi=(0.0, 0.0, 1.0))
    try:
        g.update(image(), now=0.0)
    except ValueError:
        return
    raise AssertionError("une zone à 3 fractions doit être refusée")


# ---------------------------------------------------------------- state machine
D = dt.date(2026, 11, 6)


def at(h, m):
    return dt.datetime.combine(D, dt.time(h, m))


class Pilot:
    """Advances the mosque clock and real time together (as the orchestrator does)."""

    def __init__(self, m, now):
        self.m, self.now, self.t = m, now, 0.0

    def step(self, dsec=1.0, voice=0.0, clock=None):
        self.t += dsec
        self.now += dt.timedelta(seconds=dsec if clock is None else clock)
        self.m.tick(Signals(self.now, self.t, voice_run=voice, silence_run=0.0))
        return self.m.state


def khutba(**params):
    m = Machine([at(12, 30)], MachineParams(**params))
    p = Pilot(m, at(12, 15))
    for i in range(1000):                       # 12:15 -> 12:30, continuous voice
        if p.step(1.0, voice=float(i + 1)) is State.KHUTBA:
            return m, p
    raise AssertionError("la khutba n'a pas démarré")


def test_fin_de_khutba_par_visage_perdu():
    m, p = khutba()
    # noisy room: silence stays below the threshold, but no face looking forward anymore
    for i in range(200):
        p.t += 1.0
        p.m.tick(Signals(p.now, p.t, voice_run=0.0, silence_run=40.0, face_run=60.0 + i))
    assert m.state is State.PRIERE


def test_visage_de_retour_annule_la_grace():
    m, p = khutba()
    for i in range(5):
        p.t += 1.0
        p.m.tick(Signals(p.now, p.t, voice_run=0.0, silence_run=40.0, face_run=60.0 + i))
    assert m.state is State.KHUTBA          # grace period in progress
    p.m.tick(Signals(p.now, p.t + 1, voice_run=0.0, silence_run=41.0, face_run=1.0))
    for _ in range(40):
        p.t += 1.0
        p.m.tick(Signals(p.now, p.t, voice_run=5.0, silence_run=0.0, face_run=1.0))
    assert m.state is State.KHUTBA          # the imam is back: we do not cut


def test_sans_visage_ni_silence_on_ne_coupe_pas():
    m, p = khutba()
    for _ in range(120):
        p.t += 1.0
        p.m.tick(Signals(p.now, p.t, voice_run=0.0, silence_run=10.0, face_run=None))
    assert m.state is State.KHUTBA          # detection unavailable: nothing changes
