"""Scènes du direct (SCENES) et leur rendu (calques RGBA 1280x720) avec Pillow, sans OBS.

Le flux est composé ainsi : caméra + fond commun des écrans pleins + contenu de la scène (encoder.py).
 - écrans pleins (avant, priere, entracte, termine) : le fond masque la caméra, le contenu est le bloc de textes ;
 - khutba : bandeau en bas à gauche ; janaza : mention discrète. Le micro n'est ouvert que sur ces deux scènes.

Charte : vert profond (dérivé de cfg.dark_green), or mat en accent, texte ivoire ; titres en
EB Garamond, textes courants en Source Sans 3, arabe en Amiri (polices OFL dans assets/fonts).
L'arabe passe par libraqm (mise en forme des lettres liées + sens RTL) ; à défaut, arabic_reshaper
+ python-bidi s'ils sont installés. Tout le contenu utile reste dans une zone de sécurité centrale
(les surcouches de l'appli Facebook mobile occupent les coins et le bas de l'image).
"""
from __future__ import annotations
import datetime as dt
import functools
import math
from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .paths import FONTS
from .text import french_date, shaped, is_arabic_char, RAQM

SERIF = str(FONTS / "EBGaramond-SemiBold.ttf")
SANS = str(FONTS / "SourceSans3-Regular.ttf")
SANS_SB = str(FONTS / "SourceSans3-Semibold.ttf")
ARABIC = str(FONTS / "Amiri-Regular.ttf")
ARABIC_B = str(FONTS / "Amiri-Bold.ttf")

# --- palette ------------------------------------------------------------------
IVORY = (244, 240, 230, 255)
IVORY_DIM = (214, 210, 198, 255)
GOLD = (200, 169, 106, 255)          # or mat, jamais saturé
GOLD_SOFT = (200, 169, 106, 150)

# --- textes fixes (arabe vérifié) --------------------------------------------
AR_KHUTBA = "خطبة الجمعة"
AR_JUMUA = "صلاة الجمعة"
AR_JAZAKUM = "جزاكم الله خيرا"
AR_JANAZA = "صلاة الجنازة"


def _hex(c: str, a: int = 255):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


def _mix(c1, c2, t: float, a: int = 255):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3)) + (a,)


def _alpha(c, a: int):
    return c[:3] + (a,)


@functools.lru_cache(maxsize=64)
def _font(path: str, size: int):
    layout = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
    return ImageFont.truetype(path, size, layout_engine=layout)


def _strong(ch: str) -> Optional[bool]:
    """True = arabe, False = latin (fort), None = neutre (espace, ponctuation, chiffres)."""
    if is_arabic_char(ch) and ch.isalpha():
        return True
    if ch.isalpha():
        return False
    return None


def _runs(text: str) -> list[tuple[str, bool]]:
    """Découpe en segments mono-écriture, dans l'ordre VISUEL (gauche -> droite).

    Les neutres situés entre deux lettres arabes restent dans le segment arabe ; ceux en bordure
    vont au contexte de base. Paragraphe RTL si la première lettre forte est arabe."""
    kinds = [_strong(ch) for ch in text]
    base_rtl = next((k for k in kinds if k is not None), False)
    runs: list[list] = []
    i, n = 0, len(text)
    while i < n:
        if kinds[i] is True:
            j = i
            last_ar = i
            while j < n and kinds[j] is not False:
                if kinds[j] is True:
                    last_ar = j
                j += 1
            runs.append([text[i:last_ar + 1], True])
            i = last_ar + 1
        else:
            j = i
            while j < n and kinds[j] is not True:
                j += 1
            seg = text[i:j]
            if base_rtl and runs and runs[-1][1] and not seg.strip(" \u00a0-—·:,.;()«»") :
                runs[-1][0] += seg  # neutres entre deux segments arabes
            else:
                runs.append([seg, False])
            i = j
    runs = [(t, ar) for t, ar in runs if t]
    if base_rtl:
        runs.reverse()
    return runs


def _shape(text: str) -> tuple[str, dict]:
    """Texte mono-écriture + options de d.text() (arabe : lettres liées et sens RTL)."""
    return shaped(text)


