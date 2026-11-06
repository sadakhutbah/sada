"""Rendu des scènes (PNG RGBA 1280x720) avec Pillow, sans OBS.

Le flux est composé ainsi : caméra  +  overlay(PNG de la scène courante).
 - scènes « plein écran » (avant, priere, entracte, termine) : PNG opaque qui masque la caméra ;
 - scène khutba : PNG transparent avec un bandeau en bas ; janaza : PNG quasi vide, sans bandeau.
L'encodeur relit ce PNG en continu (image2 -loop 1) : changer de scène = réécrire le fichier.
"""
from __future__ import annotations
import datetime as dt
import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _hex(c: str, a: int = 255):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


def _font(path: str, size: int):
    return ImageFont.truetype(path, size)


class SceneRenderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.W, self.H = cfg.width, cfg.height
        self.green = _hex(cfg.green)
        self.dark = _hex(cfg.dark_green)
        logo = Image.open(cfg.logo).convert("RGBA")
        self._logo_cache: dict[int, Image.Image] = {}
        self._logo = logo

    def logo(self, size: int) -> Image.Image:
        if size not in self._logo_cache:
            self._logo_cache[size] = self._logo.resize((size, size), Image.LANCZOS)
        return self._logo_cache[size]

    # --- briques ------------------------------------------------------------
    def _bg(self) -> Image.Image:
        """Fond dégradé vert foncé -> vert mosquée."""
        W, H = self.W, self.H
        img = Image.new("RGBA", (W, H))
        px = ImageDraw.Draw(img)
        top, bot = _hex("#0f3d20"), self.dark
        for y in range(H):
            t = y / (H - 1)
            px.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
        px.rectangle([0, H - 14, W, H], fill=self.green)
        return img

    def _center(self, d: ImageDraw.ImageDraw, text: str, y: int, font, fill, W: Optional[int] = None):
        W = W or self.W
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2, y), text, font=font, fill=fill)

    def _full(self, title: str, sub: str = "", big: str = "", foot: str = "") -> Image.Image:
        img = self._bg()
        d = ImageDraw.Draw(img)
        # pastille blanche derrière le logo (le logo est sur fond blanc)
        s = 230
        cx, cy = self.W // 2, 150
        d.ellipse([cx - s // 2 - 8, cy - s // 2 - 8, cx + s // 2 + 8, cy + s // 2 + 8], fill=(255, 255, 255, 255))
        img.alpha_composite(self.logo(s), (cx - s // 2, cy - s // 2))
        self._center(d, title, 300, _font(FONT_B, 46), (255, 255, 255, 255))
        if sub:
            self._center(d, sub, 368, _font(FONT_R, 30), self.green)
        if big:
            self._center(d, big, 430, _font(FONT_B, 110), (255, 255, 255, 255))
        if foot:
            self._center(d, foot, 600, _font(FONT_R, 26), (230, 240, 225, 255))
        self._center(d, self.cfg.mosque_name, self.H - 62, _font(FONT_B, 24), (255, 255, 255, 255))
        return img

    # --- scènes -------------------------------------------------------------
    def avant(self, now: dt.datetime, planned: Optional[dt.datetime]) -> Image.Image:
        at = f"à {planned:%H:%M}" if planned else ""
        return self._full(f"{self.cfg.title} — début imminent", at, "", "Merci de patienter, la diffusion commence très bientôt.")

    def priere(self) -> Image.Image:
        return self._full("Prière en cours", "La prière n'est pas diffusée", "", "La diffusion reprendra pour la prochaine khutba, in sha Allah.")

    def entracte(self, now: dt.datetime, planned: Optional[dt.datetime]) -> Image.Image:
        if planned:
            remaining = max(0, int((planned - now).total_seconds()))
            m, s = divmod(remaining, 60)
            big = f"{m:02d}:{s:02d}"
            sub = f"Prochaine khutba à {planned:%H:%M}"
        else:
            big, sub = "", ""
        return self._full("Entracte", sub, big, "Le live continue : restez connectés.")

    def termine(self, next_friday: str = "") -> Image.Image:
        return self._full("Khutba terminée — qu'Allah vous récompense", "Jazakoum Allahou khayran", "", next_friday or "Rendez-vous vendredi prochain, in sha Allah.")

    def khutba(self, subtitle: str = "") -> Image.Image:
        """Transparent + bandeau en bas (logo, mosquée, titre)."""
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        h = 96
        y0 = self.H - h - 26
        d.rectangle([40, y0, self.W - 40, y0 + h], fill=_hex(self.cfg.dark_green, 225))
        d.rectangle([40, y0, 52, y0 + h], fill=self.green)
        s = h - 14
        d.ellipse([66, y0 + 7, 66 + s, y0 + 7 + s], fill=(255, 255, 255, 255))
        img.alpha_composite(self.logo(s - 6), (69, y0 + 10))
        x = 66 + s + 22
        d.text((x, y0 + 12), self.cfg.title, font=_font(FONT_B, 34), fill=(255, 255, 255, 255))
        d.text((x, y0 + 56), self.cfg.mosque_name + ((" — " + subtitle) if subtitle else ""), font=_font(FONT_R, 24), fill=self.green)
        return img

    def janaza(self) -> Image.Image:
        """Sobre : caméra minbar, pas de bandeau ; seule une petite mention discrète."""
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([40, self.H - 70, 420, self.H - 30], fill=(0, 0, 0, 150))
        d.text((55, self.H - 64), "Salat al-Janaza", font=_font(FONT_R, 26), fill=(255, 255, 255, 255))
        return img

    def render(self, scene: Optional[str], now: dt.datetime, planned: Optional[dt.datetime]) -> Image.Image:
        if scene == "avant":
            return self.avant(now, planned)
        if scene == "priere":
            return self.priere()
        if scene == "entracte":
            return self.entracte(now, planned)
        if scene == "termine":
            return self.termine()
        if scene == "janaza":
            return self.janaza()
        return self.khutba()


def write_png_atomic(img: Image.Image, path: str) -> None:
    tmp = f"{path}.tmp.png"
    img.save(tmp, "PNG", compress_level=1)
    os.replace(tmp, path)
