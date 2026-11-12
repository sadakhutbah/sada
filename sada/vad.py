"""Voice detection (webrtcvad) on the camera / mixing desk audio.

A separate ffmpeg reads the source audio (RTSP) as 16 kHz mono PCM; it is split into 30 ms frames
and a sliding one-second window is kept: "voice" if the share of voiced frames >= vad_ratio.
Exposes voice_run / silence_run (real consecutive seconds of voice / of non-voice).
"""
from __future__ import annotations
import collections
import shutil
import subprocess
import threading
import time

import webrtcvad

from . import source
from .i18n import _

ffmpeg = shutil.which("ffmpeg")

RATE = 16000
FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2


def _last_voice_offset(path: str) -> float | None:
    """Offset (s) of the end of the last speech in a file, measured on its audio track.

    The recording does not run to the end of the file: the stream may stop (or the source loop) while
    the camera keeps writing. Measured with silencedetect at a fixed threshold, no VAD needed for offline."""
    if not ffmpeg:
        return None
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-af",
                            "silencedetect=noise=-30dB:d=0.8", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
    except Exception:  # noqa: BLE001
        return None
    last = None
    for line in r.stderr.splitlines():
        if "silence_end:" in line and "silence_duration" in line:
            try:
                last = float(line.split("silence_end:")[1].split("|")[0])
            except ValueError:
                continue
    return last


class VadMonitor(threading.Thread):
    def __init__(self, url: str, aggressiveness: int = 3, ratio: float = 0.5, log=print):
        super().__init__(daemon=True, name="vad")
        self.url, self.ratio, self.log = url, ratio, log
        self.vad = webrtcvad.Vad(aggressiveness)
        self.stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._last_voice = time.monotonic()
        self._dense_since = None
        self.dense_min = 0.4
        self.connected = False
        self.proc: subprocess.Popen | None = None

    def run(self):
        while not self.stop_flag.is_set():
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *source.input_options(self.url), "-i", self.url,
                   "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._read(self.proc)
            except Exception as e:  # noqa
                self.log(_("VAD: error {error}", error=e))
            self.connected = False
            if self.stop_flag.wait(2.0):
                break

    def _read(self, proc):
        win = collections.deque(maxlen=int(1000 / FRAME_MS))
        dens = collections.deque(maxlen=int(10000 / FRAME_MS))
        assert proc.stdout is not None
        while not self.stop_flag.is_set():
            buf = proc.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                return
            self.connected = True
            try:
                win.append(self.vad.is_speech(buf, RATE))
            except Exception:
                win.append(False)
            voice = len(win) == win.maxlen and sum(win) / len(win) >= self.ratio
            dens.append(voice)
            now = time.monotonic()
            with self._lock:
                # silence_run: seconds elapsed since the last "voice" frame (a single one resets it to 0)
                if voice:
                    self._last_voice = now
                # voice_run: duration for which the voice density over 10 s stays >= 40 %
                # (a speaker pauses: requiring 20 s of continuous voice would never work)
                dense = len(dens) == dens.maxlen and sum(dens) / len(dens) >= self.dense_min
                if dense and self._dense_since is None:
                    self._dense_since = now
                elif not dense:
                    self._dense_since = None

    def runs(self) -> tuple[float, float]:
        """(voice_run, silence_run) in real seconds."""
        with self._lock:
            now = time.monotonic()
            v = (now - self._dense_since) if self._dense_since is not None else 0.0
            return v, now - self._last_voice

    def stop(self):
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
