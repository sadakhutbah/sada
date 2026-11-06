"""Détection de voix (webrtcvad) sur l'audio de la caméra / table de mixage.

Un ffmpeg séparé lit l'audio de la source (RTSP) en PCM 16 kHz mono ; on découpe en trames de 30 ms
et on garde une fenêtre glissante d'une seconde : « voix » si la part de trames voisées >= vad_ratio.
Expose voice_run / silence_run (secondes réelles consécutives de voix / de non-voix).
"""
from __future__ import annotations
import collections
import subprocess
import threading
import time

import webrtcvad

RATE = 16000
FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2


class VadMonitor(threading.Thread):
    def __init__(self, url: str, aggressiveness: int = 3, ratio: float = 0.5, log=print):
        super().__init__(daemon=True, name="vad")
        self.url, self.ratio, self.log = url, ratio, log
        self.vad = webrtcvad.Vad(aggressiveness)
        self.stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._is_voice = False
        self._since = time.monotonic()
        self.connected = False
        self.last_audio = 0.0
        self.proc: subprocess.Popen | None = None

    def run(self):
        while not self.stop_flag.is_set():
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", self.url,
                   "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._read(self.proc)
            except Exception as e:  # noqa
                self.log(f"VAD : erreur {e}")
            self.connected = False
            if self.stop_flag.wait(2.0):
                break

    def _read(self, proc):
        win = collections.deque(maxlen=int(1000 / FRAME_MS))
        assert proc.stdout is not None
        while not self.stop_flag.is_set():
            buf = proc.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                return
            self.connected = True
            self.last_audio = time.monotonic()
            try:
                win.append(self.vad.is_speech(buf, RATE))
            except Exception:
                win.append(False)
            voice = len(win) == win.maxlen and sum(win) / len(win) >= self.ratio
            with self._lock:
                if voice != self._is_voice:
                    self._is_voice = voice
                    self._since = time.monotonic()

    def runs(self) -> tuple[float, float]:
        """(voice_run, silence_run) en secondes réelles."""
        with self._lock:
            d = time.monotonic() - self._since
            return (d, 0.0) if self._is_voice else (0.0, d)

    def stop(self):
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
