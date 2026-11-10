"""Live scenes (SCENES) and their rendering (1280x720 RGBA layers) with Pillow, without OBS.

The stream is composed like this: camera + shared background of the full screens + scene content (encoder.py).
 - full screens (avant, priere, entracte, termine): the background masks the camera, the content is the text block;
 - khutba: banner at the bottom left; janaza: sober full screen (the prayer can be heard, the image is
   replaced). The microphone is open on these two scenes only.

Charter: deep green (derived from cfg.dark_green), matte gold as accent, ivory text; titles in
EB Garamond, body text in Source Sans 3, Arabic in Amiri (OFL fonts in assets/fonts).
Arabic goes through libraqm (shaping of joined letters + RTL direction); otherwise arabic_reshaper
+ python-bidi if they are installed. All the useful content stays in a central safe area
(the overlays of the mobile Facebook app occupy the corners and the bottom of the image).
"""
from __future__ import annotations
import datetime as dt
import functools
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import screens
from .paths import FONTS
from .text import JOURS, french_date, shaped, is_arabic_char, RAQM

if TYPE_CHECKING:                       # for annotations only
    from .events import EventInstance

SERIF = str(FONTS / "EBGaramond-SemiBold.ttf")
SANS = str(FONTS / "SourceSans3-Regular.ttf")
SANS_SB = str(FONTS / "SourceSans3-Semibold.ttf")
ARABIC = str(FONTS / "Amiri-Regular.ttf")
ARABIC_B = str(FONTS / "Amiri-Bold.ttf")

# --- palette ------------------------------------------------------------------
IVORY = (244, 240, 230, 255)
IVORY_DIM = (214, 210, 198, 255)
GOLD = (200, 169, 106, 255)          # matte gold, never saturated
GOLD_SOFT = (200, 169, 106, 150)

# --- fixed texts (verified Arabic) -------------------------------------------
AR_KHUTBA = "خطبة الجمعة"
AR_JUMUA = "صلاة الجمعة"
AR_JAZAKUM = "جزاكم الله خيرا"
AR_JANAZA = "صلاة الجنازة"
AR_DARS = "درس"
AR_CONF = "محاضرة"


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
    """True = Arabic, False = Latin (strong), None = neutral (space, punctuation, digits)."""
    if is_arabic_char(ch) and ch.isalpha():
        return True
    if ch.isalpha():
        return False
    return None


def _runs(text: str) -> list[tuple[str, bool]]:
    """Split into single-script segments, in VISUAL order (left -> right).

    Neutrals between two Arabic letters stay in the Arabic segment; those at the edges
    go to the base context. RTL paragraph if the first strong letter is Arabic."""
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
                runs[-1][0] += seg  # neutrals between two Arabic segments
            else:
                runs.append([seg, False])
            i = j
    runs = [(t, ar) for t, ar in runs if t]
    if base_rtl:
        runs.reverse()
    return runs


