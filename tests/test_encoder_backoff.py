"""A source that is not available must not cause a relaunch storm nor leave empty recordings behind."""

import io
import os
import time

from sada.config import Config
from sada.encoder import BACKOFF_MAX_S, BACKOFF_MIN_S, Encoder, backoff_delay


class DeadProc:
    returncode = 1

    def poll(self):
        return 1


def test_backoff_doubles_and_is_capped():
    delays = [backoff_delay(n) for n in range(1, 10)]
    assert delays[:6] == [1, 2, 4, 8, 16, 30]
    assert max(delays) == BACKOFF_MAX_S and delays[0] == BACKOFF_MIN_S


def _enc(tmp_path, logs):
    cfg = Config()
    cfg.record_dir = str(tmp_path / "rec")
    enc = Encoder(cfg, tmp_path, log=logs.append)
    enc.wanted = True
    return enc


def _failed_attempt(enc):
    rec, clean = enc.new_recording()
    open(rec, "wb").close()  # ffmpeg left an empty file behind
    enc.recordings.append((rec, clean))
    enc.proc, enc._opened = DeadProc(), False
    enc._on_exit()
    return rec


def test_failed_attempts_leave_no_file_and_log_once(tmp_path):
    logs: list[str] = []
    enc = _enc(tmp_path, logs)
    for _ in range(5):
        rec = _failed_attempt(enc)
        assert not os.path.exists(rec)
    assert enc.recordings == [] and enc.failures == 5
    assert len(logs) == 1  # only the first failure is logged
    assert enc._next_spawn - time.monotonic() > BACKOFF_MIN_S * 8  # delay grew


def test_recovery_resets_backoff_and_logs_once(tmp_path):
    logs: list[str] = []
    enc = _enc(tmp_path, logs)
    for _ in range(3):
        _failed_attempt(enc)
    proc = enc.proc = DeadProc()
    enc._opened = False

    proc.stdout = io.StringIO("out_time_us=1000\nout_time_us=2000\n")
    enc._read_progress(proc)
    assert enc.failures == 0 and enc._opened
    assert len(logs) == 2  # first failure + recovery
