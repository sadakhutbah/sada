"""An ffmpeg that is alive but whose graph no longer answers (source switched) is killed so `_watch` relaunches it."""

import time

from sada.config import Config
from sada.encoder import STUCK_GRACE_S, Encoder


class FakeProc:
    def __init__(self):
        self.killed = False

    def poll(self):
        return -9 if self.killed else None

    def kill(self):
        self.killed = True


def _enc(tmp_path, age):
    enc = Encoder(Config(), tmp_path, log=lambda _m: None)
    enc.proc, enc.wanted = FakeProc(), True
    enc._spawned_at = time.monotonic() - age
    return enc


def test_stuck_ffmpeg_is_killed(tmp_path):
    enc = _enc(tmp_path, STUCK_GRACE_S + 1)
    assert enc.restart_if_stuck("audio") is True
    assert enc.proc.killed and not enc.alive()


def test_young_ffmpeg_is_left_alone(tmp_path):
    enc = _enc(tmp_path, 1)
    assert enc.restart_if_stuck("audio") is False
    assert not enc.proc.killed


def test_stopped_encoder_is_not_restarted(tmp_path):
    enc = _enc(tmp_path, STUCK_GRACE_S + 1)
    enc.wanted = False
    assert enc.restart_if_stuck("audio") is False
    assert not enc.proc.killed
