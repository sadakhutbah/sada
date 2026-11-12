"""Simulated / accelerated clock. now() returns the "mosque" time.

The speed can change at runtime (the orchestrator resets it to 1x during the khutba,
because the simulated camera, for its part, runs in real time)."""
from __future__ import annotations

import datetime as dt
import threading
import time


class SimClock:
    def __init__(self, start: dt.datetime | None = None, speed: float = 1.0):
        self._lock = threading.Lock()
        self._speed = speed
        self._anchor_real = time.monotonic()
        self._anchor_sim = start or dt.datetime.now()

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


def parse_hhmm(s: str, day: dt.date) -> dt.datetime:
    h, m = s.strip().split(":")[:2]
    return dt.datetime.combine(day, dt.time(int(h), int(m)))
