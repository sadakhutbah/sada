"""Visage au minbar : détection de visage de face sur la caméra (YuNet, sans GPU).

Un ffmpeg séparé lit la caméra à quelques images par seconde, en petit format BGR.
Chaque image est passée à YuNet sur la seule zone du minbar (`face_roi`) : « visage » =
au moins un visage de face dont le score dépasse `threshold`.

C'est le signal de la fin de khutba : l'imam debout face à l'assemblée est vu de face ;
pendant la prière il tourne le dos (qibla) et la salle est de profil ou de dos, donc plus
aucun visage de face devant le minbar. Le signal audio seul ne suffit pas — une salle
pleine n'est jamais silencieuse.

Vie privée : aucune image n'est écrite, aucune identité n'est calculée, aucun gabarit n'est
conservé. Seuls un score et un booléen sortent de ce module. Le modèle (232 Ko, YuNet
2023mar, licence MIT) est embarqué dans `minbar/models/`.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FRAME_TIMEOUT = 5.0     # sans image pendant ce délai : source considérée perdue
MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"


@dataclass
class FaceGate:
    """Décision temporelle pure (aucune E/S), testable avec un détecteur factice.

    `detect(image, roi)` doit renvoyer la liste des visages trouvés dans la zone ROI,
    chaque visage étant un tuple dont le dernier élément est le score (0–1).
    """
    detect: object                     # callable(image, roi) -> [(x, y, w, h, score), …]
    roi: tuple[float, ...] = (0.0, 0.0, 1.0, 1.0)   # x0, y0, x1, y1 en fractions
    threshold: float = 0.6             # score minimal d'un visage de face
    hold_s: float = 4.0                # un visage vu vaut « présent » pendant ce délai
    _last_face: float | None = field(default=None, repr=False)
    _last_seen: float | None = field(default=None, repr=False)
    last_score: float | None = field(default=None, repr=False)

    def update(self, image: np.ndarray, now: float) -> bool:
        """Nouvelle image → visage de face à l'instant `now` ?"""
        if len(self.roi) != 4:
            raise ValueError(f"zone de visage : 4 fractions attendues, {len(self.roi)} reçues")
        x0, y0, x1, y1 = self.bounds(image.shape)
        zone = image[y0:y1, x0:x1]
        faces = self.detect(zone, None) or []
        scores = [float(f[-1]) for f in faces if len(f)]
        best = max(scores) if scores else 0.0
        self.last_score = best
        self._last_seen = now
        if best >= self.threshold:
            self._last_face = now
        return self.present(now)

    def bounds(self, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
        """Zone utile en pixels (au moins 1×1, dans l'image)."""
        h, w = shape[0], shape[1]
        fx0, fy0, fx1, fy1 = self.roi
        bx0 = min(max(int(round(fx0 * w)), 0), w - 1)
        by0 = min(max(int(round(fy0 * h)), 0), h - 1)
        bx1 = min(max(int(round(fx1 * w)), bx0 + 1), w)
        by1 = min(max(int(round(fy1 * h)), by0 + 1), h)
        return bx0, by0, bx1, by1

    def present(self, now: float) -> bool:
        return self._last_face is not None and (now - self._last_face) <= self.hold_s

    def absence_run(self, now: float) -> float | None:
        """Secondes écoulées depuis le dernier visage de face (None : aucun visage vu encore)."""
        return None if self._last_face is None else now - self._last_face


def yunet_detector(threshold: float = 0.6, size: tuple[int, int] = (320, 180)):
    """Détecteur YuNet prêt à l'emploi : callable(image BGR, roi) -> [(x, y, w, h, score)]."""
    import cv2

    det = cv2.FaceDetectorYN.create(str(MODEL), "", size, threshold, 0.3, 5000)

    def detect(image, _roi=None):
        det.setInputSize((image.shape[1], image.shape[0]))
        _, faces = det.detect(image)
        if faces is None:
            return []
        return [tuple(f) for f in faces]

    return detect


class FaceMonitor(threading.Thread):
    """Fil de lecture de la caméra ; `present()` et `absence_run()` sont sûrs depuis n'importe quel fil."""

    def __init__(self, url: str, gate: FaceGate, width: int = 320, height: int = 180,
                 fps: float = 5.0, log=print):
        super().__init__(daemon=True, name="faces")
        self.url, self.gate, self.width, self.height, self.fps, self.log = url, gate, width, height, fps, log
        self.stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._present = False
        self._absence: float | None = None
        self._score: float | None = None
        self._last_frame = 0.0
        self.connected = False
        self.proc: subprocess.Popen | None = None

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3

    def run(self) -> None:
        while not self.stop_flag.is_set():
            vf = f"fps={self.fps:g},scale={self.width}:{self.height},format=bgr24"
            transport = ["-rtsp_transport", "tcp"] if self.url.startswith("rtsp://") else []
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *transport,
                   "-i", self.url, "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._read(self.proc)
            except Exception as e:  # noqa: BLE001 - on relance, la caméra peut revenir
                self.log(f"Visage : erreur {e}")
            self.connected = False
            with self._lock:
                self._present, self._absence, self._score = False, None, None
            if self.stop_flag.wait(2.0):
                break

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        while not self.stop_flag.is_set():
            frame = proc.stdout.read(self.frame_bytes)
            if len(frame) < self.frame_bytes:
                return
            img = np.frombuffer(frame, dtype=np.uint8).reshape(self.height, self.width, 3)
            now = time.monotonic()
            try:
                present = self.gate.update(img, now)
            except ValueError as e:
                self.log(f"Visage : image ignorée ({e})")
                continue
            with self._lock:
                self.connected = True
                self._last_frame = now
                self._present = present
                self._absence = self.gate.absence_run(now)
                self._score = self.gate.last_score

    def _alive(self) -> bool:
        return self.connected and (time.monotonic() - self._last_frame) <= FRAME_TIMEOUT

    def present(self) -> bool | None:
        """True/False, ou None si la caméra ne donne plus d'image (détection inutilisable)."""
        with self._lock:
            return self._present if self._alive() else None

    def absence_run(self) -> float | None:
        """Secondes sans visage de face, ou None si la détection n'est pas utilisable."""
        with self._lock:
            return self._absence if self._alive() else None

    def last_score(self) -> float | None:
        with self._lock:
            return self._score

    def stop(self) -> None:
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
