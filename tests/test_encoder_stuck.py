"""An ffmpeg that is alive but whose graph no longer answers (source switched) is killed so `_watch` relaunches it."""

import time

from sada.config import Config
from sada.encoder import STUCK_GRACE_S, Encoder


class FakeProc:
    def __init__(self):
        self.killed = False
        self.returncode = -9

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


# --- a destination dropped by the tee muxer (source switch: the output starved, the platform cut the link)


def test_dropped_destination_is_noticed_in_ffmpeg_stderr(tmp_path):
    enc = _enc(tmp_path, STUCK_GRACE_S + 1)
    proc = enc.proc
    proc.stderr = iter(["frame dup\n", "[tee @ 0x1] Slave muxer #0 failed: Broken pipe, continuing with 2/3 slaves.\n"])
    with open(tmp_path / "encoder.log", "ab") as f:
        enc._read_errors(proc, f)
    assert enc._dropped
    assert b"Broken pipe" in (tmp_path / "encoder.log").read_bytes()


def test_dropped_destination_triggers_a_relaunch_after_the_grace(tmp_path):
    enc = _enc(tmp_path, STUCK_GRACE_S + 1)
    proc = enc.proc
    enc._dropped = True
    enc._tick()
    assert proc.killed and not enc._dropped
    enc.recordings.append(("rec.mkv", None))
    enc._opened = True
    enc._tick()  # the dead ffmpeg is noticed: relaunch is scheduled (source had been opened -> quick)
    assert enc.proc is None


def test_a_destination_broken_at_start_up_is_not_relaunched_in_a_loop(tmp_path):
    enc = _enc(tmp_path, 1)
    enc._dropped = True
    enc._tick()
    assert not enc.proc.killed and enc._dropped
