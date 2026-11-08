"""Clips verticaux 9:16 (1080x1920) sous-titrés en FRANÇAIS SEUL, sans musique. Tous marqués « à valider ».

Rendu (ASS incrusté par libass) :
- Parole : 1 à 2 lignes courtes, Montserrat ExtraBold blanc, contour sombre doux, mot prononcé en or
  (horodatage par mot de Whisper quand il existe ; sinon, et pour l'arabe traduit, interpolé au prorata des caractères).
  Placées dans le tiers inférieur, au-dessus des zones d'interface TikTok/Reels/Shorts (bas du texte à SAFE_BOTTOM px du bas).
- Verset : carte distincte (panneau sombre arrondi) : « SOURATE … · s:v » en capitales or, UNE ligne d'arabe propre
  (sans signes de pause U+06D6–U+06ED, seulement si elle tient sur une ligne), traduction Hamidullah en italique
  (EB Garamond). Pendant la carte, pas de sous-titre de parole.
- Segments arabes non appariés : traduction française (translate.py) ; jamais d'arabe brut dans les sous-titres.
- Discret en-tête les premières secondes (khutba, mosquée, date) ; voile dégradé en bas pour la lisibilité.
"""
from __future__ import annotations
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from ..paths import FONTS
from ..text import AR_RUN, arabic_ratio, is_arabic
from .translate import segment_fr, verse_fr
from .verses import match_for, recited_arabic, shown

DEFAULT_TITLE = "Khutba du vendredi"

MIN_S, MAX_S = 30.0, 90.0
W, H = 1080, 1920

# --- mise en page (pixels, repère 1080x1920) ---------------------------------------------------
SAFE_BOTTOM = 430          # bas du texte de parole : au-dessus de la légende / des boutons TikTok
SUB_SIZE = 100             # taille ASS (≈ 82 px de cadratin)
SUB_MAXW = 920             # largeur max d'une ligne de sous-titre
SUB_CHUNK_CHARS = 26       # au-delà : nouvelle « page » de sous-titre
CARD_BOTTOM = 1920 - 390
CARD_W = 980
GOLD = "&H0058C8F2&"       # #F2C858 (BGR)
WHITE = "&H00FFFFFF&"
WEAK_END = set("de du des la le les l' d' un une et à au aux en que qui qu' pour par sur dans avec sans son sa ses ce cette ces "
               "ne n' se s' leur nos vos mon ma mes ton ta tes notre votre il elle ils elles nous vous on je j' tu y est".split())


@lru_cache(maxsize=None)
def _font(name: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS / name), size)


@lru_cache(maxsize=None)
def _ass_ratio(name: str) -> float:
    """libass dimensionne la police sur sa hauteur de ligne (ascent+descent), pas sur le cadratin :
    taille ASS = taille em * ratio (Montserrat 1,22 ; Noto Naskh 1,70 ; EB Garamond 1,31)."""
    a, d = _font(name, 1000).getmetrics()
    return (a + d) / 1000


def text_width(text: str, font_file: str, ass_size: float) -> float:
    """Largeur en pixels d'un texte rendu par libass à la taille ASS donnée."""
    return _font(font_file, max(1, round(ass_size / _ass_ratio(font_file)))).getlength(text)


def _sub_w(t: str) -> float:
    return text_width(t, "Montserrat-ExtraBold.ttf", SUB_SIZE)


# --------------------------------------------------------------------------- fenêtres

def select_windows(segments: list[dict], matches: list[dict], duration: float, quotes: list[tuple] | None = None, n: int = 3) -> list[dict]:
    """1 à n fenêtres : (1) meilleur verset + commentaire, (2) citation marquante, (3) conclusion (fin de la PAROLE)."""
    cands: list[dict] = []
    if segments:  # une session finit par le silence qui a déclenché l'arrêt : la conclusion = fin de la parole
        duration = min(duration, segments[-1]["end"] + 1.0)

    def snap(t0: float, t1: float) -> tuple[float, float]:
        s = [x for x in segments if x["end"] > t0 - 0.5 and x["start"] < t1 + 0.5]
        if not s:
            return t0, t1
        a, b = max(0.0, s[0]["start"] - 0.2), s[-1]["end"] + 0.3
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

    vms = shown(matches)
    good = [m for m in vms if m["coverage"] >= 0.45] or vms
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


