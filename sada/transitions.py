"""Smooth transitions between the live scenes (fades, slides), without cutting the stream.

The encoder composes: camera -> BACKGROUND layer (full screens: gradient, medallion, footer)
                              -> CONTENT A layer -> CONTENT B layer (texts / banner).
The two content layers serve as a double buffer: the next scene is written into the inactive
layer (transparent), then, once the PNG has been re-read by ffmpeg (DELAI_CHARGEMENT), the opacity
(colorchannelmixer aa) and position (overlay x/y) of the layers are animated via ZeroMQ, frame by frame.
The sound follows the same start (mic fade driven by a volume expression, see encoder.py).

Choreography (sober):
  0.00 → 0.45 s  the old content fades out
  0.00 → 0.80 s  the background rises or fades out (crossfade camera <-> full screen), if it changes
  0.35 → 1.05 s  the new content comes in: fade + 18 px slide upwards (full screens)
                 or 36 px from the left (khutba banner, dars), gentle deceleration
  0.00 → 0.80 s  the mic rises or falls (audio fade)

This module is pure (no I/O): the value computation is testable without ffmpeg.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DELAI_CHARGEMENT = 1.0  # s: the layer PNG is re-read in ≤ 0.65 s (ffmpeg queue at 25 fps)
OUT_DUR = 0.45  # fade-out of the old content
FOND_DUR = 0.80  # background fade (camera <-> full screen)
IN_START = 0.35  # entrance of the new content
IN_DUR = 0.70
AUDIO_DUR = 0.80  # audio fade of the mic
SLIDE_Y = 18  # px (720p mockup): rise of the full-screen texts
SLIDE_X = 36  # px: banner slide from the left
TOTAL = max(OUT_DUR, FOND_DUR, IN_START + IN_DUR, AUDIO_DUR)


def _clip(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_in_out(x: float) -> float:
    """Sinusoidal: soft start and arrival."""
    x = _clip(x)
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out(x: float) -> float:
    """Cubic: decelerating entrance (the title "settles")."""
    x = _clip(x)
    return 1 - (1 - x) ** 3


@dataclass
class Plan:
    """A transition from one content layer (src) to the other (dst)."""

    src: str  # "a" | "b": layer of the outgoing content
    dst: str  # layer of the incoming content
    fond_from: float  # background opacity at the start (0 or 1)
    fond_to: float
    full: bool  # the arriving scene is a full screen (vertical slide) or a banner
    scale: float = 1.0  # scale factor (height / 720)

    def values(self, tau: float) -> dict[str, float]:
        """Layer parameters at tau seconds after the start (tau >= TOTAL: final state)."""
        out = 1 - ease_in_out(tau / OUT_DUR)
        k = ease_out((tau - IN_START) / IN_DUR)
        fond = self.fond_from + (self.fond_to - self.fond_from) * ease_in_out(tau / FOND_DUR)
        v = {
            "fond": fond,
            f"a_{self.src}": out,
            f"a_{self.dst}": k,
            f"x_{self.src}": 0.0,
            f"y_{self.src}": 0.0,
            f"x_{self.dst}": 0.0,
            f"y_{self.dst}": 0.0,
        }
        if self.full:
            v[f"y_{self.dst}"] = round(SLIDE_Y * self.scale * (1 - k), 1)
        else:
            v[f"x_{self.dst}"] = round(-SLIDE_X * self.scale * (1 - k), 1)
        return v


def plan(src: str, old_full: bool, new_full: bool, scale: float = 1.0) -> Plan:
    dst = "b" if src == "a" else "a"
    return Plan(src, dst, 1.0 if old_full else 0.0, 1.0 if new_full else 0.0, new_full, scale)


def volume_ramp(a: float, b: float, dur: float = AUDIO_DUR) -> str:
    """ffmpeg expression (volume filter, eval=frame): from a to b in dur seconds, starting at the first
    audio frame following the command (start t memorised in register 0; +1 to avoid 0)."""
    if a == b:
        return f"{b:g}"
    return (
        f"if(isnan(t),{a:g},(if(eq(ld(0),0),st(0,t+1),0);{a:g}+({b:g}-{a:g})"
        f"*(0.5-0.5*cos(PI*clip((t+1-ld(0))/{dur:g},0,1)))))"
    )