def _run_font(font, arabic: bool):
    """Police adaptée au segment : Amiri pour l'arabe, Source Sans pour le latin."""
    path = getattr(font, "path", "")
    if arabic and path not in (ARABIC, ARABIC_B):
        return _font(ARABIC_B if path in (SANS_SB, SERIF) else ARABIC, round(font.size * 1.1))
    if not arabic and path in (ARABIC, ARABIC_B):
        return _font(SANS, font.size)
    return font


def _hm(t: dt.datetime) -> str:
    return f"{t:%H} h {t:%M}"


class SceneRenderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.W, self.H = cfg.width, cfg.height
        self.dark = _hex(cfg.dark_green)
        # vert profond et digne, dérivé du vert de la mosquée
        self.deep = _mix(self.dark, (6, 18, 12), 0.62)
        self.deep_top = _mix(self.dark, (4, 12, 8), 0.74)
        self.panel = _mix(self.dark, (6, 18, 12), 0.55)
        self._logo = Image.open(cfg.logo).convert("RGBA")
        self._logo_cache: dict[int, Image.Image] = {}
        self._bg_cache: Optional[Image.Image] = None
        self._medal_cache: dict[tuple, Image.Image] = {}
        self.s = self.H / 720  # facteur d'échelle (maquette en 1280x720)

    def px(self, v: float) -> int:
        return int(round(v * self.s))

    # --- briques ------------------------------------------------------------
    def logo(self, size: int) -> Image.Image:
        """Logo carré (fond blanc) recadré en disque."""
        if size not in self._logo_cache:
            k = 4
            lg = self._logo.resize((size * k, size * k), Image.Resampling.LANCZOS)
            mask = Image.new("L", lg.size, 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size * k - 1, size * k - 1], fill=255)
            lg.putalpha(mask)
            self._logo_cache[size] = lg.resize((size, size), Image.Resampling.LANCZOS)
        return self._logo_cache[size]

    def medallion(self, d: int, ring: int = 2, pad: Optional[int] = None) -> Image.Image:
        """Disque blanc + logo + filet or, avec une ombre douce (taille d + marge d'ombre)."""
        pad = max(2, d // 30) if pad is None else pad
        key = (d, ring, pad)
        if key in self._medal_cache:
            return self._medal_cache[key]
        m = max(6, d // 12)  # marge pour l'ombre
        size = d + 2 * m
        k = 4
        big = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
        g = ImageDraw.Draw(big)
        o = m * k
        g.ellipse([o, o, o + d * k - 1, o + d * k - 1], fill=GOLD)
        r = ring * k
        g.ellipse([o + r, o + r, o + d * k - 1 - r, o + d * k - 1 - r], fill=(255, 255, 255, 255))
        img = big.resize((size, size), Image.Resampling.LANCZOS)
        inner = d - 2 * ring - 2 * pad
        img.alpha_composite(self.logo(inner), (m + ring + pad, m + ring + pad))
        shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse([m, m + max(1, d // 40), m + d, m + d + max(1, d // 40)], fill=(0, 0, 0, 120))
        shadow = shadow.filter(ImageFilter.GaussianBlur(m / 2))
        shadow.alpha_composite(img)
        self._medal_cache[key] = shadow
        return shadow

    def _bg(self) -> Image.Image:
        """Fond vert profond : dégradé vertical, lumière douce au centre, motif d'étoiles à 8 branches très discret."""
        if self._bg_cache is not None:
            return self._bg_cache.copy()
        W, H = self.W, self.H
        col = Image.new("RGBA", (1, H))
        for y in range(H):
            col.putpixel((0, y), _mix(self.deep_top, self.deep, y / (H - 1)))
        img = col.resize((W, H))
        # halo central (éclaire la zone de lecture), vignettage des bords
        rad = Image.radial_gradient("L").resize((W, H), Image.Resampling.BICUBIC)  # 0 au centre -> 255 aux bords
        light = Image.new("RGBA", (W, H), _mix(self.dark, (255, 255, 255), 0.05, 0))
        light.putalpha(rad.point([max(0, 70 - v // 2) for v in range(256)]))
        img.alpha_composite(light)
        dark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dark.putalpha(rad.point([max(0, min(110, v - 120)) for v in range(256)]))
        img.alpha_composite(dark)
        # motif géométrique (khatam) : presque invisible au centre, à peine perceptible sur les bords
        pat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        p = ImageDraw.Draw(pat)
        step = self.px(88)
        r = step * 0.36
        for gy in range(-1, H // step + 2):
            for gx in range(-1, W // step + 2):
                cx, cy = gx * step + (step // 2 if gy % 2 else 0), gy * step
                for rot in (0, 45):
                    pts = [(cx + r * math.cos(math.radians(rot + 45 + 90 * i)),
                            cy + r * math.sin(math.radians(rot + 45 + 90 * i))) for i in range(4)]
                    p.polygon(pts, outline=_alpha(GOLD, 255), width=1)
        pmask = rad.point([int(max(0, v - 90) * 0.11) for v in range(256)])
        pat.putalpha(Image.composite(pmask, Image.new("L", (W, H), 0), pat.getchannel("A")))
        img.alpha_composite(pat)
        self._bg_cache = img
        return img.copy()

    def _layout(self, d: ImageDraw.ImageDraw, text: str, font, **kw):
        """[(texte, police, options, largeur)] dans l'ordre visuel."""
        out = []
        for seg, ar in _runs(text):
            f = _run_font(font, ar)
            t, opts = _shape(seg)
            opts.update({k: v for k, v in kw.items() if v is not None})
            out.append((t, f, opts, d.textlength(t, font=f, **opts)))
        return out

    def _text(self, d: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor: str = "ms", **kw):
        """Texte (éventuellement mixte latin/arabe) ; ancres Pillow : l/m/r + s/m."""
        runs = self._layout(d, text, font, **kw)
        if len(runs) == 1:
            t, f, opts, _ = runs[0]
            d.text(xy, t, font=f, fill=fill, anchor=anchor, **opts)
            return
        x, y = xy
        total = sum(r[3] for r in runs)
        x -= {"l": 0, "m": total / 2, "r": total}[anchor[0]]
        if anchor[1] == "m":
            asc, desc = font.getmetrics()
            y += (asc - desc) / 2
        for t, f, opts, w in runs:
            d.text((x, y), t, font=f, fill=fill, anchor="ls", **opts)
            x += w

    def _width(self, d: ImageDraw.ImageDraw, text: str, font, **kw) -> float:
        return sum(r[3] for r in self._layout(d, text, font, **kw))

    def _tracked(self, d: ImageDraw.ImageDraw, cx: float, y: float, text: str, font, fill, track: float):
        """Petites capitales espacées, centrées sur cx (ligne de base y)."""
        ws = [d.textlength(ch, font=font) for ch in text]
        total = sum(ws) + track * (len(text) - 1)
        x = cx - total / 2
        for ch, w in zip(text, ws):
            d.text((x, y), ch, font=font, fill=fill, anchor="ls")
            x += w + track

    def _ornament(self, d: ImageDraw.ImageDraw, cx: float, y: float, half: int):
        """Filet or — losange — filet or."""
        g = self.px(14)
        d.line([(cx - half, y), (cx - g, y)], fill=GOLD_SOFT, width=max(1, self.px(1.5)))
        d.line([(cx + g, y), (cx + half, y)], fill=GOLD_SOFT, width=max(1, self.px(1.5)))
        r = self.px(5)
        d.polygon([(cx, y - r), (cx + r, y), (cx, y + r), (cx - r, y)], fill=GOLD)

    def _footer(self, img: Image.Image):
        d = ImageDraw.Draw(img)
        cx, y = self.W / 2, self.H - self.px(38)
        f_lat, f_ar = _font(SANS_SB, self.px(19)), _font(ARABIC, self.px(22))
        name = self.cfg.mosque_name.upper()
        track = self.px(2.2)
        w_lat = sum(d.textlength(ch, font=f_lat) for ch in name) + track * (len(name) - 1)
        ar = self.cfg.mosque_name_ar
        w_ar = self._width(d, ar, f_ar) if ar else 0
        gap = self.px(18)
        total = w_lat + (2 * gap + w_ar if ar else 0)
        x0 = cx - total / 2
        self._tracked(d, x0 + w_lat / 2, y, name, f_lat, IVORY_DIM, track)
        if ar:
            dot = x0 + w_lat + gap
            rr = self.px(2.5)
            d.ellipse([dot - rr, y - self.px(7) - rr, dot + rr, y - self.px(7) + rr], fill=GOLD)
            self._text(d, (x0 + w_lat + 2 * gap + w_ar / 2, y + self.px(1)), ar, f_ar, IVORY_DIM)

    def _ink(self, d: ImageDraw.ImageDraw, text: str, font, **kw) -> tuple[int, int]:
        """(hauteur au-dessus, sous la ligne de base) de l'encre réelle du texte."""
        up = down = 0
        for t, f, opts, _ in self._layout(d, text, font, **kw):
            b = d.textbbox((0, 0), t, font=f, anchor="ls", **opts)
            up, down = max(up, -b[1]), max(down, b[3])
        return up, down

    # médaillon des écrans pleins : position fixe (pas de saut d'une scène à l'autre)
    def _med_geom(self) -> tuple[int, int]:
        return self.px(80), self.px(158)   # (haut, diamètre)

    def background(self) -> Image.Image:
        """Fond commun des écrans pleins : dégradé + motif, médaillon, pied de page.

        Identique pour toutes les scènes pleines : c'est le calque « fond » de l'encodeur, fixe pendant
        les transitions (seul le bloc de textes change, voir _full_content)."""
        img = self._bg()
        med_top, med = self._med_geom()
        m = self.medallion(med)
        img.alpha_composite(m, (int(self.W / 2 - m.width / 2), int(med_top + med / 2 - m.height / 2)))
        self._footer(img)
        return img

    def _full_content(self, title: str, sub: str = "", big: str = "", foot: str = "", *,
                      eyebrow: str = "", arabic: str = "") -> Image.Image:
        """Bloc de textes d'un écran plein (calque transparent) : surtitre, titre, arabe, ornement,
        (compte à rebours), texte. Le médaillon et le pied de page sont dans background().

        Les blocs sont empilés d'après l'encre réelle des glyphes (espaces optiquement réguliers),
        puis centrés verticalement dans la zone utile au-dessus du pied de page."""
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        px = self.px
        tnum = {"features": ["tnum"]} if RAQM else {}
        # (genre, texte, police, couleur, espace avant, options)
        items: list[tuple] = [("medallion", None, None, None, 0, {})]
        if eyebrow:
            items.append(("eyebrow", eyebrow.upper(), _font(SANS_SB, px(20)), GOLD, px(34), {}))
        items.append(("text", title, self._fit(d, title, _font(SERIF, px(62)), SERIF, px(62)), IVORY,
                      px(24) if eyebrow else px(40), {}))
        if arabic:
            items.append(("text", arabic, _font(ARABIC, px(40)), GOLD, px(20), {}))
        items.append(("orn", None, None, None, px(28), {}))
        if big:
            items.append(("text", big, _font(SANS_SB, px(116)), IVORY, px(36), tnum))
        if sub:
            items.append(("text", sub, self._fit(d, sub, _font(SANS_SB, px(30)), SANS_SB, px(30)), IVORY,
                          px(28) if big else px(32), {}))
        if foot:
            items.append(("text", foot, self._fit(d, foot, _font(SANS, px(24)), SANS, px(24)), IVORY_DIM,
                          px(18) if sub else px(30), {}))
        med_top, med = self._med_geom()
        sized = []
        for kind, text, font, fill, gap, kw in items:
            if kind == "medallion":
                up, down = med, 0
            elif kind == "orn":
                up, down = px(5), px(5)
            else:
                up, down = self._ink(d, text, font, **kw)
            sized.append((kind, text, font, fill, gap, kw, up, down))
        # textes centrés sous le médaillon (dessiné à position fixe dans background())
        rest = sized[1:]
        total = sum(it[4] + it[6] + it[7] for it in rest) - rest[0][4]
        region_top, bottom_zone = med_top + med + px(30), self.H - px(84)  # pied de page sous bottom_zone
        y = region_top + max(0, (bottom_zone - region_top - total) * 0.42) - rest[0][4]
        cx = self.W / 2
        for kind, text, font, fill, gap, kw, up, down in rest:
            base = y + gap + up
            if kind == "orn":
                self._ornament(d, cx, base - up, px(110))
            elif kind == "eyebrow":
                self._tracked(d, cx, base, text, font, fill, px(3.2))
            else:
                self._text(d, (cx, base), text, font, fill, **kw)
            y = base + down
        return img

    def _fit(self, d, text: str, font, path: str, size: int, max_w: Optional[int] = None):
        """Réduit la taille si le texte dépasse la zone utile (marges de 10 %)."""
        max_w = max_w or int(self.W * 0.8)
        while size > 12 and self._width(d, text, font) > max_w:
            size -= 2
            font = _font(path, size)
        return font

    # --- scènes -------------------------------------------------------------
    # Chaque scène rend son calque de contenu (transparent) à partir de l'instant affiché (Moment).
    def _avant(self, m: "Moment") -> Image.Image:
        sub = f"Début à {_hm(m.planned)}" if m.planned else ""
        return self._full_content(self.cfg.title, sub, "", "Merci de patienter, la diffusion commence très bientôt.",
                                  eyebrow="Début imminent", arabic=AR_KHUTBA)

    def _priere(self, m: "Moment") -> Image.Image:
        return self._full_content("Prière en cours", "La prière n'est pas diffusée.", "",
                                  "La diffusion reprendra pour la prochaine khutba, in sha Allah.",
                                  eyebrow=french_date(m.now), arabic=AR_JUMUA)

    def _entracte(self, m: "Moment") -> Image.Image:
        if m.planned:
            remaining = max(0, int((m.planned - m.now).total_seconds()))
            mi, se = divmod(remaining, 60)
            return self._full_content(f"Prochaine khutba à {_hm(m.planned)}", "", f"{mi:02d}:{se:02d}",
                                      "Le live continue : restez connectés.", eyebrow="Entracte")
        return self._full_content("Entracte", "", "", "Le live continue : restez connectés.", arabic=AR_KHUTBA)

    def _termine(self, m: "Moment") -> Image.Image:
        return self._full_content("Khutba terminée", "Jazakoum Allahou khayran — qu'Allah vous récompense.", "",
                                  "Rendez-vous vendredi prochain, in sha Allah.",
                                  eyebrow="Fin de la diffusion", arabic=AR_JAZAKUM)

    def _panel_shadow(self, img: Image.Image, box, radius: int):
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        o = self.px(4)
        ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + o, box[2], box[3] + o], radius, fill=(0, 0, 0, 110))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(self.px(10))))

    def _khutba(self, m: "Moment") -> Image.Image:
        """Transparent + bandeau compact en bas à gauche : médaillon, titre, mosquée, titre arabe."""
        px = self.px
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f_s, f_ar = _font(SANS_SB, px(30)), _font(ARABIC_B, px(44))
        line2 = self.cfg.mosque_name
        h = px(124)
        x0, y1 = px(56), self.H - px(46)
        y0 = y1 - h
        med = px(92)
        pad = px(14)
        tx = x0 + px(5) + pad + med + px(18)
        sep = px(26)
        max_x1 = self.W - px(56)
        title = self.cfg.title
        f_t = self._fit(d, title, _font(SERIF, px(46)), SERIF, px(46), max_x1 - px(26) - tx)
        w_ar = self._width(d, AR_KHUTBA, f_ar)
        line2 = self._ellipsize(d, line2, f_s, max_x1 - px(26) - tx)
        w_text = max(self._width(d, title, f_t), self._width(d, line2, f_s))
        x1 = int(tx + w_text + sep + px(1) + sep + w_ar + px(26))
        if x1 > max_x1:  # sous-titre ou titre long : on renonce au titre arabe plutôt que de déborder
            x1 = int(min(max_x1, tx + w_text + px(26)))
        r = px(6)
        self._panel_shadow(img, (x0, y0, x1, y1), r)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x0, y0, x1, y1], r, fill=_alpha(self.panel, 244), corners=(False, True, True, False))
        d.rectangle([x0, y0, x0 + px(5) - 1, y1], fill=GOLD)  # filet or à gauche
        m = self.medallion(med, ring=max(1, px(1.5)))
        img.alpha_composite(m, (int(x0 + px(5) + pad + med / 2 - m.width / 2), int(y0 + h / 2 - m.height / 2)))
        d = ImageDraw.Draw(img)
        self._text(d, (tx, y0 + px(56)), title, f_t, IVORY, anchor="ls")
        self._text(d, (tx, y0 + px(96)), line2, f_s, IVORY_DIM, anchor="ls")
        sx = tx + w_text + sep
        if sx + sep + w_ar < x1:
            d.line([(sx, y0 + px(22)), (sx, y1 - px(22))], fill=GOLD_SOFT, width=max(1, px(1)))
            self._text(d, (x1 - px(26), y0 + h / 2 + px(4)), AR_KHUTBA, f_ar, GOLD, anchor="rm")
        return img

    def _ellipsize(self, d, text: str, font, max_w: float) -> str:
        if self._width(d, text, font) <= max_w:
            return text
        while text and self._width(d, text + "…", font) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def _janaza(self, m: "Moment") -> Image.Image:
        """Sobre : caméra minbar, pas de bandeau ; seule une petite mention discrète (nom du défunt si saisi)."""
        px = self.px
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f_l, f_ar = _font(SANS_SB, px(24)), _font(ARABIC, px(28))
        label = self._ellipsize(d, "Salat al-Janaza" + (f"  ·  {m.janaza_name}" if m.janaza_name else ""), f_l, self.W * 0.55)
        w_l, w_ar = self._width(d, label, f_l), self._width(d, AR_JANAZA, f_ar)
        gap = px(18)
        x0, y1, h = px(56), self.H - px(46), px(50)
        y0 = y1 - h
        x1 = int(x0 + px(20) + w_l + gap + px(1) + gap + w_ar + px(20))
        r = px(6)
        d.rounded_rectangle([x0, y0, x1, y1], r, fill=(8, 14, 11, 165))
        cy = y0 + h / 2
        self._text(d, (x0 + px(20), cy), label, f_l, IVORY, anchor="lm")
        sx = x0 + px(20) + w_l + gap
        d.line([(sx, y0 + px(14)), (sx, y1 - px(14))], fill=GOLD_SOFT, width=max(1, px(1)))
        self._text(d, (x1 - px(20), cy + px(3)), AR_JANAZA, f_ar, IVORY_DIM, anchor="rm")
        return img

    def content(self, scene: str, m: "Moment") -> Image.Image:
        """Calque de contenu de la scène ; l'encodeur le pose sur le fond commun si la scène est un écran plein."""
        return SCENES[scene].draw(self, m)


@dataclass(frozen=True)
class Moment:
    """Ce que montrent les scènes : l'heure, la session prévue, le nom du défunt (Janaza, avec l'accord de la famille)."""
    now: dt.datetime
    planned: Optional[dt.datetime] = None
    janaza_name: Optional[str] = None


@dataclass(frozen=True)
class Scene:
    full: bool                  # écran plein : le fond commun masque la caméra
    mic: bool                   # micro du direct ouvert
    draw: Callable[[SceneRenderer, Moment], Image.Image]
    ticking: bool = False       # le contenu change avec l'heure (compte à rebours) : rendu à chaque seconde


SCENES = {
    "avant": Scene(full=True, mic=False, draw=SceneRenderer._avant),
    "khutba": Scene(full=False, mic=True, draw=SceneRenderer._khutba),
    "priere": Scene(full=True, mic=False, draw=SceneRenderer._priere),
    "entracte": Scene(full=True, mic=False, draw=SceneRenderer._entracte, ticking=True),
    "termine": Scene(full=True, mic=False, draw=SceneRenderer._termine),
    "janaza": Scene(full=False, mic=True, draw=SceneRenderer._janaza),
}