# --------------------------------------------------------------------------- texte

def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


PROPER = {"coran": "Coran", "allah": "Allah", "ramadan": "Ramadan", "mecque": "Mecque", "médine": "Médine"}


def clean_fr(t: str) -> str:
    t = AR_RUN.sub("", t)  # jamais d'arabe dans les sous-titres de parole
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.;:!?…])", r"\1", t)
    t = re.sub(r"\b(" + "|".join(PROPER) + r")\b", lambda m: PROPER[m.group(1)], t)  # Whisper : « coran », « allah »
    return t.replace("'", "’")


def interpolate_words(text: str, a: float, b: float) -> list[dict]:
    """Mots + horodatage interpolé au prorata des caractères (faute d'horodatage par mot)."""
    words = clean_fr(text).split()
    if not words or b <= a:
        return []
    weights = [len(w) + 1.5 for w in words]
    tot, t, out = sum(weights), a, []
    for w, k in zip(words, weights):
        d = (b - a) * k / tot
        out.append({"w": w, "s": t, "e": t + d})
        t += d
    return out


def chunk_words(words: list[dict], max_chars: int = SUB_CHUNK_CHARS) -> list[list[dict]]:
    """Découpe en « pages » courtes : ponctuation, pauses, longueur ; ne termine pas sur un petit mot faible."""
    chunks, cur = [], []
    for w in words:
        if cur:
            cur_txt = " ".join(x["w"] for x in cur)
            length = len(cur_txt) + 1 + len(w["w"])
            gap = w["s"] - cur[-1]["e"]
            prev = cur[-1]["w"]
            strong = prev[-1:] in ".!?;:…" and len(cur_txt) >= 10
            soft = prev[-1:] == "," and len(cur_txt) >= 18
            if length > max_chars or gap > 0.7 or strong or soft:
                carry = []
                if length > max_chars and not strong and not soft and gap <= 0.7:
                    # ne pas finir une page sur « de », « la », « et »… : ces mots passent à la page suivante
                    while len(cur) > 1 and len(carry) < 2 and cur[-1]["w"].lower() in WEAK_END:
                        carry.insert(0, cur.pop())
                chunks.append(cur); cur = carry
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def split_lines(words: list[str]) -> list[list[str]]:
    """1 ligne si courte, sinon 2 lignes équilibrées (coupure évitant les mots faibles en fin de ligne)."""
    full = " ".join(words)
    if len(words) < 2 or (_sub_w(full) <= SUB_MAXW and len(full) <= 18):
        return [words]
    best = None
    for k in range(1, len(words)):
        w1, w2 = _sub_w(" ".join(words[:k])), _sub_w(" ".join(words[k:]))
        over = max(0, w1 - SUB_MAXW) + max(0, w2 - SUB_MAXW)
        pen = (abs(w1 - w2) + over * 10 + (120 if words[k - 1].lower() in WEAK_END else 0)
               - (160 if words[k - 1][-1:] in ",;:.!?" else 0))   # couper après la ponctuation
        if best is None or pen < best[0]:
            best = (pen, k)
    k = best[1]
    return [words[:k], words[k:]]


