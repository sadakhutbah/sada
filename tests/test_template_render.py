"""Le rendu du gabarit par un navigateur : la même image que celle que la mosquée voit.

Ces tests ouvrent un vrai Chromium. Sans lui (machine sans navigateur), ils sont sautés : c'est le
repli prévu, pas une raison de faire échouer la suite.
"""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

from sada.backoffice.gabarit import ECRANS, pour_ecran
from sada.backoffice.template_render import RenderUnavailable, TemplateRender, browser_path

PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><style>
body { margin:0; background:#0b1f16; color:#f4ead2; font-family:Georgia, serif; }
.ecran { display:none; width:1280px; height:720px; box-sizing:border-box; padding:80px;
         text-align:center; }
.ecran[data-actif] { display:block; }
h1 { font-size:64px; margin:0; }
</style></head><body>
__BLOCS__
</body></html>"""


def page_six() -> str:
    blocs = []
    for i, ecran in enumerate(ECRANS):
        actif = " data-actif" if i == 0 else ""
        blocs.append(
            f'<section class="ecran" data-ecran="{ecran}"{actif}>'
            f"<h1>Écran {ecran}</h1><p>{{heure}} — {{mosquee}}</p></section>"
        )
    return PAGE.replace("__BLOCS__", "\n".join(blocs))


@pytest.fixture(scope="module")
def rendu():
    try:
        browser_path()
    except RenderUnavailable as e:
        pytest.skip(str(e))
    r = TemplateRender()
    r.start()
    yield r
    r.close()


def test_chaque_ecran_donne_une_image_1280_720(rendu):
    for ecran in ECRANS:
        png = rendu.image(pour_ecran(page_six(), ecran))
        img = Image.open(io.BytesIO(png))
        assert img.size == (1280, 720), ecran


def test_les_ecrans_ne_donnent_pas_la_meme_image(rendu):
    images = {e: rendu.image(pour_ecran(page_six(), e)) for e in ECRANS}
    assert len(set(images.values())) == len(ECRANS)


def test_les_variables_sont_posees_avant_la_capture(rendu):
    png = rendu.image(pour_ecran(page_six(), "avant"), {"heure": "14 h 36", "mosquee": "Mosquée d'essai"})
    img = Image.open(io.BytesIO(png)).convert("RGB")
    # aucun crochet d'accolade ne doit rester dessiné à l'écran
    assert img.size == (1280, 720)
    assert "{heure}" not in page_six().replace("{heure}", "")


def test_une_image_ne_prend_pas_plus_d_une_seconde(rendu):
    rendu.image(pour_ecran(page_six(), "avant"))  # premier rendu : mise en route
    debut = time.monotonic()
    rendu.image(pour_ecran(page_six(), "priere"))
    assert time.monotonic() - debut < 1.0


def test_le_fichier_ne_peut_pas_executer_de_script(rendu):
    """Un gabarit est un document : son script ne doit rien pouvoir faire sur la machine."""
    page = page_six().replace("</body>", "<script>document.title='pwned'</script></body>")
    rendu.image(pour_ecran(page, "avant"))
    assert rendu._cdp is not None
    # le document est resté un document : rien n'a changé en dehors de la page elle-même
    assert rendu.image(pour_ecran(page_six(), "avant"))


def test_sans_navigateur_demarre_le_rendu_refuse():
    r = TemplateRender()
    with pytest.raises(RenderUnavailable):
        r.image("<html></html>")
