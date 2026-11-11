"""Traitement automatique d'après-direct : session terminée -> pipeline -> import."""
import datetime as dt
import json
import subprocess

from sada import runs
from sada.backoffice import traitement


def _mosque(tmp_path):
    return {"id": 1, "slug": "m", "state_dir": str(tmp_path / "state"), "output_dir": str(tmp_path / "out")}


def _run(tmp_path, run_id, sessions):
    rd = tmp_path / "state" / run_id
    rd.mkdir(parents=True)
    runs.write(rd, {"run_id": run_id, "recordings": ["live.mkv"], "sessions": sessions})
    return rd


def _now_id(h=1):
    return (dt.datetime.now() - dt.timedelta(hours=h)).strftime("%Y%m%d_%H%M%S")


def _transcrit(tmp_path, run_id, idx):
    d = tmp_path / "out" / run_id / runs.session_dir(idx)
    d.mkdir(parents=True)
    (d / "transcript.json").write_text(json.dumps({"segments": []}))


def test_selection(tmp_path):
    m = _mosque(tmp_path)
    rid = _now_id()
    _run(tmp_path, rid, [{"index": 0, "status": "done", "planned": "13:30"},
                         {"index": 1, "status": "janaza"},
                         {"index": 2, "status": "live"},
                         {"index": 3, "status": "done", "planned": "14:30"}])
    _transcrit(tmp_path, rid, 3)
    old = _now_id(24 * 10)
    _run(tmp_path, old, [{"index": 0, "status": "done"}])
    assert [(rd.name, i) for rd, i in traitement.a_traiter(m)] == [(rid, 0)]


def test_succes_echec_relance(tmp_path, monkeypatch):
    m = _mosque(tmp_path)
    rid = _now_id()
    rd = _run(tmp_path, rid, [{"index": 0, "status": "done", "planned": "13:30"}])
    imports = []
    monkeypatch.setattr(traitement.importer, "import_run", lambda c, mo, r, log=print: imports.append(r))

    def echoue(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "boum\nCUDA out of memory\n")
    monkeypatch.setattr(traitement.subprocess, "run", echoue)
    assert traitement.tour(None, m, log=lambda *_: None) == 1
    e = traitement.lire(rd)["0"]
    assert e["etat"] == "echec" and e["erreur"] == "CUDA out of memory"
    assert traitement.a_traiter(m) == []           # un échec ne repart pas seul

    traitement.relancer(m, rid, 0)
    assert traitement.lire(rd)["0"]["etat"] == "attente"

    def reussit(cmd, **kw):
        _transcrit(tmp_path, rid, 0)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(traitement.subprocess, "run", reussit)
    traitement.tour(None, m, log=lambda *_: None)
    assert traitement.lire(rd)["0"]["etat"] == "termine" and imports == [rid]
    items = traitement.liste(m)
    assert items[0]["etat"] == "termine" and items[0]["run"] == rid


def test_le_pipeline_recoit_le_logo_et_les_couleurs_de_la_mosquee(tmp_path, monkeypatch):
    png = tmp_path / "logo.png"
    from PIL import Image
    Image.new("RGB", (64, 64), (20, 80, 40)).save(png)
    m = {**_mosque(tmp_path), "logo_path": "data/backoffice/logos/m.png",
         "brand_green": "#1E7A3C", "brand_gold": "#C8A96A"}
    rid = _now_id()
    rd = _run(tmp_path, rid, [{"index": 0, "status": "done", "planned": "13:30"}])
    from sada.backoffice import branding
    monkeypatch.setattr(branding, "path_for", lambda mosque: png)
    monkeypatch.setattr(traitement.importer, "import_run", lambda c, mo, r, log=print: None)
    vus = []

    def reussit(cmd, **kw):
        vus.append(cmd)
        _transcrit(tmp_path, rid, 0)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(traitement.subprocess, "run", reussit)
    traitement.tour(None, m, log=lambda *_: None)
    cmd = vus[0]
    assert cmd[cmd.index("--logo") + 1] == str(png)
    assert cmd[cmd.index("--brand-green") + 1] == "#1E7A3C"
    assert cmd[cmd.index("--brand-gold") + 1] == "#C8A96A"
    assert traitement.lire(rd)["0"]["etat"] == "termine"


def test_sans_logo_ni_couleurs_aucune_option_d_habillage(tmp_path, monkeypatch):
    m = _mosque(tmp_path)
    rid = _now_id()
    _run(tmp_path, rid, [{"index": 0, "status": "done", "planned": "13:30"}])
    monkeypatch.setattr(traitement.importer, "import_run", lambda c, mo, r, log=print: None)
    vus = []

    def reussit(cmd, **kw):
        vus.append(cmd)
        _transcrit(tmp_path, rid, 0)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(traitement.subprocess, "run", reussit)
    traitement.tour(None, m, log=lambda *_: None)
    assert not {"--logo", "--brand-green", "--brand-gold"} & set(vus[0])


def test_reprise_apres_coupure(tmp_path):
    m = _mosque(tmp_path)
    rd = _run(tmp_path, _now_id(), [{"index": 0, "status": "done"}])
    (rd / traitement.FICHIER).write_text(json.dumps({"0": {"etat": "en_cours"}}))
    traitement.reprendre(m)
    assert traitement.lire(rd)["0"]["etat"] == "attente"


def test_relance_refusee(tmp_path):
    m = _mosque(tmp_path)
    rid = _now_id()
    _run(tmp_path, rid, [{"index": 0, "status": "janaza"}])
    for args in ((rid, 0), ("x", 0), (rid, 5)):
        try:
            traitement.relancer(m, *args)
        except (ValueError, FileNotFoundError):
            continue
        raise AssertionError(args)
