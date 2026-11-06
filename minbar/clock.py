"""Horloge simulée / accélérée. now() renvoie l'heure « mosquée ».

La vitesse peut changer à chaud (l'orchestrateur la remet à 1x pendant la khutba,
car la caméra simulée, elle, joue en temps réel)."""
from __future__ import annotations
import datetime as dt
import threading
import time


class SimClock:
    def __init__(self, start: dt.datetime | None = None, speed: float = 1.0):
        self._lock = threading.Lock()
        self._real0 = time.monotonic()
        self._offset = (start - dt.datetime.now()) if start else dt.timedelta(0)
        self._base = start or dt.datetime.now()
        self._speed = speed
        self._anchor_real = time.monotonic()
        self._anchor_sim = self._base

    def now(self) -> dt.datetime:
        with self._lock:
            elapsed = time.monotonic() - self._anchor_real
            return self._anchor_sim + dt.timedelta(seconds=elapsed * self._speed)

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        with self._lock:
            t = time.monotonic()
            self._anchor_sim += dt.timedelta(seconds=(t - self._anchor_real) * self._speed)
            self._anchor_real = t
            self._speed = speed

    def jump(self, delta_min: float) -> None:
        with self._lock:
            self._anchor_sim += dt.timedelta(minutes=delta_min)


def parse_hhmm(s: str, day: dt.date) -> dt.datetime:
    h, m = s.strip().split(":")[:2]
    return dt.datetime.combine(day, dt.time(int(h), int(m)))