def _speech_events(words: list[dict], hide: list[tuple[float, float]], t_end: float) -> list[str]:
    """Une page = 1-2 lignes ; un événement par mot actif (mot en or), apparition douce à chaque page."""
    ev = []
    chunks = chunk_words(words)
    for ci, ch in enumerate(chunks):
        a = ch[0]["s"]
        nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else t_end
        b = min(nxt, max(ch[-1]["e"] + 0.4, a + 1.0), t_end)
        if b - a < 0.25 or any(x0 - 0.1 < (a + b) / 2 < x1 + 0.1 for x0, x1 in hide):
            continue
        toks = [w["w"] for w in ch]
        lines = split_lines(toks)
        brk = len(lines[0]) if len(lines) > 1 else -1
        for i in range(len(ch)):
            s = a if i == 0 else ch[i]["s"]
            e = b if i == len(ch) - 1 else ch[i + 1]["s"]
            if e - s < 0.02:
                continue
            out = ""
            for j, tok in enumerate(toks):
                if j:
                    out += "\\N" if j == brk else " "
                t = _esc(tok)
                out += f"{{\\c{GOLD}}}{t}{{\\c{WHITE}}}" if j == i else t
            tag = "{\\fad(80,0)\\blur0.6}" if i == 0 else "{\\blur0.6}"
            ev.append(f"Dialogue: 1,{_ass_time(s)},{_ass_time(e)},Sub,,0,0,0,,{tag}{out}")
        # halo sombre diffus sous la page (contraste sur fond clair sans boîte opaque)
        plain = "\\N".join(" ".join(_esc(t) for t in ln) for ln in lines)
        ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Glow,,0,0,0,,{{\\fad(80,0)\\blur18}}{plain}")
    return ev


# --------------------------------------------------------------------------- carte verset

def _round_rect(w: int, h: int, r: int) -> str:
    k = 0.45 * r
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0")


def wrap_px(text: str, font_file: str, size: int, maxw: float) -> list[str]:
    lines, cur = [], ""
    for w in text.split(" "):  # split(" ") : respecte les espaces insécables
        if not w:
            continue
        t = f"{cur} {w}".strip()
        if cur and text_width(t, font_file, size) > maxw:
            lines.append(cur); cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    if len(lines) >= 2 and len(lines[-1].split(" ")) == 1 and len(lines[-2].split(" ")) > 2:  # pas de veuve
        a = lines[-2].split(" ")
        lines[-2], lines[-1] = " ".join(a[:-1]), a[-1] + " " + lines[-1]
    return lines


def surah_label(name: str) -> str:
    n = re.sub(r"aa", "a", name, flags=re.I)
    n = re.sub(r"ii", "i", n, flags=re.I)
    n = re.sub(r"uu", "ou", n, flags=re.I)
    return n.upper()


FR_CARD_FONT, FR_CARD_SIZE = "EBGaramond-Italic-VF.ttf", 66
AR_CARD_SIZE = 96
AR_CARD_FONT = "NotoNaskhArabic-Bold.ttf"


