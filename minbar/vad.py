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
        self._last_voice = time.monotonic()
        self._dense_since = None
        self.dense_min = 0.4
        self.connected = False
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
                # silence_run : secondes écoulées depuis la dernière trame « voix » (une seule suffit à le remettre à 0)
                if voice:
                    self._last_voice = now
                # voice_run : durée pendant laquelle la densité de voix sur 10 s reste >= 40 %
                # (un orateur fait des pauses : exiger une voix continue 20 s ne marcherait jamais)
                dense = len(dens) == dens.maxlen and sum(dens) / len(dens) >= self.dense_min
                if dense and self._dense_since is None:
                    self._dense_since = now
                elif not dense:
                    self._dense_since = None

    def runs(self) -> tuple[float, float]:
        """(voice_run, silence_run) en secondes réelles."""
        with self._lock:
            now = time.monotonic()
            v = (now - self._dense_since) if self._dense_since is not None else 0.0
            return v, now - self._last_voice

    def stop(self):
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
