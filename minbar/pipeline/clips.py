"""Clips verticaux 9:16 sous-titrés (ASS incrusté), recadrage centre, sans musique.  Tous marqués « à valider »."""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

FONTS = str(Path(__file__).resolve().parents[2] / "assets" / "fonts")

from .arabic import arabic_ratio
from .notes import fmt_t

MIN_S, MAX_S = 30.0, 90.0
FONT_AR = "DejaVu Sans"


def select_windows(segments: list[dict], matches: list[dict], duration: float, quotes: list[tuple] | None = None, n: int = 3) -> list[dict]:
    """1 à n fenêtres : (1) meilleur verset + commentaire, (2) citation marquante, (3) conclusion."""
    cands: list[dict] = []

    def snap(t0: float, t1: float) -> tuple[float, float]:
        s = [x for x in segments if x["end"] > t0 - 0.5 and x["start"] < t1 + 0.5]
        if not s:
            return t0, t1
        a, b = max(0.0, s[0]["start"] - 0.2), s[-1]["end"] + 0.3
        # borne de durée
        if b - a > MAX_S:
            keep = [x for x in s if x["end"] - a <= MAX_S]
            b = keep[-1]["end"] + 0.3 if keep else a + MAX_S
        return a, min(b, duration)

    def add(kind, t0, t1, why):
        a, b = snap(t0, t1)
        if b - a < MIN_S:  # étend vers l'avant
            b = min(duration, a + MIN_S + 5)
            a2, b2 = snap(a, b)
            a, b = a2, max(b2, a + MIN_S)
        if b - a >= MIN_S - 1 and all(abs(a - c["start"]) > 20 for c in cands):
            cands.append({"kind": kind, "start": round(a, 2), "end": round(b, 2), "why": why})

    matches = [m for m in matches if m.get("confidence", "sûr") in ("sûr", "court")]
    good = [m for m in matches if m["coverage"] >= 0.45] or matches
    if good:
        m = max(good, key=lambda x: (x["score"] * min(1.0, x["coverage"] + 0.2), x["end"] - x["start"]))
        add("verset", m["start"] - 12, m["start"] + 55, f"verset {m['ref']} ({m['surah_name']}) récité, avec le contexte qui précède")
    if quotes:
        for t, q in quotes:
            if 60 < t < duration - 120:
                add("citation", t - 3, t + 50, f"citation marquante : « {q[:90]} »")
                break
    add("conclusion", duration - 70, duration, "conclusion / invocation finale")
    if len(cands) < n:  # compléter par le passage le plus dense en français
        frs = [s for s in segments if arabic_ratio(s["text"]) < 0.3]
        if frs:
            best = None
            for s in frs[:: max(1, len(frs) // 40)]:
                w = [x for x in frs if s["start"] <= x["start"] < s["start"] + 55]
                sc = sum(len(x["text"]) for x in w)
                if best is None or sc > best[0]:
                    best = (sc, s["start"])
            if best:
                add("passage", best[1], best[1] + 55, "passage le plus dense de la khutba")
    return cands[:n]


def _ass_time(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def build_ass(segments: list[dict], matches: list[dict], t0: float, t1: float, path: str, verse_fr: bool = True) -> int:
    """Sous-titres FR ; pour un segment arabe apparié à un verset : texte coranique exact (haut) + traduction (bas)."""
    hdr = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # tout en bas (visage libre) ; Amiri pour l'arabe (signes de pause coraniques ۚ ۖ), Source Sans 3 pour le français
        "Style: FR,Source Sans 3 Semibold,64,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,70,70,380,1\n"
        "Style: AR,Amiri,150,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,380,1\n"
        "Style: TR,Source Sans 3,46,&H00D8E8DC,&H000000FF,&H00101010,&H80000000,0,1,0,0,100,100,0,0,1,3,1,2,80,80,380,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    ev, n = [], 0
    matches = [m for m in matches if m.get("confidence", "sûr") in ("sûr", "court")]
    for s in segments:
        if s["end"] <= t0 or s["start"] >= t1:
            continue
        a, b = max(s["start"], t0) - t0, min(s["end"], t1) - t0
        text = s["text"].strip()
        if arabic_ratio(text) > 0.5:
            hit = next((m for m in matches if m["start"] - 0.5 <= s["start"] <= m["end"] + 0.5), None)
            if hit:
                ar = hit["verse_text"]
                # un seul événement : verset arabe puis traduction dessous, empilés en bas
                txt = "{\\q2}" + "\\N".join(_wrap(ar, 26))
                if verse_fr and hit["verse_fr"]:
                    txt += "\\N{\\rTR}« " + "\\N".join(_wrap(hit["verse_fr"], 44)) + f" » ({hit['ref']})"
                ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},AR,,0,0,0,,{txt}")
                n += 1
            else:
                ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},AR,,0,0,0,,{{\\q2}}" + "\\N".join(_wrap(text, 26)))
                n += 1
            continue
        # texte français : découpé en tranches d'environ 2 lignes, durée au prorata des mots
        chunks, words, cur = [], text.split(), []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 56:
                chunks.append(" ".join(cur)); cur = []
        if cur:
            chunks.append(" ".join(cur))
        tot = max(1, sum(len(c.split()) for c in chunks))
        t = a
        for c in chunks:
            d = (b - a) * len(c.split()) / tot
            ev.append(f"Dialogue: 0,{_ass_time(t)},{_ass_time(t + d)},FR,,0,0,0,,{{\\q2}}" + "\\N".join(_wrap(c, 28)))
            t += d
            n += 1
    Path(path).write_text(hdr + "\n".join(ev) + "\n", encoding="utf-8")
    return n


def render_clip(src: str, rec_offset: float, w: dict, ass: str, out: str, log=print) -> None:
    """Recadrage centre 9:16 (1080x1920), sous-titres brûlés, sans musique (audio d'origine)."""
    a, dur = rec_offset + w["start"], w["end"] - w["start"]
    vf = f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920:flags=lanczos,setsar=1,subtitles={ass}:fontsdir={FONTS}"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", str(Path(src).resolve()),
           "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-af", "afade=t=in:d=0.3,afade=t=out:st=%.2f:d=0.5" % max(0, dur - 0.5), "-movflags", "+faststart", str(Path(out).resolve())]
    subprocess.run(cmd, check=True, cwd=str(Path(out).resolve().parent))
    log(f"clip -> {out} ({dur:.0f}s)")
