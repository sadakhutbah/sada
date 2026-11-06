"""Transitions de scènes : courbes, calques (fond + contenu == rendu complet), graphe de l'encodeur."""
import datetime as dt

from PIL import Image, ImageChops

from minbar import transitions as tr
from minbar.config import Config
from minbar.encoder import Encoder
from minbar.scenes import SceneRenderer


def test_plan_curves():
    p = tr.plan("a", old_full=True, new_full=False)          # écran plein -> bandeau khutba
    v0, vend = p.values(0), p.values(tr.TOTAL)
    assert (p.src, p.dst) == ("a", "b")
    assert v0["a_a"] == 1 and v0["a_b"] == 0 and v0["fond"] == 1 and v0["x_b"] < 0
    assert vend["a_a"] == 0 and vend["a_b"] == 1 and vend["fond"] == 0 and vend["x_b"] == 0 and vend["y_b"] == 0
    mid = p.values(tr.FOND_DUR / 2)
    assert 0.4 < mid["fond"] < 0.6                            # fondu enchaîné caméra <-> écran plein
    steps = [p.values(i / 50)["a_b"] for i in range(int(tr.TOTAL * 50) + 2)]
    assert all(b >= a for a, b in zip(steps, steps[1:]))      # entrée monotone, sans saut
    assert max(b - a for a, b in zip(steps, steps[1:])) < 0.15
    q = tr.plan("b", old_full=True, new_full=True)            # écran plein -> écran plein : le fond reste
    assert all(q.values(t / 10)["fond"] == 1 for t in range(12))
    assert q.values(tr.IN_START)["y_a"] == tr.SLIDE_Y and q.values(tr.TOTAL)["y_a"] == 0
    assert 0.5 <= tr.TOTAL <= 1.2                             # transition courte et sobre


def test_volume_ramp_expr():
    assert tr.volume_ramp(1, 1) == "1"
    e = tr.volume_ramp(0, 1)
    assert e.startswith("if(isnan(t),0,") and f"/{tr.AUDIO_DUR:g}" in e and e.count("(") == e.count(")")


def test_layers_match_full_render():
    r = SceneRenderer(Config.load(None))
    now, planned = dt.datetime(2026, 11, 6, 12, 20, 5), dt.datetime(2026, 11, 6, 12, 30)
    bg = r.background()
    for sc in ("avant", "priere", "entracte", "termine", "khutba", "janaza"):
        full, content = r.layers(sc, now, planned)
        assert full == (sc in tr.FULL_SCENES)
        comp = bg.copy() if full else Image.new("RGBA", content.size, (0, 0, 0, 0))
        comp.alpha_composite(content)
        diff = ImageChops.difference(r.render(sc, now, planned).convert("RGBA"), comp)
        assert max(hi for _, hi in diff.getextrema()) <= 2, sc


def test_encoder_layer_graph(tmp_path):
    cfg = Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path))
    enc = Encoder(cfg, str(tmp_path / "scene.png"), log=lambda m: None)
    enc.prepare(SceneRenderer(cfg).background())
    assert all((tmp_path / f"scene_{k}.png").exists() for k in ("fond", "a", "b"))
    cmd = " ".join(enc._cmd())
    for f in ("zmq=bind_address", "colorchannelmixer@fond", "overlay@ca", "overlay@cb", "volume@mic=volume=0"):
        assert f in cmd
    # hors direct : la scène est posée sans animation, et le graphe relancé repart de cet état
    full, content = SceneRenderer(cfg).layers("khutba", dt.datetime(2026, 11, 6, 12, 40), None)
    enc.show("khutba", full, content)
    enc.mic(True)
    assert enc.shown == ("khutba", "a") and enc.levels["fond"] == 0 and enc.vol == 1
    assert "colorchannelmixer@fond=aa=0[" in " ".join(enc._cmd()) and "volume@mic=volume=1" in " ".join(enc._cmd())
