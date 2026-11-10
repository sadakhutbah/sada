"""Scene transitions: curves, scene rendering, layer graph and encoder outputs."""
import datetime as dt
from pathlib import Path

from PIL import ImageChops

from minbar import transitions as tr
from minbar.config import Config
from minbar.encoder import Encoder
from minbar.scenes import SCENES, Moment, SceneRenderer


def test_plan_curves():
    p = tr.plan("a", old_full=True, new_full=False)          # full screen -> khutba banner
    v0, vend = p.values(0), p.values(tr.TOTAL)
    assert (p.src, p.dst) == ("a", "b")
    assert v0["a_a"] == 1 and v0["a_b"] == 0 and v0["fond"] == 1 and v0["x_b"] < 0
    assert vend["a_a"] == 0 and vend["a_b"] == 1 and vend["fond"] == 0 and vend["x_b"] == 0 and vend["y_b"] == 0
    mid = p.values(tr.FOND_DUR / 2)
    assert 0.4 < mid["fond"] < 0.6                            # crossfade camera <-> full screen
    steps = [p.values(i / 50)["a_b"] for i in range(int(tr.TOTAL * 50) + 2)]
    assert all(b >= a for a, b in zip(steps, steps[1:]))      # monotonic entry, without jumps
    assert max(b - a for a, b in zip(steps, steps[1:])) < 0.15
    q = tr.plan("b", old_full=True, new_full=True)            # full screen -> full screen: the background stays
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
    assert ImageChops.difference(plain, named).getbbox()       # the entered name appears on screen


def test_encoder_diffuse_vers_plusieurs_destinations(tmp_path):
    """The same stream goes to every destination at once: one ffmpeg, one tee output each."""
    cfg = Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path),
                      rtmp_urls=["rtmps://live-api-s.facebook.com:443/rtmp/CLE-FB",
                                 "rtmps://a.rtmp.youtube.com/live2/CLE-YT"])
    enc = Encoder(cfg, tmp_path, log=lambda m: None)
    cmd = enc.ffmpeg_cmd(*enc.new_recording())
    tee = cmd[cmd.index("tee") + 1]
    assert tee.count("[f=flv:onfail=ignore]") == 2
    assert "facebook.com:443/rtmp/CLE-FB" in tee and "youtube.com/live2/CLE-YT" in tee
    assert tee.split("|")[-1].startswith("[f=matroska]")     # the recording stays in the tee, after the streams


def test_encoder_urls_lues_dans_l_environnement(tmp_path, monkeypatch):
    """MINBAR_RTMP_URLS: comma-separated, one destination per key, single rule of precedence."""
    monkeypatch.setenv("MINBAR_RTMP_URLS", "rtmps://a/1, rtmps://b/2")
    cfg = Config.load(None)
    assert cfg.rtmp_urls == ["rtmps://a/1", "rtmps://b/2"]
    assert Config.load(None, rtmp_urls=["rtmps://c/3"]).rtmp_urls == ["rtmps://c/3"]   # flag wins


def test_encoder_layer_graph(tmp_path):
    cfg = Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path))
    enc = Encoder(cfg, tmp_path, log=lambda m: None)
    r = SceneRenderer(cfg)
    enc.prepare(r.background())
    assert all(Path(p).exists() for p in enc.layer_png.values())
    cmd = " ".join(enc.ffmpeg_cmd(*enc.new_recording()))
    for f in ("zmq=bind_address", "colorchannelmixer@fond", "overlay@ca", "overlay@cb", "volume@mic=volume=0"):
        assert f in cmd
    # off air: the scene is set without animation, and the restarted graph starts from this state
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
