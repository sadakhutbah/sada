"""Présence au minbar (v1 : mouvement dans une zone, sans GPU).

Un ffmpeg séparé lit la caméra à quelques images par seconde, en petit format
(niveaux de gris). Chaque image est comparée à la précédente sur la seule zone du minbar
(`presence_roi`, fractions de l'image) : le score est la part de pixels de cette zone qui
changent nettement. « Présent » = un mouvement a été vu dans les `presence_hold_s` dernières
secondes.

Vie privée : aucune image n'est écrite, aucun visage ni identité n'est calculé ; seuls un
score et un booléen sortent de ce module. Une caméra de surveillance à faible débit suffit.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field

FRAME_TIMEOUT = 5.0     # sans image pendant ce délai : source considérée perdue


@dataclass
class MotionGate:
    """Calcul pur du mouvement (aucune E/S) : testable image par image."""
    width: int = 160
    height: int = 90
    roi: tuple[float, ...] = (0.0, 0.0, 1.0, 1.0)                   # x0, y0, x1, y1 en fractions
    threshold: float = 0.004        # part de pixels changés pour dire « mouvement »
    pixel_delta: int = 12           # écart de niveau (0-255) compté comme un vrai changement
    hold_s: float = 8.0             # durée pendant laquelle un mouvement vaut « présence »
    _prev: bytes | None = field(default=None, repr=False)
    _last_motion: float | None = None
    last_score: float | None = field(default=None, repr=False)

    def bounds(self) -> tuple[int, int, int, int]:
        """Zone utile en pixels (au moins 1×1, dans l'image)."""
        if len(self.roi) != 4:
            raise ValueError(f"zone de présence : 4 fractions attendues, {len(self.roi)} reçues")
        x0, y0, x1, y1 = self.roi
        bx0 = min(max(int(round(x0 * self.width)), 0), self.width - 1)
        by0 = min(max(int(round(y0 * self.height)), 0), self.height - 1)
        bx1 = min(max(int(round(x1 * self.width)), bx0 + 1), self.width)
        by1 = min(max(int(round(y1 * self.height)), by0 + 1), self.height)
        return bx0, by0, bx1, by1

    def score(self, frame: bytes) -> float | None:
        """Part (0–1) de pixels qui changent nettement dans la zone. None pour la 1re image."""
        if len(frame) != self.width * self.height:
            raise ValueError(f"image de {len(frame)} octets, attendu {self.width * self.height}")
        prev = self._prev
        self._prev = frame
        if prev is None:
            self.last_score = None
            return None
        x0, y0, x1, y1 = self.bounds()
        changed = 0
        for y in range(y0, y1):
            base = y * self.width
            row_a = frame[base + x0:base + x1]
            row_b = prev[base + x0:base + x1]
            for a, b in zip(row_a, row_b):
                if a - b > self.pixel_delta or b - a > self.pixel_delta:
                    changed += 1
        self.last_score = changed / float((x1 - x0) * (y1 - y0))
        return self.last_score

    def update(self, frame: bytes, now: float) -> bool:
        """Nouvelle image → présence à l'instant `now` (secondes monotones)."""
        s = self.score(frame)
        if s is not None and s >= self.threshold:
            self._last_motion = now
        return self.present(now)

    def present(self, now: float) -> bool:
        return self._last_motion is not None and (now - self._last_motion) <= self.hold_s


class PresenceMonitor(threading.Thread):
    """Fil de lecture de la caméra ; `present()` est sûr depuis n'importe quel fil."""

    def __init__(self, url: str, gate: MotionGate, fps: float = 2.0, log=print):
        super().__init__(daemon=True, name="presence")
        self.url, self.gate, self.fps, self.log = url, gate, fps, log
        self.stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._present = False
        self._score: float | None = None
        self._last_frame = 0.0
        self.connected = False
        self.proc: subprocess.Popen | None = None

    @property
    def frame_bytes(self) -> int:
        return self.gate.width * self.gate.height

    def run(self) -> None:
        while not self.stop_flag.is_set():
            vf = f"fps={self.fps:g},scale={self.gate.width}:{self.gate.height},format=gray"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
                   "-i", self.url, "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._read(self.proc)
            except Exception as e:  # noqa: BLE001 - on relance, la caméra peut revenir
                self.log(f"Présence : erreur {e}")
            self.connected = False
            with self._lock:
                self._present, self._score = False, None
            if self.stop_flag.wait(2.0):
                break

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        while not self.stop_flag.is_set():
            frame = proc.stdout.read(self.frame_bytes)
            if len(frame) < self.frame_bytes:
                return
            now = time.monotonic()
            try:
                present = self.gate.update(frame, now)
            except ValueError as e:
                self.log(f"Présence : image ignorée ({e})")
                continue
            score = self.gate.last_score
            with self._lock:
                self.connected = True
                self._last_frame = now
                self._present = present
                self._score = score

    def present(self) -> bool | None:
        """True/False, ou None si la caméra ne donne plus d'image (détection inutilisable)."""
        with self._lock:
            if not self.connected or (time.monotonic() - self._last_frame) > FRAME_TIMEOUT:
                return None
            return self._present

    def last_score(self) -> float | None:
        with self._lock:
            return self._score

    def stop(self) -> None:
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