def _run_font(font, arabic: bool):
    """Font suited to the segment: Amiri for Arabic, Source Sans for Latin."""
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
        # deep and dignified green, derived from the mosque green
        self.deep = _mix(self.dark, (6, 18, 12), 0.62)
        self.deep_top = _mix(self.dark, (4, 12, 8), 0.74)
        self.panel = _mix(self.dark, (6, 18, 12), 0.55)
        self._logo = Image.open(cfg.logo).convert("RGBA") if cfg.logo else None
        self._logo_cache: dict[int, Image.Image] = {}
        self._bg_cache: Optional[Image.Image] = None
        self._medal_cache: dict[tuple, Image.Image] = {}
        self.s = self.H / 720  # scale factor (mockup at 1280x720)

    def px(self, v: float) -> int:
        return int(round(v * self.s))

    # --- building blocks ----------------------------------------------------
    def logo(self, size: int) -> Image.Image:
        """Square logo (white background) cropped into a disc; without a mosque logo: neutral emblem (crescent + star)."""
        if size not in self._logo_cache:
            if self._logo is None:
                self._logo_cache[size] = self._emblem(size)
                return self._logo_cache[size]
            k = 4
            lg = self._logo.resize((size * k, size * k), Image.Resampling.LANCZOS)
            mask = Image.new("L", lg.size, 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size * k - 1, size * k - 1], fill=255)
            lg.putalpha(mask)
            self._logo_cache[size] = lg.resize((size, size), Image.Resampling.LANCZOS)
        return self._logo_cache[size]

    def _emblem(self, size: int) -> Image.Image:
        """Generic drawn emblem (crescent + star) as long as no logo is configured."""
        k = 4
        s = size * k
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        g = ImageDraw.Draw(img)
        pale = _mix(self.dark, (255, 255, 255), 0.86)          # almost white disc, tinted with the mosque green
        g.ellipse([0, 0, s - 1, s - 1], fill=pale)
        area = Image.new("L", (s, s), 0)
        ga = ImageDraw.Draw(area)
        R = s * 0.58
        ga.ellipse([s / 2 - R, s / 2 - R, s / 2 + R, s / 2 + R], fill=255)          # full disc
        rb, bx, by = s * 0.58, s * 0.70, s * 0.30                                   # bite of the same radius, shifted up-right
        ga.ellipse([bx - rb, by - rb, bx + rb, by + rb], fill=0)
        img.paste(self.dark, (0, 0), area)                                          # green crescent
        # four-branch gold star, in the hollow of the crescent
        sx, sy, e = s * 0.66, s * 0.36, s * 0.085
        g.polygon([(sx, sy - e), (sx + e * 0.45, sy), (sx, sy + e), (sx - e * 0.45, sy)], fill=GOLD)
        g.polygon([(sx - e * 0.8, sy), (sx, sy - e * 0.42), (sx + e * 0.8, sy), (sx, sy + e * 0.42)], fill=GOLD)
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
        img.putalpha(mask)
        return img.resize((size, size), Image.Resampling.LANCZOS)

    def medallion(self, d: int, ring: int = 2, pad: Optional[int] = None) -> Image.Image:
        """White disc + logo + gold ring, with a soft shadow (size d + shadow margin)."""
        pad = max(2, d // 30) if pad is None else pad
        key = (d, ring, pad)
        if key in self._medal_cache:
            return self._medal_cache[key]
        m = max(6, d // 12)  # margin for the shadow
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
        """Deep green background: vertical gradient, soft light in the centre, very discreet 8-branch star pattern."""
        if self._bg_cache is not None:
            return self._bg_cache.copy()
        W, H = self.W, self.H
        col = Image.new("RGBA", (1, H))
        for y in range(H):
            col.putpixel((0, y), _mix(self.deep_top, self.deep, y / (H - 1)))
        img = col.resize((W, H))
        # central halo (lights the reading area), edge vignetting
        rad = Image.radial_gradient("L").resize((W, H), Image.Resampling.BICUBIC)  # 0 at the centre -> 255 at the edges
        light = Image.new("RGBA", (W, H), _mix(self.dark, (255, 255, 255), 0.05, 0))
        light.putalpha(rad.point([max(0, 70 - v // 2) for v in range(256)]))
        img.alpha_composite(light)
        dark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dark.putalpha(rad.point([max(0, min(110, v - 120)) for v in range(256)]))
        img.alpha_composite(dark)
        # geometric pattern (khatam): almost invisible in the centre, barely perceptible at the edges
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
        """[(text, font, options, width)] in visual order."""
        out = []
        for seg, ar in _runs(text):
            f = _run_font(font, ar)
            t, opts = shaped(seg)
            opts.update({k: v for k, v in kw.items() if v is not None})
            out.append((t, f, opts, d.textlength(t, font=f, **opts)))
        return out

    def _text(self, d: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor: str = "ms", **kw):
        """Text (possibly mixed Latin/Arabic); Pillow anchors: l/m/r + s/m."""
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
        """Small spaced capitals, centred on cx (baseline y)."""
        ws = [d.textlength(ch, font=font) for ch in text]
        total = sum(ws) + track * (len(text) - 1)
        x = cx - total / 2
        for ch, w in zip(text, ws):
            d.text((x, y), ch, font=font, fill=fill, anchor="ls")
            x += w + track

    def _ornament(self, d: ImageDraw.ImageDraw, cx: float, y: float, half: int):
        """Gold rule — diamond — gold rule."""
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
        """(height above, below the baseline) of the text's actual ink."""
        up = down = 0
        for t, f, opts, _ in self._layout(d, text, font, **kw):
            b = d.textbbox((0, 0), t, font=f, anchor="ls", **opts)
            up, down = max(up, -b[1]), max(down, b[3])
        return up, down

    def txt(self, screen: str, m: "Moment", **variables) -> dict[str, str]:
        """Texts of a screen for this instant: defaults of minbar.screens, mosque overrides, variables."""
        vals = screens.variables(m, title=self.cfg.title, mosque=self.cfg.mosque_name, **variables)
        return screens.resolve(screen, getattr(self.cfg, "screen_texts", None), vals)

    # medallion of the full screens: fixed position (no jump from one scene to another)
    def _med_geom(self) -> tuple[int, int]:
        return self.px(80), self.px(158)   # (top, diameter)

    def background(self) -> Image.Image:
        """Shared background of the full screens: gradient + pattern, medallion, footer.

        Identical for all full scenes: it is the encoder's "background" layer, fixed during
        transitions (only the text block changes, see _full_content)."""
        img = self._bg()
        med_top, med = self._med_geom()
        m = self.medallion(med)
        img.alpha_composite(m, (int(self.W / 2 - m.width / 2), int(med_top + med / 2 - m.height / 2)))
        self._footer(img)
        return img

    def _full_content(self, title: str, sub: str = "", big: str = "", foot: str = "", *,
                      eyebrow: str = "", arabic: str = "") -> Image.Image:
        """Text block of a full screen (transparent layer): eyebrow, title, Arabic, ornament,
        (countdown), text. The medallion and the footer are in background().

        The blocks are stacked according to the actual ink of the glyphs (optically regular spacing),
        then centred vertically in the usable area above the footer."""
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        px = self.px
        tnum = {"features": ["tnum"]} if RAQM else {}
        # (kind, text, font, colour, gap before, options)
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
        # texts centred under the medallion (drawn at a fixed position in background())
        rest = sized[1:]
        total = sum(it[4] + it[6] + it[7] for it in rest) - rest[0][4]
        region_top, bottom_zone = med_top + med + px(30), self.H - px(84)  # footer below bottom_zone
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
        """Shrinks the size if the text overflows the usable area (10 % margins)."""
        max_w = max_w or int(self.W * 0.8)
        while size > 12 and self._width(d, text, font) > max_w:
            size -= 2
            font = _font(path, size)
        return font

    # --- scenes -------------------------------------------------------------
    # Each scene renders its content layer (transparent) from the displayed instant (Moment).
    def _avant(self, m: "Moment") -> Image.Image:
        t = self.txt("avant", m, heure=_hm(m.planned) if m.planned else "")
        return self._full_content(self.cfg.title, t["sub"], "", t["foot"],
                                  eyebrow=t["eyebrow"], arabic=AR_KHUTBA)

    def _priere(self, m: "Moment") -> Image.Image:
        t = self.txt("priere", m)
        return self._full_content(t["title"], t["sub"], "", t["foot"],
                                  eyebrow=t["eyebrow"] or (french_date(m.now) if m.now else ""),
                                  arabic=AR_JUMUA)

    def _entracte(self, m: "Moment") -> Image.Image:
        nxt = m.next_event
        t = self.txt("entracte", m, heure=_hm(nxt.starts) if nxt is not None and nxt.kind != "khutba"
                     else (_hm(m.planned) if m.planned else ""),
                     cours=getattr(nxt, "title", "") if nxt is not None and nxt.kind != "khutba" else "")
        if nxt is not None and nxt.kind != "khutba":
            remaining = max(0, int((nxt.starts - m.now).total_seconds()))
            mi, se = divmod(remaining, 60)
            return self._full_content(t["title_cours"] or t["title"], nxt.title, f"{mi:02d}:{se:02d}",
                                      t["foot"], eyebrow=t["eyebrow"])
        if m.planned:
            remaining = max(0, int((m.planned - m.now).total_seconds()))
            mi, se = divmod(remaining, 60)
            return self._full_content(t["title_khutba"] or t["title"], "", f"{mi:02d}:{se:02d}",
                                      t["foot"], eyebrow=t["eyebrow"])
        return self._full_content(t["title"], "", "", t["foot"], eyebrow=t["eyebrow"], arabic=AR_KHUTBA)

    def _termine(self, m: "Moment") -> Image.Image:
        t = self.txt("termine", m)
        return self._full_content(t["title"], t["sub"], "", t["foot"],
                                  eyebrow=t["eyebrow"], arabic=AR_JAZAKUM)

    def _panel_shadow(self, img: Image.Image, box, radius: int):
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        o = self.px(4)
        ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + o, box[2], box[3] + o], radius, fill=(0, 0, 0, 110))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(self.px(10))))

    def _khutba(self, m: "Moment") -> Image.Image:
        """Khutba banner: meeting title, mosque name, Arabic title."""
        return self._banner(self.cfg.title, self.cfg.mosque_name, AR_KHUTBA)

    def _dars(self, m: "Moment") -> Image.Image:
        """Banner of a dars or a conference: title, speaker, series and session."""
        ev = m.event
        if ev is None:
            return self._banner("Dars", self.cfg.mosque_name, AR_DARS)
        bits = [self.cfg.mosque_name] if self.cfg.mosque_name else []
        if ev.get("speaker"):
            bits.append(ev["speaker"])
        no = ev.get("session_no")
        if ev.get("series"):
            bits.append(ev["series"] + (f" · séance {no}" if no else ""))
        arabic = AR_CONF if ev.get("kind") == "conference" else AR_DARS
        return self._banner(ev.get("title") or "Dars", "  ·  ".join(bits), arabic)

    def _dars_fin(self, m: "Moment") -> Image.Image:
        """End screen of a dars: thanks and announcement of the next class."""
        nxt = m.next_event
        t = self.txt("dars_fin", m,
                     jour=JOURS[nxt.starts.weekday()] if nxt else "",
                     heure=_hm(nxt.starts) if nxt else "",
                     cours=nxt.title if nxt else "")
        foot = (t["foot_cours"] or t["foot"]) if nxt else t["foot"]
        return self._full_content(t["title"], t["sub"], "", foot,
                                  eyebrow=t["eyebrow"], arabic=AR_DARS)

    def _banner(self, title: str, line2: str, arabic: str) -> Image.Image:
        """Transparent + compact banner at the bottom left: medallion, title, 2nd line, Arabic title."""
        px = self.px
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f_s, f_ar = _font(SANS_SB, px(30)), _font(ARABIC_B, px(44))
        h = px(124)
        x0, y1 = px(56), self.H - px(46)
        y0 = y1 - h
        med = px(92)
        pad = px(14)
        tx = x0 + px(5) + pad + med + px(18)
        sep = px(26)
        max_x1 = self.W - px(56)
        f_t = self._fit(d, title, _font(SERIF, px(46)), SERIF, px(46), max_x1 - px(26) - tx)
        w_ar = self._width(d, arabic, f_ar)
        line2 = self._ellipsize(d, line2, f_s, max_x1 - px(26) - tx)
        w_text = max(self._width(d, title, f_t), self._width(d, line2, f_s))
        x1 = int(tx + w_text + sep + px(1) + sep + w_ar + px(26))
        if x1 > max_x1:  # subtitle or long title: we give up the Arabic title rather than overflow
            x1 = int(min(max_x1, tx + w_text + px(26)))
        r = px(6)
        self._panel_shadow(img, (x0, y0, x1, y1), r)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x0, y0, x1, y1], r, fill=_alpha(self.panel, 244), corners=(False, True, True, False))
        d.rectangle([x0, y0, x0 + px(5) - 1, y1], fill=GOLD)  # gold rule on the left
        m = self.medallion(med, ring=max(1, px(1.5)))
        img.alpha_composite(m, (int(x0 + px(5) + pad + med / 2 - m.width / 2), int(y0 + h / 2 - m.height / 2)))
        d = ImageDraw.Draw(img)
        self._text(d, (tx, y0 + px(56)), title, f_t, IVORY, anchor="ls")
        self._text(d, (tx, y0 + px(96)), line2, f_s, IVORY_DIM, anchor="ls")
        sx = tx + w_text + sep
        if sx + sep + w_ar < x1:
            d.line([(sx, y0 + px(22)), (sx, y1 - px(22))], fill=GOLD_SOFT, width=max(1, px(1)))
            self._text(d, (x1 - px(26), y0 + h / 2 + px(4)), arabic, f_ar, GOLD, anchor="rm")
        return img

    def _ellipsize(self, d, text: str, font, max_w: float) -> str:
        if self._width(d, text, font) <= max_w:
            return text
        while text and self._width(d, text + "…", font) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def _janaza(self, m: "Moment") -> Image.Image:
        """Full screen dedicated to the funeral prayer: sober, without camera or banner.

        The record entered in the dashboard appears only if it was (family consent):
        name as title, note as second line. Otherwise the card stays generic. The mic stays open:
        the prayer can be heard, the image is replaced.
        """
        t = self.txt("janaza", m)
        name, info = m.janaza_name, m.janaza_info
        sub = (info or t["sub"]) if name else t["sub"]
        return self._full_content(name or t["title"], sub, "", t["foot"],
                                  eyebrow=t["eyebrow"], arabic=AR_JANAZA)

    def content(self, scene: str, m: "Moment") -> Image.Image:
        """Content layer of the scene; the encoder lays it over the shared background if the scene is a full screen."""
        return SCENES[scene].draw(self, m)


