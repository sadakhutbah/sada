"""Face at the minbar: frontal face detection on the camera (YuNet, without GPU).

A separate ffmpeg reads the camera a few frames per second, in a small BGR format.
Each frame is passed to YuNet on the minbar region only (`face_roi`): "face" =
at least one frontal face whose score exceeds `threshold`.

This is the end-of-khutba signal: the imam standing facing the congregation is seen frontally;
during the prayer he turns his back (qibla) and the room is in profile or from behind, so no
frontal face is left in front of the minbar. The audio signal alone is not enough — a full
room is never silent.

Privacy: no frame is written, no identity is computed, no template is
kept. Only a score and a boolean leave this module. The model (232 KB, YuNet
2023mar, MIT licence) is embedded in `sada/models/`.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import source
from .i18n import _

FRAME_TIMEOUT = 5.0  # no frame for this long: source considered lost
MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"


@dataclass
class FaceGate:
    """Pure temporal decision (no I/O), testable with a fake detector.

    `detect(image, roi)` must return the list of faces found in the ROI region,
    each face being a tuple whose last element is the score (0–1).
    """

    detect: object  # callable(image, roi) -> [(x, y, w, h, score), …]
    roi: tuple[float, ...] = (0.0, 0.0, 1.0, 1.0)  # x0, y0, x1, y1 as fractions
    threshold: float = 0.6  # minimum score for a frontal face
    hold_s: float = 4.0  # a face seen counts as "present" for this delay
    _last_face: float | None = field(default=None, repr=False)
    _last_seen: float | None = field(default=None, repr=False)
    last_score: float | None = field(default=None, repr=False)

    def update(self, image: np.ndarray, now: float) -> bool:
        """New frame → frontal face at instant `now`?"""
        if len(self.roi) != 4:
            raise ValueError(_("face zone: 4 fractions expected, {count} received", count=len(self.roi)))
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
        """Usable region in pixels (at least 1×1, within the frame)."""
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
        """Seconds elapsed since the last frontal face (None: no face seen yet)."""
        return None if self._last_face is None else now - self._last_face


def yunet_detector(threshold: float = 0.6, size: tuple[int, int] = (320, 180)):
    """Ready-to-use YuNet detector: callable(BGR image, roi) -> [(x, y, w, h, score)]."""
    import cv2

    det = cv2.FaceDetectorYN.create(str(MODEL), "", size, threshold, 0.3, 5000)

    def detect(image, _roi=None):
        det.setInputSize((image.shape[1], image.shape[0]))
        _scores, faces = det.detect(image)
        if faces is None:
            return []
        return [tuple(f) for f in faces]

    return detect


class FaceMonitor(threading.Thread):
    """Camera reader thread; `present()` and `absence_run()` are safe from any thread."""

    def __init__(self, url: str, gate: FaceGate, width: int = 320, height: int = 180, fps: float = 5.0, log=print):
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
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                *source.input_options(self.url),
                "-i",
                self.url,
                "-an",
                "-vf",
                vf,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-",
            ]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._read(self.proc)
            except Exception as e:  # noqa: BLE001 - we restart, the camera may come back
                self.log(_("Face: error {error}", error=e))
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
                self.log(_("Face: frame ignored ({error})", error=e))
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
        """True/False, or None if the camera no longer provides frames (detection unusable)."""
        with self._lock:
            return self._present if self._alive() else None

    def absence_run(self) -> float | None:
        """Seconds without a frontal face, or None if detection is not usable."""
        with self._lock:
            return self._absence if self._alive() else None

    def last_score(self) -> float | None:
        with self._lock:
            return self._score

    def stop(self) -> None:
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
