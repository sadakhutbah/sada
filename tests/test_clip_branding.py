"""Habillage des clips : logo de la mosquée et couleurs configurées (repli sur la charte actuelle)."""
import subprocess

from PIL import Image

from sada.pipeline import clips as C


def _png(path, color=(180, 30, 30), size=(128, 128)):
    Image.new("RGB", size, color).save(path, "PNG")
    return str(path)


def _lines(path):
    return open(path, encoding="utf-8").read().splitlines()


def test_couleurs_par_defaut_sans_reglage():
    gold, panel = C.brand_colours(None)
    assert gold == C.GOLD and panel == C.PANEL
    gold, panel = C.brand_colours({"green": "pas une couleur", "gold": "#zzzzzz"})
    assert gold == C.GOLD and panel == C.PANEL


def test_couleurs_de_la_mosquee_converties_en_ass():
    gold, panel = C.brand_colours({"green": "#1E7A3C", "gold": "#C8A96A"})
    assert gold == "&H006AA9C8&"                       # BGR de #C8A96A
    assert panel == "&H100D1B07&"                      # vert assombri (BGR, alpha 0x10)
    assert C.brand_colours({"gold": "C8A96A"})[0] == gold   # sans # accepté


def test_ass_utilise_les_couleurs_et_le_logo_dans_l_entete(tmp_path):
    p = tmp_path / "c.ass"
    C.build_ass([{"start": 0, "end": 3, "text": "La taqwa est une lumière."}], [], 0, 6, str(p),
                header={"title": "Khutba du vendredi", "subtitle": "Mosquée de test · 11 novembre"},
                brand={"green": "#1E7A3C", "gold": "#C8A96A"}, logo=True)
    txt = p.read_text(encoding="utf-8")
    assert "Style: Head2,Montserrat Bold,32,&H006AA9C8&" in txt
    assert "Style: Card,Montserrat,20,&H100D1B07&" in txt
    assert "Style: Glow,Montserrat ExtraBold" in txt
    assert "\\pos(540,258)" in txt                      # titre sous le logo
    assert "\\pos(540,308)" in txt                      # sous-titre de la mosquée


def test_sans_logo_l_entete_ne_bouge_pas(tmp_path):
    p = tmp_path / "c.ass"
    C.build_ass([], [], 0, 6, str(p), header={"title": "Dars", "subtitle": "Mosquée"})
    txt = p.read_text(encoding="utf-8")
    assert "\\pos(540,118)" in txt and "\\pos(540,168)" in txt


def test_logo_decoupe_en_disque_avec_anneau_dore(tmp_path):
    src = _png(tmp_path / "logo.png")
    out = C.logo_disc(src, str(tmp_path / "disc.png"), brand={"gold": "#C8A96A"})
    assert out and (tmp_path / "disc.png").exists()
    im = Image.open(out).convert("RGBA")
    assert im.size == (C.LOGO_SIZE * 2, C.LOGO_SIZE * 2)
    assert im.getpixel((0, 0))[3] == 0                 # coins transparents : disque
    assert im.getpixel((im.width // 2, im.height // 2))[:3] == (180, 30, 30)
    ring = im.getpixel((3, im.height // 2))
    assert ring[3] == 255 and ring[0] > ring[1]        # anneau doré sur le bord


def test_logo_illisible_ne_casse_pas_le_clip(tmp_path):
    assert C.logo_disc(str(tmp_path / "absent.svg"), str(tmp_path / "x.png")) is None


def _video(path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x2E6B45:s=640x360:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return str(path)


def _frame(video, t, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", video, "-frames:v", "1", str(out)],
                   check=True)
    return Image.open(out).convert("RGB")


def test_clip_rendu_avec_logo_et_couleurs(tmp_path):
    """Rendu réel : logo en tête, couleurs de la mosquée, sous-titres brûlés."""
    video = _video(tmp_path / "src.mp4")
    logo = _png(tmp_path / "logo.png")
    ass = tmp_path / "clip.ass"
    C.build_ass([{"start": 0.2, "end": 2.6, "text": "La taqwa est une lumière pour le croyant."}],
                [], 0.0, 3.0, str(ass),
                header={"title": "Khutba du vendredi", "subtitle": "Mosquée de test · 11 novembre"},
                brand={"green": "#1E7A3C", "gold": "#C8A96A"}, logo=True)
    out = tmp_path / "clip.mp4"
    C.render_clip(video, 0.0, {"start": 0.0, "end": 3.0}, ass.name, str(out), log=lambda *_: None,
                  logo=logo, brand={"green": "#1E7A3C", "gold": "#C8A96A"})
    assert out.exists() and out.stat().st_size > 1000
    im = _frame(str(out), 1.0, tmp_path / "f1.png")
    assert im.size == (C.W, C.H)
    cx, cy = C.W // 2, 78 + C.LOGO_SIZE // 2
    r, g, b = im.getpixel((cx, cy))
    assert r > 120 and r > g + 40              # centre du logo (rouge du fichier de test)
    corner = im.getpixel((30, cy))
    assert corner[1] > corner[0]               # fond vert de la vidéo hors du disque
