"""Two orchestrators on one machine: distinct encoder control ports."""

from sada.config import Config
from sada.encoder import Encoder


def _cmd(tmp_path, **kw):
    enc = Encoder(Config(**kw), tmp_path)
    return " ".join(enc.ffmpeg_cmd(str(tmp_path / "r.mp4"), None)), enc


def test_default_ports_unchanged(tmp_path):
    cmd, enc = _cmd(tmp_path)
    assert enc.zmq_aport == 5556 and enc.zmq_vports == (5557, 5558, 5559, 5560)
    assert "5556" in cmd and "5560" in cmd


def test_zmq_port_moves_every_control_port(tmp_path):
    cmd, enc = _cmd(tmp_path, zmq_port=6100)
    assert enc.zmq_vports == (6101, 6102, 6103, 6104)
    assert all(str(p) not in cmd for p in range(5556, 5561))
    assert all(str(p) in cmd for p in range(6100, 6105))