def _verse_card(m: dict, fr: str, a: float, b: float) -> list[str]:
    """Carte verset : panneau + libellé + arabe (1 ligne, si elle tient) + traduction Hamidullah en italique."""
    fr = clean_fr(fr).rstrip()
    inner = CARD_W - 130
    q = lambda t: f"«\u00a0{t}\u00a0»"  # noqa: E731  (espaces insécables : pas de guillemet seul en bout de ligne)
    fr_lines = [x.replace("\u00a0", " ") for x in wrap_px(q(fr), FR_CARD_FONT, FR_CARD_SIZE, inner)]
    if len(fr_lines) > 5:  # trop long : on garde la fin (le passage récité est en général à la fin)
        fr_lines = [x.replace("\u00a0", " ") for x in wrap_px(q("… " + fr[-190:].split(" ", 1)[-1]), FR_CARD_FONT, FR_CARD_SIZE, inner)[:5]]
    ar = recited_arabic(m)
    ar_size = AR_CARD_SIZE
    while ar and text_width(ar, AR_CARD_FONT, ar_size) > inner and ar_size > 74:
        ar_size -= 2
    if ar and text_width(ar, AR_CARD_FONT, ar_size) > inner:
        ar = ""  # ne tient pas sur une ligne : pas d'arabe
    pad_t, pad_b, lab_h, rule_h = 46, 44, 30, 44
    ar_h = int(ar_size * 1.3) if ar else 0
    fr_h = int(FR_CARD_SIZE * 0.92)
    h = pad_t + lab_h + rule_h + ar_h + fr_h * len(fr_lines) + pad_b
    top = CARD_BOTTOM - h
    x0 = (W - CARD_W) // 2
    ta, tb = _ass_time(a), _ass_time(b)
    fad = "\\fad(350,300)"

    def pos(x: int, y: float) -> str:  # léger glissement vers le haut à l'apparition
        return f"\\move({x},{int(y) + 24},{x},{int(y)},0,380)"

    ev = [f"Dialogue: 2,{ta},{tb},Card,,0,0,0,,{{\\an7{pos(x0, top)}{fad}\\p1}}{_round_rect(CARD_W, h, 34)}",
          # filet or à gauche de la carte
          f"Dialogue: 3,{ta},{tb},Card,,0,0,0,,{{\\an7{pos(x0 + 34, top)}{fad}\\1c{GOLD}\\1a&H00&\\p1}}m 0 0 l {CARD_W - 68} 0 l {CARD_W - 68} 4 l 0 4"]
    y = top + pad_t + lab_h / 2
    label = "CORAN" if m.get("ambiguous") else f"SOURATE {_esc(surah_label(m['surah_name']))}   ·   {m['ref']}"
    ev.append(f"Dialogue: 3,{ta},{tb},CardLabel,,0,0,0,,{{\\an5{pos(W // 2, y)}{fad}}}{label}")
    y += lab_h / 2 + rule_h / 2
    ev.append(f"Dialogue: 3,{ta},{tb},Card,,0,0,0,,{{\\an5{pos(W // 2, y)}{fad}\\1c{GOLD}\\1a&H60&\\p1}}m 0 0 l 90 0 l 90 2 l 0 2")
    y += rule_h / 2
    if ar:
        y += ar_h / 2
        ev.append(f"Dialogue: 3,{ta},{tb},CardAr,,0,0,0,,{{\\an5{pos(W // 2, y)}{fad}\\fs{ar_size}}}{ar}")
        y += ar_h / 2
    for ln in fr_lines:
        y += fr_h / 2
        ev.append(f"Dialogue: 3,{ta},{tb},CardFr,,0,0,0,,{{\\an5{pos(W // 2, y)}{fad}}}{_esc(ln)}")
        y += fr_h / 2
    return ev


def _header(header: dict | None, t_end: float) -> list[str]:
    if not header:
        return []
    b = min(6.0, t_end)
    ev = [f"Dialogue: 1,{_ass_time(0.15)},{_ass_time(b)},Head,,0,0,0,,{{\\an8\\pos(540,118)\\fad(600,800)}}"
          f"{_esc(header.get('title', DEFAULT_TITLE).upper())}"]
    if header.get("subtitle"):
        ev.append(f"Dialogue: 1,{_ass_time(0.15)},{_ass_time(b)},Head2,,0,0,0,,{{\\an8\\pos(540,168)\\fad(600,800)}}{_esc(header['subtitle'])}")
    return ev


ASS_HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
    "YCbCr Matrix: TV.709\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    f"Style: Sub,Montserrat ExtraBold,{SUB_SIZE},&H00FFFFFF,&H000000FF,&H00141414,&H64000000,0,0,0,0,100,100,0,0,1,5,3,2,90,90,{SAFE_BOTTOM},1\n"
    f"Style: Glow,Montserrat ExtraBold,{SUB_SIZE},&HFF000000,&H000000FF,&H50000000,&HFF000000,0,0,0,0,100,100,0,0,1,20,0,2,90,90,{SAFE_BOTTOM},1\n"
    "Style: Card,Montserrat,20,&H1016120E,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
    "Style: CardLabel,Montserrat Bold,31,&H0058C8F2,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,6,0,1,0,0,5,0,0,0,1\n"
    f"Style: CardAr,Noto Naskh Arabic,{AR_CARD_SIZE},&H00E9F1F4,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
    f"Style: CardFr,EB Garamond,{FR_CARD_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,-1,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
    "Style: Head,Montserrat Bold,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,8,0,1,0,2,8,0,0,0,1\n"
    "Style: Head2,Montserrat Bold,32,&H0058C8F2,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,3,0,1,0,2,8,0,0,0,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def subtitle(seg: dict, vms: list[dict], tr: dict) -> dict:
    """Ce que les clips montrent pour un segment (et ce que le back-office propose de corriger) :
    speech      parole française : le texte du segment ;
    verse       verset détecté : carte verset, traduction Hamidullah (fr) ;
    translation parole arabe : sa traduction française (fr None : pas de traduction, rien à l'écran) ;
    recitation  Coran non apparié : rien à l'écran (pas de traduction improvisée du Coran)."""
    if not is_arabic(seg["text"]):
        return {"kind": "speech", "fr": seg["text"]}
    m = match_for(seg, vms)
    if m:
        return {"kind": "verse", "verse": m, "fr": verse_fr(tr, m)}
    e = segment_fr(tr, seg)
    if e and e.get("coran"):
        return {"kind": "recitation", "fr": None}
    return {"kind": "translation", "fr": e["fr"] if e else None}


