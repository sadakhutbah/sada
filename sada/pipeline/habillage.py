"""Habillage d'une vidéo hors direct : l'identité de la mosquée par-dessus la source, comme un overlay de direct.

Chaque élément s'active seul :
- carton d'ouverture : fond aux couleurs de la mosquée, logo, nom de la mosquée, titre, sous-titre (série,
  séance, date), ajouté AVANT la vidéo (il ne cache jamais le début de la parole) ;
- bandeau « lower third » : nom de l'intervenant et sa qualité, révélé en volet au début de la vidéo ;
- logo en filigrane dans un coin (position, taille et opacité réglables), le disque des clips, montré
  pendant le bandeau seulement (le reste de la vidéo est copié tel quel, voir le rendu par segments) ;
- barre de couleur au bas de l'image, le doré de la mosquée ;
- écran de fin : logo, nom de la mosquée, message, lien, ajouté APRÈS la vidéo.

Même grammaire que les clips (`clips.py`) : textes en ASS brûlés par libass, mêmes polices, même doré,
même disque de logo. L'original n'est jamais modifié : le rendu écrit un nouveau fichier.
Tout ce qui est pur (options, mise en page, ASS, graphe ffmpeg) se teste sans ffmpeg.

Rendu par segments : seuls la tête (carton d'ouverture, bandeau) et la queue (carton de fin) sont
réencodées ; le milieu, coupé sur des images clés de la source, est copié sans réencodage. Le son est
réencodé d'un seul tenant (pas de jointure AAC). Repli sur le rendu complet quand la source ne s'y
prête pas (codec, taille, cadence variable, B-frames profondes) ou que la barre court sur toute la vidéo.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

from ..i18n import _
from ..paths import FONTS
from ..text import is_arabic
from . import clips as clipmod

# ------------------------------------------------------------------ options

DEFAUTS: dict = {
    "intro_on": True,
    "title": "",
    "subtitle": "",
    "intro_s": 5.0,
    "lower_on": True,
    "speaker": "",
    "role": "",
    "lower_at": 2.0,
    "lower_s": 8.0,
    "logo_on": True,
    "logo_pos": "tr",
    "logo_size": "m",
    "logo_opacity": 0.85,
    "bar_on": False,
    "outro_on": True,
    "outro_text": "",
    "outro_link": "",
    "outro_s": 6.0,
}
BOOLEENS = ("intro_on", "lower_on", "logo_on", "bar_on", "outro_on")
#: bornes des nombres réglables (secondes ; opacité entre 0 et 1)
BORNES = {
    "intro_s": (2.0, 15.0),
    "lower_at": (0.0, 600.0),
    "lower_s": (3.0, 30.0),
    "outro_s": (2.0, 20.0),
    "logo_opacity": (0.3, 1.0),
}
TEXTES = {"title": 160, "subtitle": 160, "speaker": 80, "role": 90, "outro_text": 140, "outro_link": 100}
POSITIONS = ("tl", "tr", "bl", "br")
#: diamètre du filigrane, en fraction du petit côté de l'image
TAILLES = {"s": 0.075, "m": 0.10, "l": 0.13}

VRAI = {"1", "true", "on", "oui", "yes"}
FAUX = {"0", "false", "off", "non", "no", ""}


def normaliser(brut: dict | None, base: dict | None = None) -> dict:
    """Options complètes et propres : `base` (les réglages enregistrés) complétée par `brut` (le formulaire).

    Les nombres sont ramenés dans leurs bornes, les textes nettoyés et coupés. Une valeur illisible
    (durée qui n'est pas un nombre, position inconnue) lève ValueError avec un message à montrer tel quel.
    """
    o = dict(DEFAUTS)
    o.update({k: v for k, v in (base or {}).items() if k in DEFAUTS})
    for k, v in (brut or {}).items():
        if k not in DEFAUTS:
            continue
        if k in BOOLEENS:
            if isinstance(v, bool):
                o[k] = v
            elif str(v).strip().lower() in VRAI | FAUX:
                o[k] = str(v).strip().lower() in VRAI
            else:
                raise ValueError(_("Yes/no value expected for {key}.", key=k))
        elif k in BORNES:
            try:
                x = float(str(v).replace(",", "."))
            except ValueError:
                raise ValueError(_("Invalid duration: a number of seconds is expected.")) from None
            if not math.isfinite(x):
                raise ValueError(_("Invalid duration: a number of seconds is expected."))
            lo, hi = BORNES[k]
            o[k] = round(min(hi, max(lo, x)), 2)
        elif k == "logo_pos":
            if v not in POSITIONS:
                raise ValueError(_("Unknown logo position."))
            o[k] = v
        elif k == "logo_size":
            if v not in TAILLES:
                raise ValueError(_("Unknown logo size."))
            o[k] = v
        else:
            o[k] = re.sub(r"\s+", " ", str(v or "")).strip()[: TEXTES[k]]
    return o


def verifier(o: dict) -> None:
    """Refuse un habillage qui afficherait un élément vide."""
    if o["intro_on"] and not o["title"]:
        raise ValueError(_("The opening card needs a title."))
    if o["lower_on"] and not o["speaker"]:
        raise ValueError(_("The lower third needs the speaker's name."))
    if not any(o[k] for k in BOOLEENS):
        raise ValueError(_("No branding element is enabled."))


# ------------------------------------------------------------------ couleurs

VERT = "#1C3A2E"  # vert profond du back-office, quand la mosquée n'a pas de couleur
OR = "#F2C858"  # doré des clips
IVOIRE = "&H00E3F1F7&"  # #F7F1E3 en BGR


def _hex(value: str | None, defaut: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (value or "").strip())
    h = m.group(1) if m else defaut.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ass(rgb: tuple[int, int, int], alpha: int = 0) -> str:
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}&"


def couleurs(brand: dict | None) -> dict:
    """Vert et doré de la mosquée (charte des clips par défaut), et leurs variantes."""
    vert = _hex((brand or {}).get("green"), VERT)
    gold = _hex((brand or {}).get("gold"), OR)
    sombre = tuple(max(4, round(c * 0.42)) for c in vert)
    return {"green": vert, "gold": gold, "dark": sombre}


# ------------------------------------------------------------------ cadre et chronologie


@dataclass(frozen=True)
class Cadre:
    """Image de sortie (W×H, images par seconde) et source (durée, son présent)."""

    w: int
    h: int
    fps: float
    duree: float
    audio: bool = True

    @property
    def u(self) -> float:  # unité : 1 px d'une image 1080 de petit côté
        return min(self.w, self.h) / 1080


def taille_sortie(w: int, h: int) -> tuple[int, int]:
    """Petit côté ramené à 1080, proportions de la source gardées, dimensions paires."""
    if w <= 0 or h <= 0:
        return 1920, 1080
    if w >= h:
        return max(2, round(1080 * w / h / 2) * 2), 1080
    return 1080, max(2, round(1080 * h / w / 2) * 2)


def _ffprobe() -> str | None:
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    for bin_ in (Path(__file__).resolve().parents[2] / "tools" / "bin", Path.home() / ".local" / "bin"):
        for name in ("ffprobe.exe", "ffprobe"):
            if (bin_ / name).exists():
                return str(bin_ / name)
    return None


def ffmpeg() -> str:
    from ..backoffice.importer import _ffmpeg

    exe = _ffmpeg()
    if not exe:
        raise RuntimeError(_("ffmpeg not found on this machine."))
    return exe


def sonder(src: str) -> Cadre:
    """Dimensions (rotation du téléphone comprise), cadence, durée et présence du son de la source."""
    exe = _ffprobe()
    if not exe:
        raise RuntimeError(_("ffprobe not found on this machine."))
    p = subprocess.run(
        [exe, "-v", "error", "-show_streams", "-show_format", "-of", "json", src],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if p.returncode != 0:
        raise RuntimeError(_("Unreadable source video."))
    d = json.loads(p.stdout or "{}")
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError(_("The source has no video stream."))
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    try:
        rot = int(float((v.get("tags") or {}).get("rotate") or 0))
    except ValueError:
        pass
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if abs(rot) % 180 == 90:
        w, h = h, w
    try:
        n, _sep, dd = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "25/1").partition("/")
        fps = float(n) / float(dd or 1)
    except (ValueError, ZeroDivisionError):
        fps = 25.0
    fps = 25.0 if not (10 <= fps <= 61) else round(fps, 3)
    duree = float((d.get("format") or {}).get("duration") or v.get("duration") or 0)
    if duree <= 0:
        raise RuntimeError(_("Unknown source duration."))
    W, H = taille_sortie(w, h)
    return Cadre(W, H, fps, duree, any(s.get("codec_type") == "audio" for s in streams))


@dataclass(frozen=True)
class Chrono:
    """Où tombe chaque partie dans la vidéo habillée : [0, i) carton, [i, i+v) vidéo, [i+v, total) fin."""

    i: float
    v: float
    o: float

    @property
    def total(self) -> float:
        return self.i + self.v + self.o

    @property
    def fin_video(self) -> float:
        return self.i + self.v


def chrono(o: dict, duree: float) -> Chrono:
    return Chrono(o["intro_s"] if o["intro_on"] and o["title"] else 0.0, duree, o["outro_s"] if o["outro_on"] else 0.0)


def fenetre_bandeau(o: dict, c: Chrono) -> tuple[float, float] | None:
    """Début et fin du bandeau dans la vidéo habillée (None s'il est coupé ou hors de la vidéo)."""
    if not (o["lower_on"] and o["speaker"]):
        return None
    a = c.i + min(o["lower_at"], max(0.0, c.v - 1.0))
    b = min(a + o["lower_s"], c.fin_video)
    return (a, b) if b - a >= 1.0 else None


# ------------------------------------------------------------------ mise en page


def _lignes(texte: str, police: str, taille: float, maxw: float, max_lignes: int = 3) -> list[str]:
    lignes = clipmod.wrap_px(texte, police, round(taille), maxw) or [texte]
    if len(lignes) > max_lignes:
        lignes = lignes[: max_lignes - 1] + [" ".join(lignes[max_lignes - 1 :])]
    return lignes


#: largeur réellement dessinée par libass / largeur estimée par `clips.text_width`, mesurée une fois sur
#: un rendu (Montserrat gras : libass la dessine nettement plus étroite que l'estimation PIL).
CALAGE = {"Montserrat-Bold.ttf": 0.77, "EBGaramond-SemiBold.ttf": 0.91}


def _largeur(texte: str, police: str, taille: float, espacement: float = 0.0) -> float:
    return clipmod.text_width(texte, police, taille) * CALAGE.get(police, 1.0) + espacement * max(0, len(texte) - 1)


def mise_en_page(o: dict, cadre: Cadre, mosquee: str, logo: bool) -> dict:
    """Positions de chaque élément, en pixels de l'image de sortie. Partagée par l'ASS et le graphe ffmpeg
    (le logo des cartons est un overlay : il doit tomber exactement où l'ASS lui a laissé la place)."""
    W, H, u = cadre.w, cadre.h, cadre.u
    p: dict = {"u": u}
    maxw = W * 0.78

    # carton d'ouverture : bloc centré verticalement
    titre_ar = is_arabic(o["title"])
    t_police, t_taille = ("NotoNaskhArabic-SemiBold.ttf", 84 * u) if titre_ar else ("EBGaramond-SemiBold.ttf", 96 * u)
    t_lignes = _lignes(o["title"], t_police, t_taille, maxw) if o["title"] else []
    t_pas = t_taille * (1.15 if titre_ar else 1.02)
    d_logo = round(200 * u) if logo else 0
    blocs = [
        d_logo + 46 * u if logo else 0,
        34 * u,
        38 * u,
        len(t_lignes) * t_pas,
        40 * u,
        (36 * u + 38 * u) if o["subtitle"] else 0,
    ]
    y = (H - sum(blocs)) / 2
    intro = {"logo": None, "titre_ar": titre_ar, "titre": [], "pas": t_pas}
    if logo:
        intro["logo"] = (round((W - d_logo) / 2), round(y), d_logo)
        y += d_logo + 46 * u
    intro["kicker"] = y + 17 * u
    y += 34 * u + 38 * u
    for ligne in t_lignes:
        intro["titre"].append((ligne, y + t_pas / 2))
        y += t_pas
    intro["regle"] = y + 20 * u
    y += 40 * u + 36 * u
    intro["sous_titre"] = y + 19 * u if o["subtitle"] else None
    p["intro"] = intro

    # écran de fin : logo, nom de la mosquée, filet, message, lien
    n_lignes = _lignes(mosquee, "EBGaramond-SemiBold.ttf", 84 * u, maxw, 2)
    m_lignes = _lignes(o["outro_text"], "SourceSans3-Regular.ttf", 42 * u, maxw * 0.9, 3) if o["outro_text"] else []
    blocs = [
        d_logo + 50 * u if logo else 0,
        len(n_lignes) * 86 * u,
        44 * u,
        len(m_lignes) * 52 * u + (18 * u if m_lignes else 0),
        (54 * u if o["outro_link"] else 0),
    ]
    y = (H - sum(blocs)) / 2
    fin = {"logo": None, "nom": [], "message": [], "lien": None}
    if logo:
        fin["logo"] = (round((W - d_logo) / 2), round(y), d_logo)
        y += d_logo + 50 * u
    for ligne in n_lignes:
        fin["nom"].append((ligne, y + 43 * u))
        y += 86 * u
    fin["regle"] = y + 22 * u
    y += 44 * u + (18 * u if m_lignes else 0)
    for ligne in m_lignes:
        fin["message"].append((ligne, y + 26 * u))
        y += 52 * u
    if o["outro_link"]:
        fin["lien"] = y + 14 * u + 20 * u
    p["fin"] = fin

    # filigrane
    marge = round(44 * u)
    d_wm = round(TAILLES[o["logo_size"]] * min(W, H))
    pos = o["logo_pos"]
    barre = round(8 * u) if o["bar_on"] else 0
    x_wm = marge if pos[1] == "l" else W - marge - d_wm
    y_wm = marge if pos[0] == "t" else H - marge - d_wm - barre
    p["filigrane"] = (x_wm, y_wm, d_wm) if logo and o["logo_on"] else None
    p["barre"] = barre

    # bandeau : en bas à gauche, au-dessus du filigrane s'il occupe le même coin
    nom_t, role_t = 52 * u, 32 * u
    nom_w = _largeur(o["speaker"], "Montserrat-Bold.ttf", nom_t, 0.5 * u)
    role_w = _largeur(o["role"].upper(), "SourceSans3-Semibold.ttf", role_t, 2.5 * u) if o["role"] else 0
    pad, accent = round(34 * u), round(9 * u)
    ph = round((24 + 56 + (10 + 36 if o["role"] else 0) + 24) * u)
    pw = round(min(W * 0.7, accent + 2 * pad + max(nom_w, role_w)))
    bas = round(96 * u) + barre
    if p["filigrane"] and pos == "bl":
        bas = H - y_wm + round(26 * u)
    x0, y1 = round(72 * u), H - bas
    p["bandeau"] = {
        "x": x0,
        "y": y1 - ph,
        "w": pw,
        "h": ph,
        "accent": accent,
        "pad": pad,
        "nom_y": y1 - ph + round(24 * u),
        "role_y": y1 - ph + round((24 + 56 + 10) * u),
        "nom_t": nom_t,
        "role_t": role_t,
    }
    return p


# ------------------------------------------------------------------ ASS


def _t(x: float) -> str:
    return clipmod._ass_time(x)


def _esc(t: str) -> str:
    return clipmod._esc(t)


def _rect(w: float, h: float) -> str:
    return f"m 0 0 l {round(w)} 0 {round(w)} {round(h)} 0 {round(h)}"


def entete_ass(cadre: Cadre, col: dict) -> str:
    u = cadre.u
    gold, ivoire = _ass(col["gold"]), IVOIRE
    ombre = "&H78000000&"

    def style(nom, police, taille, couleur, gras=0, esp=0.0, contour=0, ombre_px=0, al=5, contour_c="&H00000000&"):
        return (
            f"Style: {nom},{police},{round(taille)},{couleur},&H000000FF&,{contour_c},{ombre},{gras},0,0,0,100,100,"
            f"{esp:.1f},0,1,{contour},{ombre_px},{al},0,0,0,1\n"
        )

    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {cadre.w}\nPlayResY: {cadre.h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + style("Forme", "Montserrat", 20, "&H00FFFFFF&", al=7)
        + style("Kicker", "Montserrat", 30 * u, gold, gras=-1, esp=7 * u)
        + style("Titre", "EB Garamond SemiBold", 96 * u, ivoire)
        + style("TitreAr", "Noto Naskh Arabic SemiBold", 84 * u, ivoire)
        + style("Sous", "Source Sans 3", 38 * u, "&H30E3F1F7&", esp=0.5 * u)
        + style("Nom", "Montserrat", 52 * u, "&H00FFFFFF&", gras=-1, esp=0.5 * u, al=7)
        + style("Role", "Source Sans 3 Semibold", 32 * u, gold, esp=2.5 * u, al=7)
        + style("FinNom", "EB Garamond SemiBold", 84 * u, ivoire)
        + style("FinTexte", "Source Sans 3", 42 * u, "&H20E3F1F7&")
        + style("FinLien", "Montserrat", 34 * u, gold, gras=-1, esp=2 * u)
        + "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _ev(couche: int, a: float, b: float, style: str, texte: str) -> str:
    return f"Dialogue: {couche},{_t(a)},{_t(b)},{style},,0,0,0,,{texte}"


def _carton_intro(o: dict, p: dict, cadre: Cadre, c: Chrono, mosquee: str, col: dict) -> list[str]:
    if not c.i:
        return []
    W, u, it = cadre.w, cadre.u, p["intro"]
    fin = c.i - 0.15
    ev = [
        _ev(
            2,
            0.25,
            fin,
            "Kicker",
            f"{{\\an5\\pos({W / 2:.0f},{it['kicker']:.0f})\\fad(500,350)}}{_esc(mosquee.upper())}",
        )
    ]
    style = "TitreAr" if it["titre_ar"] else "Titre"
    for n, (ligne, y) in enumerate(it["titre"]):
        d = 0.45 + 0.12 * n
        ev.append(
            _ev(
                2,
                d,
                fin,
                style,
                f"{{\\an5\\move({W / 2:.0f},{y + 22 * u:.0f},{W / 2:.0f},{y:.0f},0,700)\\fad(550,350)}}{_esc(ligne)}",
            )
        )
    # filet doré qui s'ouvre depuis le centre sous le titre
    lw, lh = 150 * u, max(2, round(3 * u))
    ev.append(
        _ev(
            1,
            0.7,
            fin,
            "Forme",
            f"{{\\an5\\pos({W / 2:.0f},{it['regle']:.0f})\\1c{_ass(col['gold'])}\\bord0\\shad0"
            f"\\fscx0\\t(0,700,\\fscx100)\\fad(0,350)\\p1}}{_rect(lw, lh)}{{\\p0}}",
        )
    )
    if it["sous_titre"]:
        ev.append(
            _ev(
                2,
                0.95,
                fin,
                "Sous",
                f"{{\\an5\\pos({W / 2:.0f},{it['sous_titre']:.0f})\\fad(500,350)}}{_esc(o['subtitle'])}",
            )
        )
    return ev


def _bandeau(o: dict, p: dict, c: Chrono, col: dict) -> list[str]:
    f = fenetre_bandeau(o, c)
    if not f:
        return []
    a, b = f
    d = round((b - a) * 1000)
    q = p["bandeau"]
    x, y, w, h = q["x"], q["y"], q["w"], q["h"]
    ouvre, ferme = (
        f"\\clip({x},{y},{x},{y + h})\\t(0,520,0.6,\\clip({x},{y},{x + w},{y + h}))",
        f"\\t({max(0, d - 450)},{d},1.6,\\clip({x},{y},{x},{y + h}))",
    )
    ev = [
        # panneau vert profond, presque opaque, révélé en volet de gauche à droite
        _ev(
            3,
            a,
            b,
            "Forme",
            f"{{\\an7\\pos({x},{y})\\1c{_ass(col['dark'])}\\bord0\\shad0{ouvre}{ferme}\\p1}}{_rect(w, h)}{{\\p0}}",
        ),
        # liseré doré à gauche : il arrive d'abord, il part en dernier
        _ev(
            4,
            a,
            b,
            "Forme",
            f"{{\\an7\\pos({x},{y})\\1c{_ass(col['gold'])}\\bord0\\shad0\\fad(180,300)\\p1}}"
            f"{_rect(q['accent'], h)}{{\\p0}}",
        ),
    ]
    tx = x + q["accent"] + q["pad"]
    texte = f"\\alpha&HFF&\\t(260,620,\\alpha&H00&)\\t({max(0, d - 520)},{max(0, d - 220)},\\alpha&HFF&)"
    ev.append(_ev(5, a, b, "Nom", f"{{\\an7\\pos({tx},{q['nom_y']}){texte}}}{_esc(o['speaker'])}"))
    if o["role"]:
        ev.append(_ev(5, a, b, "Role", f"{{\\an7\\pos({tx},{q['role_y']}){texte}}}{_esc(o['role'].upper())}"))
    return ev


def _barre(p: dict, cadre: Cadre, c: Chrono, col: dict) -> list[str]:
    if not p["barre"]:
        return []
    return [
        _ev(
            3,
            c.i,
            c.fin_video,
            "Forme",
            f"{{\\an7\\pos(0,{cadre.h - p['barre']})\\1c{_ass(col['gold'])}\\bord0\\shad0"
            f"\\fad(600,400)\\p1}}{_rect(cadre.w, p['barre'])}{{\\p0}}",
        )
    ]


def _carton_fin(o: dict, p: dict, cadre: Cadre, c: Chrono, mosquee: str, col: dict) -> list[str]:
    if not c.o:
        return []
    W, u, f = cadre.w, cadre.u, p["fin"]
    a, b = c.fin_video + 0.3, c.total - 0.2
    ev = []
    for ligne, y in f["nom"]:
        ev.append(
            _ev(
                2,
                a + 0.15,
                b,
                "FinNom",
                f"{{\\an5\\move({W / 2:.0f},{y + 18 * u:.0f},{W / 2:.0f},{y:.0f},0,650)\\fad(550,450)}}{_esc(ligne)}",
            )
        )
    lw, lh = 150 * u, max(2, round(3 * u))
    ev.append(
        _ev(
            1,
            a + 0.4,
            b,
            "Forme",
            f"{{\\an5\\pos({W / 2:.0f},{f['regle']:.0f})\\1c{_ass(col['gold'])}\\bord0\\shad0"
            f"\\fscx0\\t(0,700,\\fscx100)\\fad(0,450)\\p1}}{_rect(lw, lh)}{{\\p0}}",
        )
    )
    for ligne, y in f["message"]:
        ev.append(_ev(2, a + 0.6, b, "FinTexte", f"{{\\an5\\pos({W / 2:.0f},{y:.0f})\\fad(500,450)}}{_esc(ligne)}"))
    if f["lien"]:
        ev.append(
            _ev(
                2,
                a + 0.85,
                b,
                "FinLien",
                f"{{\\an5\\pos({W / 2:.0f},{f['lien']:.0f})\\fad(500,450)}}{_esc(o['outro_link'])}",
            )
        )
    return ev


def build_ass(o: dict, cadre: Cadre, mosquee: str, brand: dict | None = None, logo: bool = False) -> str:
    """Fichier ASS complet de l'habillage : cartons, bandeau, barre. Le logo, lui, est un overlay ffmpeg."""
    col = couleurs(brand)
    c = chrono(o, cadre.duree)
    p = mise_en_page(o, cadre, mosquee, logo)
    ev = (
        _carton_intro(o, p, cadre, c, mosquee, col)
        + _bandeau(o, p, c, col)
        + _barre(p, cadre, c, col)
        + _carton_fin(o, p, cadre, c, mosquee, col)
    )
    return entete_ass(cadre, col) + "\n".join(ev) + ("\n" if ev else "")


# ------------------------------------------------------------------ fond des cartons


def fond_carton(path: str, cadre: Cadre, brand: dict | None = None) -> str:
    """Fond des cartons : dégradé radial du vert de la mosquée, cadre doré fin, comme un plateau de direct."""
    import numpy as np
    from PIL import Image, ImageDraw

    col = couleurs(brand)
    W, H, u = cadre.w, cadre.h, cadre.u
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((x - W / 2) / (W * 0.62)) ** 2 + ((y - H * 0.46) / (H * 0.78)) ** 2)
    k = np.clip(1.0 - r, 0, 1) ** 1.3  # 1 au centre, 0 aux bords
    centre = np.array(col["green"], np.float32) * 0.92 + 255 * 0.04
    bord = np.array(col["green"], np.float32) * 0.30
    img = (bord + (centre - bord) * k[..., None]).clip(0, 255).astype(np.uint8)
    im = Image.fromarray(img, "RGB")
    d = ImageDraw.Draw(im, "RGBA")
    m, ep = round(40 * u), max(1, round(1.5 * u))
    gold = (*col["gold"], 120)
    d.rectangle((m, m, W - 1 - m, H - 1 - m), outline=gold, width=ep)
    # petits losanges aux coins du cadre
    s = round(7 * u)
    for cx, cy in ((m, m), (W - 1 - m, m), (m, H - 1 - m), (W - 1 - m, H - 1 - m)):
        d.polygon(((cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)), fill=(*col["gold"], 210))
    im.save(path, "PNG")
    return path


# ------------------------------------------------------------------ graphe ffmpeg

FONDU = 0.45  # fondu au noir entre les cartons et la vidéo


def graphe(o: dict, cadre: Cadre, ass_name: str, logo: bool, p: dict, apercu: bool = False) -> tuple[str, list[str]]:
    """(filter_complex, entrées supplémentaires après la source). Entrées : 0 source, puis fond, puis logo."""
    c = chrono(o, cadre.duree)
    W, H, fps = cadre.w, cadre.h, cadre.fps
    extra: list[str] = []
    n = 1
    fc: list[str] = []
    v_fx = [
        f"scale={W}:{H}:force_original_aspect_ratio=decrease",
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        f"fps={fps}",
        f"trim=duration={c.v:.3f}",
        "setpts=PTS-STARTPTS",
        "format=yuv420p",
    ]
    if c.i:
        v_fx.append(f"fade=t=in:st=0:d={FONDU}")
    if c.o:
        v_fx.append(f"fade=t=out:st={max(0.0, c.v - FONDU):.3f}:d={FONDU}")
    fc.append(f"[0:v]{','.join(v_fx)}[v0]")
    if not apercu:
        if cadre.audio:
            fc.append(
                f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
                f"atrim=duration={c.v:.3f},asetpts=PTS-STARTPTS"
                + (",afade=t=in:d=0.3" if c.i else "")
                + (f",afade=t=out:st={max(0.0, c.v - 0.6):.3f}:d=0.6" if c.o else "")
                + "[a0]"
            )
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={c.v:.3f}[a0]")

    segments_v, segments_a = [], []
    if c.i or c.o:
        fond = n
        extra += ["-loop", "1", "-framerate", f"{fps}", "-t", f"{c.total + 1:.3f}", "-i", "{fond}"]
        n += 1
        sorties = [s for s, d in (("ci", c.i), ("co", c.o)) if d]
        fc.append(f"[{fond}:v]format=yuv420p,setsar=1,split={len(sorties)}" + "".join(f"[{s}]" for s in sorties))
        if c.i:
            fc.append(
                f"[ci]trim=duration={c.i:.3f},setpts=PTS-STARTPTS,fade=t=in:st=0:d=0.5,"
                f"fade=t=out:st={c.i - FONDU:.3f}:d={FONDU}[iv]"
            )
            if not apercu:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={c.i:.3f}[ia]")
        if c.o:
            fc.append(
                f"[co]trim=duration={c.o:.3f},setpts=PTS-STARTPTS,fade=t=in:st=0:d={FONDU},"
                f"fade=t=out:st={c.o - 0.6:.3f}:d=0.6[ov]"
            )
            if not apercu:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={c.o:.3f}[oa]")
    if c.i:
        segments_v.append("[iv]")
        segments_a.append("[ia]")
    segments_v.append("[v0]")
    segments_a.append("[a0]")
    if c.o:
        segments_v.append("[ov]")
        segments_a.append("[oa]")
    k = len(segments_v)
    if apercu:
        fc.append("".join(segments_v) + f"concat=n={k}:v=1:a=0[cv]")
    else:
        fc.append(
            "".join(v + a for v, a in zip(segments_v, segments_a, strict=False)) + f"concat=n={k}:v=1:a=1[cv][ca]"
        )

    courant = "cv"
    poses = []
    bandeau = fenetre_bandeau(o, c)
    if logo:
        if p["filigrane"] and bandeau:  # le filigrane accompagne le bandeau, pas toute la vidéo
            poses.append(("wm", p["filigrane"]))
        if c.i and p["intro"]["logo"]:
            poses.append(("li", p["intro"]["logo"]))
        if c.o and p["fin"]["logo"]:
            poses.append(("lo", p["fin"]["logo"]))
    if poses:
        lg = n
        extra += ["-loop", "1", "-framerate", f"{fps}", "-t", f"{c.total + 1:.3f}", "-i", "{logo}"]
        n += 1
        fc.append(f"[{lg}:v]format=rgba,split={len(poses)}" + "".join(f"[s{nom}]" for nom, _ in poses))
        for nom, (x, y, d) in poses:
            if nom == "wm":
                a, b = bandeau or (c.i, c.fin_video)
                fx = (
                    f"scale={d}:{d},colorchannelmixer=aa={o['logo_opacity']:.2f},"
                    f"fade=t=in:st={a + 0.4:.3f}:d=0.8:alpha=1,fade=t=out:st={max(a, b - 0.7):.3f}:d=0.6:alpha=1"
                )
            elif nom == "li":
                a, b = 0.0, c.i
                fx = f"scale={d}:{d},fade=t=in:st=0.15:d=0.7:alpha=1,fade=t=out:st={max(0.2, b - 0.65):.3f}:d=0.45:alpha=1"
            else:
                a, b = c.fin_video, c.total
                fx = (
                    f"scale={d}:{d},fade=t=in:st={a + 0.3:.3f}:d=0.7:alpha=1,"
                    f"fade=t=out:st={max(a + 0.4, b - 0.8):.3f}:d=0.6:alpha=1"
                )
            fc.append(f"[s{nom}]{fx}[{nom}]")
            fc.append(f"[{courant}][{nom}]overlay={x}:{y}:format=auto:enable='between(t,{a:.3f},{b:.3f})'[o{nom}]")
            courant = f"o{nom}"
    fc.append(f"[{courant}]subtitles=filename={ass_name}:fontsdir='{FONTS}',format=yuv420p[v]")
    return ";".join(fc), extra


# ------------------------------------------------------------------ rendu


def _preparer(
    o: dict, src: str, td: Path, mosquee: str, brand: dict | None, logo_src: str | None, cadre: Cadre
) -> tuple[str, list[str], Chrono]:
    """Écrit l'ASS, le fond et le logo dans `td` ; rend (filter_complex, entrées supplémentaires, chronologie)."""
    besoin_logo = bool(logo_src) and (o["logo_on"] or o["intro_on"] or o["outro_on"])
    lg = (
        clipmod.logo_disc(str(logo_src), str(td / "logo.png"), size=round(220 * cadre.u), brand=brand)
        if besoin_logo
        else None
    )
    p = mise_en_page(o, cadre, mosquee, bool(lg))
    (td / "habillage.ass").write_text(build_ass(o, cadre, mosquee, brand, logo=bool(lg)), encoding="utf-8")
    c = chrono(o, cadre.duree)
    fc, extra = graphe(o, cadre, "habillage.ass", bool(lg), p, apercu=False)
    fond = fond_carton(str(td / "fond.png"), cadre, brand) if (c.i or c.o) else ""
    extra = [x.replace("{fond}", fond).replace("{logo}", lg or "") for x in extra]
    return fc, extra, c


def _bornes(cadre: Cadre, debut: float, fin: float | None) -> Cadre:
    """La source coupée à [debut, fin] (session dans un enregistrement plus long)."""
    debut = max(0.0, min(debut, cadre.duree - 1.0))
    duree = (min(fin, cadre.duree) if fin else cadre.duree) - debut
    if duree < 1.0:
        raise RuntimeError(_("Source too short to be branded."))
    return Cadre(cadre.w, cadre.h, cadre.fps, round(duree, 3), cadre.audio)


def _entree(src: str, debut: float, duree: float) -> list[str]:
    ss = ["-ss", f"{debut:.3f}"] if debut > 0 else []
    return [*ss, "-t", f"{duree:.3f}", "-i", str(Path(src).resolve())]


def commande(o: dict, src: str, out: str, fc: str, extra: list[str], c: Chrono, debut: float = 0.0) -> list[str]:
    return [
        ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        *_entree(src, debut, c.v),
        *extra,
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "[ca]",
        "-t",
        f"{c.total:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        str(Path(out).resolve()),
    ]


def _lire_progres(flux, etat: list) -> None:
    for ligne in flux:
        if ligne.startswith("out_time_us="):
            try:
                etat[0] = max(etat[0], int(ligne.split("=", 1)[1]) / 1e6)
            except ValueError:
                pass


def _executer(taches: list[tuple[list[str], float, float]], cwd: str, avance=None) -> None:
    """Lance ensemble des commandes ffmpeg (commande, durée de sortie, poids) et attend la fin de toutes.

    avance(fraction) est appelé depuis CE fil (pas depuis les lecteurs) : la connexion à la base du
    back-office n'est jamais partagée entre fils. Une commande en échec arrête les autres."""
    procs = []
    try:
        for i, (cmd, duree, poids) in enumerate(taches):
            err = open(Path(cwd) / f"ffmpeg-{i}.err", "w", encoding="utf-8")  # noqa: SIM115
            pr = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=err, text=True)
            etat = [0.0]
            th = threading.Thread(target=_lire_progres, args=(pr.stdout, etat), daemon=True)
            th.start()
            procs.append((pr, etat, max(duree, 1e-3), poids, err, th))
        somme = sum(p[3] for p in procs) or 1.0
        while True:
            fini = [pr.poll() is not None for pr, *_ in procs]
            if any(pr.returncode for pr, *_ in procs if pr.returncode is not None):
                break
            if avance:
                avance(
                    sum((1.0 if f else min(1.0, e[0] / d)) * w for f, (_, e, d, w, *_) in zip(fini, procs, strict=True))
                    / somme
                )
            if all(fini):
                break
            time.sleep(0.5)
    finally:
        for pr, *_ in procs:
            if pr.poll() is None:
                pr.kill()
            pr.wait()
        for _pr, _e, _d, _w, err, th in procs:
            th.join(timeout=5)
            err.close()
    for pr, _e, _d, _w, err, _th in procs:
        if pr.returncode != 0:
            texte = Path(err.name).read_text(encoding="utf-8", errors="replace").strip().splitlines()
            raise RuntimeError(_("ffmpeg render failed: {error}", error=(texte or ["?"])[-1][:200]))


def _rendre_complet(o, src, part: Path, mosquee, brand, logo, cadre: Cadre, debut: float, td: str, avance) -> Chrono:
    """L'ancien rendu : toute la vidéo réencodée en une passe (barre sur toute la durée, source rétive)."""
    fc, extra, c = _preparer(o, src, Path(td), mosquee, brand, logo, cadre)
    _executer([(commande(o, src, str(part), fc, extra, c, debut), c.total, 1.0)], td, avance)
    return c


# ------------------------------------------------------------------ rendu par segments

MARGE_COUPE = 1.0  # s réencodées au-delà du bandeau et avant le fondu de fin
MILIEU_MIN = 20.0  # milieu copié plus court : le rendu complet ne coûte guère plus
PROFILS = {"Baseline": "baseline", "Constrained Baseline": "baseline", "Main": "main", "High": "high"}
VUI = (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"), ("color_space", "-colorspace"))


@dataclass(frozen=True)
class Plan:
    """Découpe de la source pour le rendu par segments (temps exacts, en secondes de la source).

    Tête [debut, k1) et queue [k2, fin) réencodées ; milieu [k1, k2) copié. k1 et k2 sont des images clés
    « nettes » : rien de ce qui précède ne s'affiche après, rien de ce qui suit ne s'affiche avant."""

    fps: Fraction
    debut: Fraction
    k1: Fraction
    k2: Fraction
    n_tete: int
    n_milieu: int
    n_queue: int
    x264: tuple[str, ...]  # profil, niveau, B-frames, couleurs : de quoi raccorder le flux copié

    @property
    def n(self) -> int:
        return self.n_tete + self.n_milieu + self.n_queue


def _flux_video(src: str) -> dict:
    exe = _ffprobe()
    if not exe:
        raise RuntimeError(_("ffprobe not found on this machine."))
    p = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", src],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return ((json.loads(p.stdout or "{}").get("streams") or [{}]) if p.returncode == 0 else [{}])[0]


def _paquets(src: str, debut: float, fin: float | None) -> list[tuple[int, bool]]:
    """(pts, image clé) des paquets vidéo, dans l'ordre de décodage ; [] si un pts manque."""
    exe = _ffprobe()
    if not exe:
        return []
    zone = []
    if debut > 0 or fin:
        zone = ["-read_intervals", f"{max(0.0, debut - 30):.3f}" + (f"%{fin + 30:.3f}" if fin else "")]
    p = subprocess.run(
        [
            exe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            *zone,
            "-show_entries",
            "packet=pts,flags",
            "-of",
            "csv=p=0",
            src,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = []
    for ligne in p.stdout.splitlines() if p.returncode == 0 else []:
        pts, _sep, flags = ligne.partition(",")
        if not pts.lstrip("-").isdigit():
            return []
        out.append((int(pts), "K" in flags))
    return out


def plan_segments(o: dict, src: str, cadre: Cadre, debut: float = 0.0, fin: float | None = None) -> Plan | None:
    """Où couper la source pour ne réencoder que la tête et la queue ; None : rendu complet.

    Il faut une source que la sortie peut prolonger sans réencodage : H.264 4:2:0 à la taille de sortie,
    cadence constante, sans rotation, au plus deux images de retard (B-frames) comme x264."""
    if o["bar_on"]:  # la barre court sur toute la vidéo : rien à copier tel quel
        return None
    v = _flux_video(src)
    rot = str((v.get("tags") or {}).get("rotate") or "0") not in ("0", "")
    rot = rot or any(int(float(sd.get("rotation") or 0)) % 360 for sd in v.get("side_data_list") or [])
    profil = PROFILS.get(v.get("profile") or "")
    retard = int(v.get("has_b_frames") or 0)
    if (
        v.get("codec_name") != "h264"
        or not profil
        or v.get("pix_fmt") != "yuv420p"
        or v.get("color_range") == "pc"
        or rot
        or (int(v.get("width") or 0), int(v.get("height") or 0)) != (cadre.w, cadre.h)
        or v.get("sample_aspect_ratio") not in (None, "1:1", "0:1")
        or v.get("field_order") not in (None, "progressive", "unknown")
        or retard > 2
    ):
        return None
    try:
        fps, tb = Fraction(v.get("r_frame_rate") or "0"), Fraction(v.get("time_base") or "0")
    except (ValueError, ZeroDivisionError):
        return None
    if not (10 <= fps <= 61) or tb <= 0:
        return None
    paq = _paquets(src, debut, fin)
    if len(paq) < 2:
        return None
    pas = 1 / fps
    pts = [p * tb for p, _ in paq]
    tri = sorted(pts)
    if any(abs((b - a) - pas) > pas / 4 for a, b in zip(tri, tri[1:], strict=False)):
        return None  # cadence variable ou images manquantes : la copie décalerait le son
    lo = Fraction(debut) - pas / 2
    hi = Fraction(fin) - pas / 2 if fin else None
    garde = [t for t in tri if t >= lo and (hi is None or t < hi)]
    if len(garde) < 2:
        return None
    t0, t_fin = garde[0], garde[-1] + pas
    # coupes nettes : maximum des pts avant < pts de la clé == minimum des pts à partir d'elle
    n = len(paq)
    suf = [Fraction(0)] * n
    m = None
    for j in range(n - 1, -1, -1):
        m = pts[j] if m is None or pts[j] < m else m
        suf[j] = m
    nettes, pre = [], None
    for j in range(n):
        if paq[j][1] and (pre is None or pre < pts[j]) and suf[j] == pts[j]:
            nettes.append(pts[j])
        pre = pts[j] if pre is None or pts[j] > pre else pre
    c = chrono(o, float(t_fin - t0))
    f = fenetre_bandeau(o, c)
    tete = Fraction(max((f[1] - c.i) if f else 0.0, FONDU if c.i else 0.0) + MARGE_COUPE)
    queue = Fraction((FONDU if c.o else 0.0) + MARGE_COUPE)
    k1 = min((k for k in nettes if k >= t0 + tete), default=None)
    k2 = max((k for k in nettes if k <= t_fin - queue), default=None)
    if k1 is None or k2 is None or k2 - k1 < MILIEU_MIN:
        return None
    from bisect import bisect_left

    i1, i2 = bisect_left(garde, k1), bisect_left(garde, k2)
    niveau = int(v.get("level") or 0)
    x264 = ["-profile:v", profil, "-bf", {0: "0", 1: "1", 2: "3"}[retard]]
    if niveau > 0:
        x264 += ["-level:v", f"{niveau // 10}.{niveau % 10}"]
    for cle, opt in VUI:
        if v.get(cle) and v[cle] not in ("unknown", "reserved"):
            x264 += [opt, str(v[cle])]
    return Plan(fps, t0, k1, k2, i1, i2 - i1, len(garde) - i2, tuple(x264))


def _segment(o: dict, cadre: Cadre, mosquee, brand, lg, p, td: Path, nom: str, images: dict) -> tuple[str, list[str]]:
    """Graphe d'un bout réencodé ; images {"[0:v]"|"[ci]"|"[co]": n} : coupes au nombre d'images près.

    trim=duration arrondit au pas de la cadence (une image de plus ou de moins selon 29,97 ou 30) : la
    jointure avec le milieu copié veut le compte exact, d'où trim=end_frame."""
    (td / f"{nom}.ass").write_text(build_ass(o, cadre, mosquee, brand, logo=bool(lg)), encoding="utf-8")
    fc, extra = graphe(o, cadre, f"{nom}.ass", bool(lg), p, apercu=True)
    for entree, n in images.items():
        a = fc.index(entree)
        b = fc.index("trim=duration=", a)
        fin = min(i for i in (fc.find(",", b), fc.find("[", b), len(fc)) if i >= 0)
        fc = fc[:b] + f"trim=end_frame={n}" + fc[fin:]
    return fc, extra


def _verifier_sortie(out: str, n: int) -> None:
    """Sortie lisible d'un bout à l'autre : n images, décodage croissant, aucun saut d'affichage."""
    exe = _ffprobe()
    p = subprocess.run(
        [
            exe or "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts,dts",
            "-of",
            "csv=p=0",
            out,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    lignes = [x.split(",") for x in p.stdout.splitlines() if x.strip()]
    try:
        pts = sorted(int(a) for a, _b in lignes)
        dts = [int(b) for _a, b in lignes]
    except ValueError:
        raise RuntimeError("timestamps manquants") from None
    if len(pts) != n:
        raise RuntimeError(f"{len(pts)} images au lieu de {n}")
    if any(b <= a for a, b in zip(dts, dts[1:], strict=False)):
        raise RuntimeError("dts non croissants")
    ecarts = {b - a for a, b in zip(pts, pts[1:], strict=False)}
    if ecarts and max(ecarts) > 1.5 * min(ecarts):
        raise RuntimeError("saut de timestamps")


def _rendre_segments(o, src, part: Path, mosquee, brand, logo, cadre: Cadre, plan: Plan, td: str, avance) -> Chrono:
    fps, pas, tdp = plan.fps, 1 / plan.fps, Path(td)
    cadre = replace(cadre, fps=fps)  # cadence exacte (30000/1001) : les images tombent sur la grille copiée
    besoin_logo = bool(logo) and (o["logo_on"] or o["intro_on"] or o["outro_on"])
    lg = (
        clipmod.logo_disc(str(logo), str(tdp / "logo.png"), size=round(220 * cadre.u), brand=brand)
        if besoin_logo
        else None
    )
    p = mise_en_page(o, cadre, mosquee, bool(lg))
    c0 = chrono(o, 1.0)
    n_i, n_o = round(c0.i * fps), round(c0.o * fps)

    def demi(k: int) -> float:  # filtres « pts < d » : exactement k images
        return float((k - Fraction(1, 2)) / fps)

    fond = fond_carton(str(tdp / "fond.png"), cadre, brand) if (n_i or n_o) else ""
    src_abs = str(Path(src).resolve())

    def cmd_bout(oo: dict, depart: Fraction, k: int, nom: str, n_sortie: int, images: dict) -> list[str]:
        cc = replace(cadre, duree=demi(k))
        fc, extra = _segment(oo, cc, mosquee, brand, lg, p, tdp, nom, {"[0:v]": k, **images})
        extra = [x.replace("{fond}", fond).replace("{logo}", lg or "") for x in extra]
        return [
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostats", "-y",
            # deux images de marge à l'entrée : le filtre fps perd la dernière en fin de flux ; trim coupe juste
            "-ss", f"{float(depart - pas / 4):.6f}", "-t", f"{float((k + 2) * pas):.6f}", "-i", src_abs, *extra,
            "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n_sortie),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", *plan.x264,
            "-f", "mpegts", "-progress", "pipe:1", f"{nom}.ts",
        ]  # fmt: skip

    o_tete = {**o, "outro_on": False, "intro_s": demi(n_i) if n_i else o["intro_s"]}
    o_queue = {**o, "intro_on": False, "lower_on": False, "outro_s": demi(n_o) if n_o else o["outro_s"]}
    n_tete, n_queue = n_i + plan.n_tete, plan.n_queue + n_o
    milieu = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-ss", f"{float(plan.k1 + pas / 4):.6f}", "-i", src_abs, "-map", "0:v:0", "-c", "copy",
        "-frames:v", str(plan.n_milieu), "-f", "mpegts", "-progress", "pipe:1", "milieu.ts",
    ]  # fmt: skip
    # Le son d'un seul tenant : silence du carton, son de la source, silence de fin, encodés en une passe.
    # Coller des morceaux AAC laisserait à chaque jointure un trou ou un clic (trames de 1024 échantillons,
    # amorce de l'encodeur) ; réencoder le son est de toute façon rapide.
    v, total = float(plan.n * pas), float((n_i + plan.n + n_o) * pas)
    morceaux, fa = [], []
    if n_i:
        fa.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={float(n_i * pas):.6f}[ia]")
        morceaux.append("[ia]")
    if cadre.audio:
        fa.append(
            f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=duration={v:.6f},asetpts=PTS-STARTPTS"
            + (",afade=t=in:d=0.3" if n_i else "")
            + (f",afade=t=out:st={max(0.0, v - 0.6):.6f}:d=0.6" if n_o else "")
            + "[a0]"
        )
    else:
        fa.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={v:.6f}[a0]")
    morceaux.append("[a0]")
    if n_o:
        fa.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={float(n_o * pas):.6f}[oa]")
        morceaux.append("[oa]")
    fa.append("".join(morceaux) + f"concat=n={len(morceaux)}:v=0:a=1[ca]")
    entree_son = ["-ss", f"{float(plan.debut):.6f}", "-t", f"{v + 1:.6f}", "-i", src_abs] if cadre.audio else []
    son = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostats", "-y", *entree_son,
        "-filter_complex", ";".join(fa), "-map", "[ca]", "-t", f"{total:.6f}",
        "-c:a", "aac", "-b:a", "160k", "-f", "mp4", "-progress", "pipe:1", "son.m4a",
    ]  # fmt: skip
    _executer(
        [
            (
                cmd_bout(o_tete, plan.debut, plan.n_tete, "tete", n_tete, {"[ci]": n_i} if n_i else {}),
                n_tete * float(pas),
                n_tete * float(pas),
            ),
            (
                cmd_bout(o_queue, plan.k2, plan.n_queue, "queue", n_queue, {"[co]": n_o} if n_o else {}),
                n_queue * float(pas),
                n_queue * float(pas),
            ),
            (milieu, plan.n_milieu * float(pas), plan.n_milieu * float(pas) * 0.004),
            (son, total, total * 0.01),
        ],
        td,
        lambda f: avance(f, 0.0, 0.95),
    )
    liste = ["ffconcat version 1.0"]
    for nom, k in (("tete", n_tete), ("milieu", plan.n_milieu), ("queue", n_queue)):
        liste += [f"file {nom}.ts", f"duration {float(k * pas):.6f}"]
    (tdp / "segments.ffconcat").write_text("\n".join(liste) + "\n", encoding="utf-8")
    assemble = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-f", "concat", "-safe", "0", "-i", "segments.ffconcat", "-i", "son.m4a",
        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart",
        "-progress", "pipe:1", str(part.resolve()),
    ]  # fmt: skip
    _executer([(assemble, total, 1.0)], td, lambda f: avance(f, 0.95, 0.99))
    _verifier_sortie(str(part), n_tete + plan.n_milieu + n_queue)
    return Chrono(n_i * float(pas), v, n_o * float(pas))


def rendre(
    o: dict,
    src: str,
    out: str,
    mosquee: str,
    brand: dict | None = None,
    logo: str | None = None,
    progres=None,
    log=print,
    debut: float = 0.0,
    fin: float | None = None,
    segments: bool = True,
) -> str:
    """Vidéo habillée complète dans `out` (écrite à côté puis renommée : jamais de fichier à moitié servi).

    Par segments quand la source s'y prête (seuls la tête et la queue sont réencodées), sinon en une
    passe. progres(fraction) est appelé pendant le rendu, toujours croissant, jamais 1 avant la fin."""
    verifier(o)
    cadre = _bornes(sonder(src), debut, fin)
    dest = Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    vu = [0.0]

    def avance(f: float, a: float = 0.0, b: float = 1.0) -> None:
        g = min(0.999, a + (b - a) * max(0.0, min(1.0, f)))
        if g > vu[0]:
            vu[0] = g
            if progres:
                progres(g)

    t0 = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as td:
            plan = plan_segments(o, src, cadre, debut, fin) if segments else None
            c = None
            if plan:
                try:
                    c = _rendre_segments(o, src, part, mosquee, brand, logo, cadre, plan, td, avance)
                    log(
                        _(
                            "segmented render: {copied:.0f}s copied, {encoded:.0f}s encoded",
                            copied=float(plan.n_milieu / plan.fps),
                            encoded=c.total - float(plan.n_milieu / plan.fps),
                        )
                    )
                except RuntimeError as e:
                    log(_("segmented render impossible ({error}): full render", error=e))
                    c = None
            if c is None:
                base = vu[0]
                c = _rendre_complet(
                    o, src, part, mosquee, brand, logo, cadre, debut, td, lambda f: avance(f, base, 1.0)
                )
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    log(_("branding -> {path} ({seconds:.0f}s)", path=dest, seconds=c.total) + f" [{time.monotonic() - t0:.0f}s]")
    return str(dest)


def instants_apercu(o: dict, c: Chrono) -> list[tuple[str, float]]:
    """Moments montrés dans l'aperçu : un par élément actif (carton, bandeau, image courante, fin)."""
    out = []
    if c.i:
        out.append(("intro", c.i * 0.62))
    f = fenetre_bandeau(o, c)
    if f:
        out.append(("bandeau", min(f[1] - 0.3, f[0] + 1.6)))
    if o["bar_on"]:  # le logo, lui, n'est plus montré hors des cartons et du bandeau
        t = (f[1] + 1.2) if f else c.i + min(3.0, c.v / 2)
        if t < c.fin_video - 0.3:
            out.append(("video", t))
    if c.o:
        out.append(("fin", c.fin_video + c.o * 0.62))
    return out or [("video", c.i + c.v / 2)]


def apercu(
    o: dict,
    src: str,
    out: str,
    mosquee: str,
    brand: dict | None = None,
    logo: str | None = None,
    debut: float = 0.0,
    fin: float | None = None,
) -> str:
    """Planche JPEG d'aperçu : une image par élément actif, rendue par le même graphe que la vraie vidéo.

    Seul le début de la source est décodé (ce qu'il faut pour voir le bandeau) : l'aperçu d'un dars d'une
    heure prend quelques secondes."""
    verifier(o)
    cadre = _bornes(sonder(src), debut, fin)
    besoin = (o["lower_at"] + o["lower_s"] + 3.5) if o["lower_on"] else 6.0
    court = Cadre(cadre.w, cadre.h, cadre.fps, min(cadre.duree, besoin), cadre.audio)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        besoin_logo = bool(logo) and (o["logo_on"] or o["intro_on"] or o["outro_on"])
        lg = (
            clipmod.logo_disc(str(logo), str(tdp / "logo.png"), size=round(220 * court.u), brand=brand)
            if besoin_logo
            else None
        )
        p = mise_en_page(o, court, mosquee, bool(lg))
        (tdp / "habillage.ass").write_text(build_ass(o, court, mosquee, brand, logo=bool(lg)), encoding="utf-8")
        c = chrono(o, court.duree)
        fc, extra = graphe(o, court, "habillage.ass", bool(lg), p, apercu=True)
        fond = fond_carton(str(tdp / "fond.png"), court, brand) if (c.i or c.o) else ""
        extra = [x.replace("{fond}", fond).replace("{logo}", lg or "") for x in extra]
        moments = instants_apercu(o, c)
        images = sorted({min(round(t * court.fps), max(0, round(c.total * court.fps) - 2)) for _, t in moments})
        sel = "+".join(f"eq(n\\,{i})" for i in images)
        fc = fc[: -len("[v]")] + f",select='{sel}'[v]"
        cmd = [
            ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *_entree(src, debut, court.duree),
            *extra,
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-t",
            f"{c.total:.3f}",
            "-fps_mode",
            "passthrough",
            "img_%02d.png",
        ]
        p_ = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=300)
        vues = sorted(tdp.glob("img_*.png"))
        if p_.returncode != 0 or not vues:
            last = (p_.stderr or "").strip().splitlines() or ["?"]
            raise RuntimeError(_("preview failed: {error}", error=last[-1][:200]))
        planche(vues, out)
    return out


def planche(vues: list[Path], out: str, largeur: int = 960) -> str:
    """Assemble les images en une planche (2 colonnes, gouttière sombre), JPEG."""
    from PIL import Image

    ims = [Image.open(v).convert("RGB") for v in vues]
    w0, h0 = ims[0].size
    h = round(largeur * h0 / w0)
    cols = 1 if len(ims) == 1 else 2
    rows = math.ceil(len(ims) / cols)
    g = 14
    sheet = Image.new("RGB", (cols * largeur + (cols + 1) * g, rows * h + (rows + 1) * g), (11, 26, 20))
    for i, im in enumerate(ims):
        r, k = divmod(i, cols)
        sheet.paste(im.resize((largeur, h), Image.Resampling.LANCZOS), (g + k * (largeur + g), g + r * (h + g)))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=88)
    return out
