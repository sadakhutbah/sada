"""Habillage des vidéos : options, ASS et graphe ffmpeg selon les éléments choisis, API, vrai rendu court."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sada.pipeline import habillage as h

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
CADRE = h.Cadre(1920, 1080, 25.0, 60.0, True)
MOSQUEE = "Mosquée Exemple"


def opts(**k):
    base = {
        "title": "Le tafsir de la sourate Al-Kahf",
        "subtitle": "Série Al-Kahf · séance 3",
        "speaker": "Cheikh Abdallah",
        "role": "Imam de la mosquée",
        "outro_link": "mosquee.fr",
    }
    base.update(k)
    return h.normaliser(base)


# ------------------------------------------------------------------ options


def test_normaliser_borne_et_convertit():
    o = h.normaliser(
        {
            "intro_s": "99",
            "lower_s": "1",
            "logo_opacity": "0,5",
            "bar_on": "1",
            "outro_on": "non",
            "logo_on": False,
            "title": "  Un   titre \n long  ",
            "inconnu": "x",
        }
    )
    assert o["intro_s"] == 15.0 and o["lower_s"] == 3.0 and o["logo_opacity"] == 0.5
    assert o["bar_on"] is True and o["outro_on"] is False and o["logo_on"] is False
    assert o["title"] == "Un titre long" and "inconnu" not in o
    # la base (réglages enregistrés) est complétée par le formulaire
    assert h.normaliser({"title": "B"}, {"title": "A", "logo_pos": "bl"})["logo_pos"] == "bl"


@pytest.mark.parametrize(
    "brut",
    [{"logo_pos": "milieu"}, {"logo_size": "xxl"}, {"intro_s": "deux"}, {"intro_s": "nan"}, {"bar_on": "peut-être"}],
)
def test_normaliser_refuse_les_valeurs_illisibles(brut):
    with pytest.raises(ValueError):
        h.normaliser(brut)


def test_verifier_refuse_un_element_vide():
    with pytest.raises(ValueError, match="title"):
        h.verifier(opts(title=""))
    with pytest.raises(ValueError, match="speaker"):
        h.verifier(opts(speaker=""))
    with pytest.raises(ValueError, match="No branding"):
        h.verifier(h.normaliser({k: False for k in h.BOOLEENS}))
    h.verifier(opts(title="", intro_on=False))


# ------------------------------------------------------------------ chronologie


def test_chronologie_carton_video_fin():
    c = h.chrono(opts(intro_s=5, outro_s=6), 60)
    assert (c.i, c.v, c.o, c.total, c.fin_video) == (5, 60, 6, 71, 65)
    assert h.chrono(opts(intro_on=False, outro_on=False), 60).total == 60


def test_bandeau_dans_la_video_et_jamais_au_dela():
    c = h.chrono(opts(intro_s=5), 60)
    assert h.fenetre_bandeau(opts(lower_at=2, lower_s=8), c) == (7, 15)
    # source courte : le bandeau est coupé à la fin de la vidéo, pas sur l'écran de fin
    court = h.chrono(opts(intro_s=5), 6)
    a, b = h.fenetre_bandeau(opts(lower_at=2, lower_s=8), court)
    assert a == 7 and b == court.fin_video
    assert h.fenetre_bandeau(opts(lower_on=False), c) is None


# ------------------------------------------------------------------ ASS


def _dialogues(ass: str) -> list[str]:
    return [ligne for ligne in ass.splitlines() if ligne.startswith("Dialogue:")]


def test_ass_complet_tous_elements():
    ass = h.build_ass(
        opts(bar_on=True, outro_text="Retrouvez nos cours"),
        CADRE,
        MOSQUEE,
        {"green": "#1E5A44", "gold": "#E8BE50"},
        logo=True,
    )
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    d = "\n".join(_dialogues(ass))
    assert "MOSQUÉE EXEMPLE" in d  # surtitre du carton
    assert "Le tafsir de la sourate Al-Kahf" in d
    assert "Cheikh Abdallah" in d and "IMAM DE LA MOSQUÉE" in d
    assert "\\clip(" in d  # bandeau révélé en volet
    assert "Retrouvez nos cours" in d and "mosquee.fr" in d and "Mosquée Exemple" in d
    # doré de la mosquée (BGR) dans le filet et la barre
    assert "&H0050BEE8&" in d
    # le bandeau commence après le carton (5 s) + son instant d'apparition (2 s)
    assert any(ligne.startswith("Dialogue: 3,0:00:07.00,") for ligne in _dialogues(ass))
    # la barre couvre toute la vidéo, pas les cartons
    assert any(ligne.startswith("Dialogue: 3,0:00:05.00,0:01:05.00,") for ligne in _dialogues(ass))


def test_ass_elements_coupes_absents():
    ass = h.build_ass(opts(intro_on=False, outro_on=False, bar_on=False), CADRE, MOSQUEE)
    d = "\n".join(_dialogues(ass))
    assert "Le tafsir" not in d and "MOSQUÉE EXEMPLE" not in d and "mosquee.fr" not in d
    assert "Cheikh Abdallah" in d
    assert any(ligne.startswith("Dialogue: 3,0:00:02.00,") for ligne in _dialogues(ass))  # sans carton : 2 s
    rien = h.build_ass(opts(intro_on=False, lower_on=False, outro_on=False), CADRE, MOSQUEE)
    assert _dialogues(rien) == []


def test_ass_echappe_les_accolades():
    d = "\n".join(_dialogues(h.build_ass(opts(title="Titre {\\b1} piégé"), CADRE, MOSQUEE)))
    assert "{\\b1}" not in d and "(b1)" in d


def test_titre_arabe_en_naskh():
    ass = h.build_ass(opts(title="تفسير سورة الكهف"), CADRE, MOSQUEE)
    assert any(",TitreAr," in ligne for ligne in _dialogues(ass))


def test_video_verticale_garde_ses_proportions():
    assert h.taille_sortie(1080, 1920) == (1080, 1920)
    assert h.taille_sortie(1280, 720) == (1920, 1080)
    assert h.taille_sortie(640, 480) == (1440, 1080)


# ------------------------------------------------------------------ mise en page et graphe ffmpeg


@pytest.mark.parametrize("pos", h.POSITIONS)
def test_filigrane_dans_son_coin(pos):
    p = h.mise_en_page(opts(logo_pos=pos), CADRE, MOSQUEE, logo=True)
    x, y, d = p["filigrane"]
    assert (x < CADRE.w / 2) == (pos[1] == "l") and (y < CADRE.h / 2) == (pos[0] == "t")
    assert 0 <= x and x + d <= CADRE.w and 0 <= y and y + d <= CADRE.h


def test_bandeau_remonte_au_dessus_du_logo_en_bas_a_gauche():
    haut = h.mise_en_page(opts(logo_pos="bl"), CADRE, MOSQUEE, logo=True)
    bas = h.mise_en_page(opts(logo_pos="tr"), CADRE, MOSQUEE, logo=True)
    assert haut["bandeau"]["y"] + haut["bandeau"]["h"] < haut["filigrane"][1]
    assert haut["bandeau"]["y"] < bas["bandeau"]["y"]


def test_graphe_complet():
    o = opts(logo_opacity=0.6)
    p = h.mise_en_page(o, CADRE, MOSQUEE, logo=True)
    fc, extra = h.graphe(o, CADRE, "habillage.ass", True, p)
    assert extra.count("-i") == 2 and "{fond}" in extra and "{logo}" in extra
    assert "concat=n=3:v=1:a=1" in fc
    assert "colorchannelmixer=aa=0.60" in fc
    x, y, _ = p["filigrane"]
    assert f"overlay={x}:{y}" in fc
    assert fc.count("overlay=") == 3  # filigrane + logo du carton + logo de fin
    assert "subtitles=filename=habillage.ass" in fc and fc.endswith("[v]")


def test_graphe_sans_carton_ni_logo():
    o = opts(intro_on=False, outro_on=False, logo_on=False)
    fc, extra = h.graphe(o, CADRE, "h.ass", False, h.mise_en_page(o, CADRE, MOSQUEE, logo=False))
    assert extra == [] and "overlay" not in fc and "concat=n=1" in fc and "fade=t=" not in fc


def test_graphe_source_muette_et_apercu():
    o = opts()
    muet = h.Cadre(1920, 1080, 25.0, 30.0, audio=False)
    fc, _ = h.graphe(o, muet, "h.ass", False, h.mise_en_page(o, muet, MOSQUEE, logo=False))
    assert "[0:a]" not in fc and "anullsrc" in fc
    fc, _ = h.graphe(o, CADRE, "h.ass", False, h.mise_en_page(o, CADRE, MOSQUEE, logo=False), apercu=True)
    assert "[0:a]" not in fc and "anullsrc" not in fc and "v=1:a=0" in fc


def test_instants_apercu_un_par_element():
    o = opts(bar_on=True)
    noms = [n for n, _ in h.instants_apercu(o, h.chrono(o, 60))]
    assert noms == ["intro", "bandeau", "video", "fin"]
    o = opts(intro_on=False, outro_on=False, logo_on=False)
    assert [n for n, _ in h.instants_apercu(o, h.chrono(o, 60))] == ["bandeau"]


# ------------------------------------------------------------------ vrai rendu


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg absent")
    d = tmp_path_factory.mktemp("src")
    f = d / "dars.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(f),
        ],
        check=True,
    )
    return f


@pytest.fixture(scope="module")
def logo(tmp_path_factory):
    from PIL import Image, ImageDraw

    f = tmp_path_factory.mktemp("logo") / "logo.png"
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((8, 8, 248, 248), fill=(30, 90, 68, 255))
    im.save(f)
    return f


def test_vrai_rendu_court(video, logo, tmp_path):
    o = opts(intro_s=2, outro_s=2, lower_at=0.5, lower_s=3, bar_on=True)
    out = h.rendre(
        o, str(video), str(tmp_path / "out.mp4"), MOSQUEE, {"green": "#1E5A44"}, str(logo), log=lambda *a: None
    )
    c = h.sonder(out)
    assert (c.w, c.h) == (1920, 1080) and c.audio
    assert abs(c.duree - 10.0) < 0.3  # 2 s + 6 s + 2 s
    assert not list(tmp_path.glob("*.part*"))
    assert video.stat().st_size > 0  # l'original est intact


def test_rendu_coupe_aux_bornes_de_la_session(video, tmp_path):
    o = opts(intro_on=False, outro_on=False, lower_on=False, logo_on=False, bar_on=True)
    out = h.rendre(o, str(video), str(tmp_path / "o.mp4"), MOSQUEE, debut=1.0, fin=4.0, log=lambda *a: None)
    assert abs(h.sonder(out).duree - 3.0) < 0.3


def test_vrai_apercu(video, logo, tmp_path):
    from PIL import Image

    out = h.apercu(
        opts(intro_s=2, outro_s=2, lower_at=0.5, lower_s=3),
        str(video),
        str(tmp_path / "a.jpg"),
        MOSQUEE,
        None,
        str(logo),
    )
    im = Image.open(out)
    assert im.format == "JPEG" and im.width > im.height


# ------------------------------------------------------------------ API

fastapi = pytest.importorskip("fastapi")
from test_backoffice import client  # noqa: E402


def test_api_prerempli_sans_chemin(env):
    from sada.backoffice import db

    db.one(env["conn"], "SELECT 1")
    env["conn"].execute(
        "UPDATE khutbas SET title = 'Patience et gratitude', series = 'Les vertus', session_no = 2, "
        "date = '2026-11-06' WHERE id = ?",
        (env["kid"],),
    )
    c = client(env, "admin")
    r = c.get(f"/api/v1/sermons/{env['kid']}/habillage")
    assert r.status_code == 200, r.text
    d = r.json()
    o = d["options"]
    assert o["title"] == "Patience et gratitude"
    assert o["subtitle"] == "Les vertus · séance 2 · 6 novembre 2026"
    assert o["speaker"] == "Imam"  # nom affiché de l'imam rattaché
    assert d["status"] == "" and d["has_source"] is False and d["can_edit"] is True
    assert d["mosque"] == "Mosquée Exemple" and d["colors"]["gold"].startswith("#")
    assert str(env["tmp"]) not in r.text  # aucun chemin de la machine


def test_api_ecriture_reservee(env):
    c = client(env, "imam")
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage").json()["can_edit"] is False
    r = c.ecrire(f"/sermons/{env['kid']}/habillage", {"options": {}})
    assert r.status_code == 403
    # sans jeton anti-rejeu
    r = client(env, "admin").post(f"/api/v1/sermons/{env['kid']}/habillage", json={"options": {}})
    assert r.status_code == 403


def test_api_sans_source_refuse_proprement(env):
    c = client(env, "admin")
    r = c.ecrire(f"/sermons/{env['kid']}/habillage", {"options": {"title": "T", "speaker": "S"}})
    assert r.status_code == 422 and "source" in r.text
    r = c.ecrire(f"/sermons/{env['kid']}/habillage", {"options": {"logo_pos": "ailleurs"}})
    assert r.status_code == 422
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage/video.mp4").status_code == 404
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage/apercu.jpg").status_code == 404


def test_api_apercu_puis_rendu(env, video):
    from sada.backoffice import db

    conn, kid = env["conn"], env["kid"]
    conn.execute("UPDATE khutbas SET video_path = ? WHERE id = ?", (str(video), kid))
    c = client(env, "admin")
    options = {
        "title": "Patience",
        "speaker": "Cheikh Test",
        "intro_s": "2",
        "outro_s": "2",
        "lower_at": "0.5",
        "lower_s": "3",
        "logo_pos": "bl",
        "bar_on": True,
        "outro_link": "mosquee-exemple.fr",
    }
    r = c.ecrire(f"/sermons/{kid}/habillage/apercu", {"options": options})
    assert r.status_code == 200, r.text
    assert r.json()["preview_ready"] is True
    img = c.get(f"/api/v1/sermons/{kid}/habillage/apercu.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    r = c.ecrire(f"/sermons/{kid}/habillage", {"options": options})
    assert r.status_code == 200, r.text
    d = c.get(f"/api/v1/sermons/{kid}/habillage").json()
    assert d["status"] == "pret" and d["video_ready"] and d["progress"] == 1
    assert d["options"]["logo_pos"] == "bl" and abs(d["duration_s"] - 10) < 0.5
    v = c.get(f"/api/v1/sermons/{kid}/habillage/video.mp4?download=1")
    assert v.status_code == 200 and "attachment" in v.headers["content-disposition"]
    assert (
        "habillee.mp4" in v.headers["content-disposition"] and str(env["tmp"]) not in v.headers["content-disposition"]
    )
    assert video.exists()  # la source n'est pas touchée
    # la mosquée retient son identité (pas le titre de ce dars)
    m = db.one(conn, "SELECT habillage_defaults FROM mosques WHERE id = ?", env["mid"])
    retenu = json.loads(m["habillage_defaults"])
    assert retenu["outro_link"] == "mosquee-exemple.fr" and retenu["logo_pos"] == "bl" and "title" not in retenu
    actions = [a["action"] for a in db.all_(conn, "SELECT action FROM audit")]
    assert "habillage.lance" in actions and "habillage.pret" in actions


def test_nouveau_rendu_garde_l_ancienne_video_jusqu_au_remplacement(env, video, monkeypatch):
    from sada.backoffice import habillage as bo
    from sada.pipeline import habillage as rendu

    conn, kid = env["conn"], env["kid"]
    conn.execute("UPDATE khutbas SET video_path = ? WHERE id = ?", (str(video), kid))
    c = client(env, "admin")
    assert (
        c.ecrire(
            f"/sermons/{kid}/habillage",
            {
                "options": {
                    "title": "Un",
                    "intro_s": "2",
                    "speaker": "Cheikh Test",
                    "outro_s": "2",
                    "lower_at": "0.5",
                    "lower_s": "3",
                }
            },
        ).status_code
        == 200
    )
    k = conn.execute("SELECT * FROM khutbas WHERE id = ?", (kid,)).fetchone()
    k = dict(k)
    ancienne = bo.fichier_video(k).read_bytes()

    # Pendant le rendu, l'ancienne vidéo reste servie ; un échec ne la touche pas.
    def echoue(o, src, sortie, *a, **kw):
        assert bo.fichier_video(k).exists() and sortie != str(bo.fichier_video(k))
        Path(sortie).write_bytes(b"partiel")
        raise RuntimeError("boom")

    monkeypatch.setattr(rendu, "rendre", echoue)
    c.ecrire(
        f"/sermons/{kid}/habillage",
        {
            "options": {
                "title": "Deux",
                "intro_s": "4",
                "speaker": "Cheikh Test",
                "outro_s": "2",
                "lower_at": "0.5",
                "lower_s": "3",
            }
        },
    )
    d = c.get(f"/api/v1/sermons/{kid}/habillage").json()
    assert d["status"] == "echec" and d["video_ready"]
    assert bo.fichier_video(k).read_bytes() == ancienne and not bo.fichier_rendu_en_cours(k).exists()
    assert c.get(f"/api/v1/sermons/{kid}/habillage/video.mp4").status_code == 200
    # le décalage affiché reste celui de la vidéo en place, pas des nouvelles options
    assert c.get(f"/api/v1/sermons/{kid}").json()["dressed"]["offset_s"] == 2

    monkeypatch.undo()
    assert (
        c.ecrire(
            f"/sermons/{kid}/habillage",
            {
                "options": {
                    "title": "Trois",
                    "intro_s": "4",
                    "speaker": "Cheikh Test",
                    "outro_s": "2",
                    "lower_at": "0.5",
                    "lower_s": "3",
                }
            },
        ).status_code
        == 200
    )
    assert bo.fichier_video(k).read_bytes() != ancienne and not bo.fichier_rendu_en_cours(k).exists()
    assert c.get(f"/api/v1/sermons/{kid}").json()["dressed"]["offset_s"] == 4


def test_rendu_interrompu_marque_en_echec(env):
    from sada.backoffice import db
    from sada.backoffice import habillage as bo

    conn = env["conn"]
    conn.execute(
        "INSERT INTO habillages (mosque_id, khutba_id, status, updated_at) VALUES (?, ?, 'en_cours', 'x')",
        (env["mid"], env["kid"]),
    )
    bo.recover_interrupted(conn)
    assert db.one(conn, "SELECT status FROM habillages")["status"] == "echec"


def test_erreur_sans_chemin():
    from sada.backoffice.habillage import sans_chemin

    assert sans_chemin("/home/x/data/output/run/source/dars.mp4: Invalid data") == "dars.mp4: Invalid data"


def test_migration_8_sur_une_base_en_version_6(tmp_path):
    from sada.backoffice import db, migrations

    path = tmp_path / "bo.db"
    db.init(path)
    conn = db.connect(path)
    conn.execute("DROP TABLE habillages")
    conn.execute("ALTER TABLE mosques DROP COLUMN habillage_defaults")
    conn.execute("DELETE FROM schema_migrations WHERE version = 8")
    conn.close()
    db.init(path)
    conn = db.connect(path)
    try:
        assert db.one(conn, "SELECT name FROM sqlite_master WHERE name = 'habillages'")
        assert "habillage_defaults" in {r[1] for r in conn.execute("PRAGMA table_info(mosques)")}
        assert max(r[0] for r in conn.execute("SELECT version FROM schema_migrations")) == migrations.LATEST
    finally:
        conn.close()
