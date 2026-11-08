"""Transitions douces entre les scènes du direct (fondus, glissements), sans couper le flux.

L'encodeur compose : caméra -> calque FOND (écrans pleins : dégradé, médaillon, pied de page)
                              -> calque CONTENU A -> calque CONTENU B (textes / bandeau).
Les deux calques de contenu servent de double tampon : la scène suivante est écrite dans le calque
inactif (transparent), puis, une fois le PNG relu par ffmpeg (DELAI_CHARGEMENT), on anime par
ZeroMQ l'opacité (colorchannelmixer aa) et la position (overlay x/y) des calques, image par image.
Le son suit le même départ (fondu du micro piloté par une expression de volume, voir encoder.py).

Chorégraphie (sobre) :
  0,00 → 0,45 s  l'ancien contenu s'efface (fondu)
  0,00 → 0,80 s  le fond se lève ou s'efface (fondu enchaîné caméra <-> écran plein), s'il change
  0,35 → 1,05 s  le nouveau contenu entre : fondu + glissement de 18 px vers le haut (écrans pleins)
                 ou de 36 px depuis la gauche (bandeau khutba, mention janaza), décélération douce
  0,00 → 0,80 s  le micro monte ou descend (fondu sonore)

Ce module est pur (aucune E/S) : le calcul des valeurs est testable sans ffmpeg.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DELAI_CHARGEMENT = 1.0   # s : le PNG du calque est relu en ≤ 0,65 s (file d'attente ffmpeg à 25 i/s)
OUT_DUR = 0.45           # fondu de sortie de l'ancien contenu
FOND_DUR = 0.80          # fondu du fond (caméra <-> écran plein)
IN_START = 0.35          # entrée du nouveau contenu
IN_DUR = 0.70
AUDIO_DUR = 0.80         # fondu sonore du micro
SLIDE_Y = 18             # px (maquette 720p) : montée des textes d'écran plein
SLIDE_X = 36             # px : glissement du bandeau depuis la gauche
TOTAL = max(OUT_DUR, FOND_DUR, IN_START + IN_DUR, AUDIO_DUR)


def _clip(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_in_out(x: float) -> float:
    """Sinusoïdale : départ et arrivée doux."""
    x = _clip(x)
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out(x: float) -> float:
    """Cubique : entrée qui décélère (le titre « se pose »)."""
    x = _clip(x)
    return 1 - (1 - x) ** 3


@dataclass
class Plan:
    """Une transition d'un calque de contenu (src) vers l'autre (dst)."""
    src: str            # "a" | "b" : calque du contenu sortant
    dst: str            # calque du contenu entrant
    fond_from: float    # opacité du fond au départ (0 ou 1)
    fond_to: float
    full: bool          # la scène d'arrivée est un écran plein (glissement vertical) ou un bandeau
    scale: float = 1.0  # facteur d'échelle (hauteur / 720)

    def values(self, tau: float) -> dict[str, float]:
        """Paramètres des calques à tau secondes après le départ (tau >= TOTAL : état final)."""
        out = 1 - ease_in_out(tau / OUT_DUR)
        k = ease_out((tau - IN_START) / IN_DUR)
        fond = self.fond_from + (self.fond_to - self.fond_from) * ease_in_out(tau / FOND_DUR)
        v = {"fond": fond, f"a_{self.src}": out, f"a_{self.dst}": k,
             f"x_{self.src}": 0.0, f"y_{self.src}": 0.0, f"x_{self.dst}": 0.0, f"y_{self.dst}": 0.0}
        if self.full:
            v[f"y_{self.dst}"] = round(SLIDE_Y * self.scale * (1 - k), 1)
        else:
            v[f"x_{self.dst}"] = round(-SLIDE_X * self.scale * (1 - k), 1)
        return v


def plan(src: str, old_full: bool, new_full: bool, scale: float = 1.0) -> Plan:
    dst = "b" if src == "a" else "a"
    return Plan(src, dst, 1.0 if old_full else 0.0, 1.0 if new_full else 0.0, new_full, scale)


def volume_ramp(a: float, b: float, dur: float = AUDIO_DUR) -> str:
    """Expression ffmpeg (filtre volume, eval=frame) : de a à b en dur secondes, à partir de la première
    trame audio qui suit la commande (t de départ mémorisé dans le registre 0 ; +1 pour éviter 0)."""
    if a == b:
        return f"{b:g}"
    return (f"if(isnan(t),{a:g},(if(eq(ld(0),0),st(0,t+1),0);{a:g}+({b:g}-{a:g})"
            f"*(0.5-0.5*cos(PI*clip((t+1-ld(0))/{dur:g},0,1)))))")