def build_ass(segments: list[dict], matches: list[dict], t0: float, t1: float, path: str, tr: dict | None = None,
              header: dict | None = None) -> int:
    """Écrit le .ass du clip [t0, t1] (temps de session) ; renvoie le nombre d'unités (pages de sous-titre + cartes)."""
    tr = tr or {}
    vms = shown(matches, ambiguous=True)
    dur = t1 - t0
    ev: list[str] = []
    cards: list[tuple[float, float]] = []
    words: list[dict] = []
    seen = set()
    segs = [s for s in segments if s["end"] > t0 and s["start"] < t1]
    for k, s in enumerate(segs):
        a, b = max(s["start"], t0) - t0, min(s["end"], t1) - t0
        nxt = (segs[k + 1]["start"] - t0) if k + 1 < len(segs) else dur
        sub = subtitle(s, vms, tr)
        if sub["kind"] == "verse":
            m, fr = sub["verse"], sub["fr"]
            key = (m["ref"], m["start"])
            if key not in seen:
                seen.add(key)
                ca = max(0.0, m["start"] - t0 - 0.2)
                after = [x for x in segs if x["start"] > m["end"] - 0.1]
                limit = (after[0]["start"] - t0 + 3.0) if after else dur  # la carte peut empiéter 3 s sur la parole suivante
                cb = min(dur, limit, max(m["end"] - t0 + 1.0, ca + 2.5 + len(fr) / 15.0))
                cards.append((ca, cb))
                ev += _verse_card(m, fr, ca, cb)
        elif sub["kind"] == "translation" and sub["fr"]:
            fr = sub["fr"]
            words += interpolate_words(fr, a, max(b, min(a + len(fr) / 17.0, nxt - 0.05, dur)))
        elif sub["kind"] == "speech":
            ws = s.get("words")
            if ws:
                for w in ws:
                    t = clean_fr(w["w"])
                    if not t or not (t0 <= w["s"] < t1):
                        continue
                    if words and (t[0] in "’-" or words[-1]["w"].endswith(("’", "-"))) and (w["s"] - t0) - words[-1]["e"] < 0.3:
                        words[-1]["w"] += t; words[-1]["e"] = w["e"] - t0   # « d » + « ’Allah » -> « d’Allah »
                    else:
                        words.append({"w": t, "s": w["s"] - t0, "e": w["e"] - t0})
            else:
                words += interpolate_words(s["text"].strip(), a, b)
    words.sort(key=lambda w: w["s"])
    if words:
        words[0]["w"] = words[0]["w"][:1].upper() + words[0]["w"][1:]
    sev = _speech_events(words, cards, dur)
    pages = sum(1 for e in sev if ",Glow," in e)
    Path(path).write_text(ASS_HEADER + "\n".join(_header(header, dur) + ev + sev) + "\n", encoding="utf-8")
    return pages + len(cards)


# --------------------------------------------------------------------------- rendu

def _pcm(path: str, start: float = 0.0, dur: float | None = None, sr: int = 8000):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", path] + (["-t", f"{dur:.3f}"] if dur else []) + \
          ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.int16).astype(np.float32)


