"""Transitions de scènes : courbes, rendu des scènes, graphe et sorties de l'encodeur."""
import datetime as dt
from pathlib import Path

from PIL import ImageChops

from minbar import transitions as tr
from minbar.config import Config
from minbar.encoder import Encoder
from minbar.scenes import SCENES, Moment, SceneRenderer


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


def test_volume_ramp_expr():
    assert tr.volume_ramp(1, 1) == "1"
    e = tr.volume_ramp(0, 1)
    assert e.startswith("if(isnan(t),0,") and f"/{tr.AUDIO_DUR:g}" in e and e.count("(") == e.count(")")


def test_every_scene_renders_and_janaza_shows_the_name():
    r = SceneRenderer(Config.load(None))
    now, planned = dt.datetime(2026, 11, 6, 12, 20, 5), dt.datetime(2026, 11, 6, 12, 30)
    for sc in SCENES:
        img = r.content(sc, Moment(now, planned))
        assert img.size == (1280, 720) and img.mode == "RGBA" and img.getbbox(), sc
    plain = r.content("janaza", Moment(now))
    named = r.content("janaza", Moment(now, janaza_name="Ahmed"))
    assert ImageChops.difference(plain, named).getbbox()       # le nom saisi apparaît à l'écran


def test_encoder_layer_graph(tmp_path):
    cfg = Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path))
    enc = Encoder(cfg, tmp_path, log=lambda m: None)
    r = SceneRenderer(cfg)
    enc.prepare(r.background())
    assert all(Path(p).exists() for p in enc.layer_png.values())
    cmd = " ".join(enc.ffmpeg_cmd(*enc.new_recording()))
    for f in ("zmq=bind_address", "colorchannelmixer@fond", "overlay@ca", "overlay@cb", "volume@mic=volume=0"):
        assert f in cmd
    # hors direct : la scène est posée sans animation, et le graphe relancé repart de cet état
    enc.show("khutba", SCENES["khutba"].full, r.content("khutba", Moment(dt.datetime(2026, 11, 6, 12, 40))))
    enc.mic(True)
    assert enc.shown == ("khutba", "a") and enc.levels["fond"] == 0 and enc.vol == 1
    cmd = " ".join(enc.ffmpeg_cmd(*enc.new_recording()))
    assert "colorchannelmixer@fond=aa=0[" in cmd and "volume@mic=volume=1" in cmd


def test_encoder_clean_output(tmp_path):
    cfg = Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path))
    enc = Encoder(cfg, tmp_path, log=lambda m: None)
    rec, clean = enc.new_recording()
    assert clean == rec[:-4] + "_clean.mkv"
    cmd = " ".join(enc.ffmpeg_cmd(rec, clean))
    assert "asplit=2[a][aclean]" in cmd and "-map 0:v:0 -c:v:0 copy" in cmd
    enc2 = Encoder(Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path), clean_copy=False), tmp_path, log=lambda m: None)
    cmd2 = " ".join(enc2.ffmpeg_cmd(*enc2.new_recording()))
    assert "split=2[cam][camclean]" in cmd2 and "-crf 18" in cmd2
    e3 = Encoder(Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path), record_clean=False), tmp_path, log=lambda m: None)
    rec3, clean3 = e3.new_recording()
    cmd3 = " ".join(e3.ffmpeg_cmd(rec3, clean3))
    assert clean3 is None and "_clean.mkv" not in cmd3 and "asplit" not in cmd3