@dataclass(frozen=True)
class Moment:
    """What the scenes show: the time, the planned session, the name of the deceased (Janaza, with the family's
    consent), and for a dars the current class (`event`) or the next one (`next_event`)."""
    now: dt.datetime
    planned: Optional[dt.datetime] = None
    janaza_name: Optional[str] = None
    janaza_info: Optional[str] = None   # note under the name (age, standing…), same family consent
    event: Optional[dict] = None        # dars / conference in progress: title, speaker, series, session_no, kind
    next_event: Optional["EventInstance"] = None   # next occurrence, for the end screen and the interlude


@dataclass(frozen=True)
class Scene:
    full: bool                  # full screen: the shared background masks the camera
    mic: bool                   # live microphone open
    draw: Callable[[SceneRenderer, Moment], Image.Image]
    ticking: bool = False       # the content changes with the time (countdown): rendered every second


SCENES = {
    "avant": Scene(full=True, mic=False, draw=SceneRenderer._avant),
    "khutba": Scene(full=False, mic=True, draw=SceneRenderer._khutba),
    "priere": Scene(full=True, mic=False, draw=SceneRenderer._priere),
    "entracte": Scene(full=True, mic=False, draw=SceneRenderer._entracte, ticking=True),
    "termine": Scene(full=True, mic=False, draw=SceneRenderer._termine),
    "janaza": Scene(full=True, mic=True, draw=SceneRenderer._janaza),
    "dars": Scene(full=False, mic=True, draw=SceneRenderer._dars),
    "dars_fin": Scene(full=True, mic=False, draw=SceneRenderer._dars_fin),
}