def align_offset(session_wav: str, video: str, probe_at: float = 30.0, probe_len: float = 40.0, sr: int = 8000) -> tuple[float, float]:
    """Décalage (s) tel que temps_vidéo = temps_session + décalage, par intercorrélation audio.

    Sert à tailler les clips dans une autre vidéo de la même khutba (enregistrement propre décalé, source d'archive).
    Renvoie (décalage, netteté du pic) ; netteté < 8 = alignement douteux.
    """
    import numpy as np
    x = _pcm(video, sr=sr)
    q = _pcm(session_wav, probe_at, probe_len, sr=sr)
    if len(q) < sr or len(x) <= len(q):
        raise ValueError("audio trop court pour l'alignement")
    n = 1 << (len(x) + len(q) - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(x, n) * np.conj(np.fft.rfft(q, n)), n)[: len(x) - len(q)]
    k = int(c.argmax())
    sharp = float(c[k] / (np.median(np.abs(c)) + 1e-9))
    return k / sr - probe_at, sharp


def gradient_png(path: str) -> str:
    """Voile sombre progressif (bas : lisibilité des sous-titres ; haut : en-tête), 1080x1920 RGBA."""
    import numpy as np
    from PIL import Image
    y = np.arange(H, dtype=np.float32)
    bot = np.clip((y - 1020) / 480, 0, 1) ** 1.4 * 0.78   # plein à partir de ~1500 px : zone des sous-titres et de l'interface
    top = np.clip((330 - y) / 330, 0, 1) ** 2 * 0.40
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 3] = (np.maximum(bot, top) * 255).astype(np.uint8)[:, None]
    Image.fromarray(img, "RGBA").save(path)
    return path


def crop_filter(crop: dict | None) -> str:
    """Recadrage 9:16. crop = {x: centre horizontal (0-1), top, bottom: fractions de hauteur retirées}."""
    c = {"x": 0.5, "top": 0.0, "bottom": 0.0, **(crop or {})}
    hh = f"ih*{1 - c['top'] - c['bottom']:.4f}"
    return (f"crop=w='trunc({hh}*9/16/2)*2':h='trunc({hh}/2)*2':"
            f"x='max(0,min(iw-ow,iw*{c['x']:.4f}-ow/2))':y='trunc(ih*{c['top']:.4f})'")


def render_clip(src: str, rec_offset: float, w: dict, ass: str, out: str, log=print, crop: dict | None = None) -> None:
    """Recadrage 9:16 (1080x1920), voile dégradé, sous-titres brûlés, audio d'origine normalisé (pas de musique)."""
    a, dur = rec_offset + w["start"], w["end"] - w["start"]
    with tempfile.TemporaryDirectory() as td:
        grad = gradient_png(str(Path(td) / "grad.png"))
        fc = (f"[0:v]{crop_filter(crop)},scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p[cam];"
              f"[cam][1:v]overlay=0:0:format=auto,subtitles={ass}:fontsdir={FONTS},format=yuv420p[v];"
              f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
              f"afade=t=in:d=0.3,afade=t=out:st={max(0, dur - 0.6):.2f}:d=0.6[a]")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", str(Path(src).resolve()),
               "-loop", "1", "-framerate", "25", "-i", grad, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(Path(out).resolve())]
        subprocess.run(cmd, check=True, cwd=str(Path(out).resolve().parent))
    log(f"clip -> {out} ({dur:.0f}s)")


def make_clip(segments: list[dict], matches: list[dict], tr: dict, w: dict, idx: int, version: int,
              video: str, offset: float, outdir: Path, header: dict | None, crop: dict | None, log=print) -> Path:
    """Sous-titres + rendu du clip n° idx, version `version`, de la fenêtre w (temps de session) ; renvoie le MP4.

    offset : position du début de la session dans `video` (temps_vidéo = temps_session + offset)."""
    v = f"_v{version}" if version > 1 else ""
    ass = outdir / f"clip{idx}{v}.ass"
    out = outdir / f"clip{idx}_{w.get('kind') or 'extrait'}{v}_A-VALIDER.mp4"
    build_ass(segments, matches, w["start"], w["end"], str(ass), tr=tr, header=header)
    render_clip(video, offset, w, ass.name, str(out), log, crop=crop)
    return out
