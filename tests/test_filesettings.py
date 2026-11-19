"""Shared file-backed settings helper."""

import json
import stat

from sada import paths
from sada.pipeline.filesettings import FileSettings


def test_path_env_then_data_dir(tmp_path, monkeypatch):
    fs = FileSettings("SADA_TEST_CFG", "x.json")
    monkeypatch.delenv("SADA_TEST_CFG", raising=False)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    assert fs.path() == tmp_path / "x.json"
    monkeypatch.setenv("SADA_TEST_CFG", str(tmp_path / "other.json"))
    assert fs.path() == tmp_path / "other.json"


def test_read_tolerant(tmp_path):
    fs = FileSettings("SADA_TEST_CFG", "x.json", keys=("a",))
    p = tmp_path / "x.json"
    assert fs.read(p) == {}
    p.write_text("not json")
    assert fs.read(p) == {}
    p.write_text("[1]")
    assert fs.read(p) == {}
    p.write_text('{"a": 1, "b": 2}')
    assert fs.read(p) == {"a": 1}
    assert FileSettings("SADA_TEST_CFG", "x.json").read(p) == {"a": 1, "b": 2}


def test_write_atomic_and_private(tmp_path):
    p = tmp_path / "sub" / "x.json"
    FileSettings("SADA_TEST_CFG", "x.json", private=True).write({"a": 1}, p)
    assert json.loads(p.read_text()) == {"a": 1}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not p.with_suffix(".tmp").exists()
    q = tmp_path / "y.json"
    FileSettings("SADA_TEST_CFG", "y.json").write({"a": 1}, q)


def test_value_precedence(monkeypatch):
    v = FileSettings.value
    monkeypatch.setenv("SADA_TEST_V", "env")
    assert v(cli="cli", override="ov", env="SADA_TEST_V", cfg="file", default="d") == "cli"
    assert v(override="ov", env="SADA_TEST_V", cfg="file", default="d") == "ov"
    assert v(env="SADA_TEST_V", cfg="file", default="d") == "env"
    monkeypatch.delenv("SADA_TEST_V")
    assert v(env="SADA_TEST_V", cfg="file", default="d") == "file"
    assert v(cli="", env="SADA_TEST_V", cfg=None, default="d") == "d"
