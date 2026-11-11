"""Presence (motion): pure computation, no camera or ffmpeg."""
import pytest

from sada.presence import MotionGate

W, H = 20, 10


def frame(value=0, block=None, level=255):
    """Grayscale image; `block` = (x0, y0, x1, y1) filled with `level`."""
    px = bytearray([value] * (W * H))
    if block:
        x0, y0, x1, y1 = block
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[y * W + x] = level
    return bytes(px)


def test_premiere_image_ne_dit_rien():
    g = MotionGate(width=W, height=H)
    assert g.score(frame()) is None
    assert g.update(frame(), now=0.0) is False


def test_image_statique_pas_de_presence():
    g = MotionGate(width=W, height=H)
    g.update(frame(), now=0.0)
    assert g.score(frame()) == 0.0
    assert g.update(frame(), now=0.5) is False


def test_mouvement_detecte_puis_retombe():
    g = MotionGate(width=W, height=H, threshold=0.004, hold_s=8.0)
    g.update(frame(), now=0.0)
    assert g.update(frame(block=(2, 2, 8, 6)), now=0.5) is True      # 24 pixels out of 200
    assert g.present(8.0) is True                                     # still within the hold
    assert g.present(9.0) is False                                    # hold expired


def test_bruit_sous_le_seuil_pixel_ignore():
    g = MotionGate(width=W, height=H, pixel_delta=12)
    g.update(frame(value=100), now=0.0)
    assert g.score(frame(value=108)) == 0.0        # +8: sensor noise
    assert g.score(frame(value=140)) == 1.0        # +40: real change


def test_seuil_de_part_de_zone():
    g = MotionGate(width=W, height=H, threshold=0.05)
    g.update(frame(), now=0.0)
    petit = frame(block=(0, 0, 2, 1))              # 2 px / 200 = 1 %
    assert g.update(petit, now=0.5) is False
    gros = frame(block=(0, 0, 10, 2))              # 20 px / 200 = 10 %
    assert g.update(gros, now=1.0) is True


def test_zone_hors_roi_ignoree():
    g = MotionGate(width=W, height=H, roi=(0.5, 0.0, 1.0, 1.0))   # right half
    g.update(frame(), now=0.0)
    gauche = frame(block=(0, 0, 10, 10))           # all on the left: outside the zone
    assert g.score(gauche) == 0.0
    droite = frame(block=(10, 0, 20, 10))          # inside the zone
    assert g.score(droite) == 1.0


def test_bornes_de_zone():
    g = MotionGate(width=W, height=H, roi=(-1.0, -1.0, 5.0, 5.0))
    assert g.bounds() == (0, 0, W, H)
    g = MotionGate(width=W, height=H, roi=(0.9, 0.9, 0.95, 0.95))
    x0, y0, x1, y1 = g.bounds()
    assert x1 > x0 and y1 > y0                     # never an empty zone


def test_taille_d_image_verifiee():
    g = MotionGate(width=W, height=H)
    with pytest.raises(ValueError):
        g.score(b"\x00" * 7)
