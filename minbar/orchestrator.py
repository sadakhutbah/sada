"""Orchestrateur : horaires + signaux + machine à états + encodeur + API.

    python -m minbar.orchestrator --sim-start 12:12 --sim-speed 40
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import mawaqit
from .api import make_server
from .clock import SimClock, parse_hhmm
from .config import Config
from .encoder import Encoder
from .scenes import SceneRenderer
from .state import Machine, MachineParams, Signals, State
from .vad import VadMonitor

log = logging.getLogger("minbar")


class Orchestrator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        start = None
        if cfg.sim_start:
            start = parse_hhmm(cfg.sim_start, dt.date.today())
        self.clock = SimClock(start, 1.0)
        self.accel = cfg.sim_speed if cfg.sim_start else 1.0
        self.run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(cfg.state_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()
        # horaires
        self.times = self._load_times()
        day = self.clock.now().date()
        sessions = [parse_hhmm(t, day) for t in self.times]
        log.info("Sessions du jour (horloge mosquée) : %s", [s.strftime("%H:%M") for s in sessions])
        p = MachineParams(**{k: getattr(cfg, k) for k in MachineParams.__dataclass_fields__ if hasattr(cfg, k)})
        self.machine = Machine(sessions, p, log=log.info, pos_fn=lambda: self.encoder.pos(),
                               on_change=self._on_change)
        self.renderer = SceneRenderer(cfg)
        self.scene_png = str(self.run_dir / "scene.png")
        self.encoder = Encoder(cfg, self.scene_png, log=log.info)
        self.encoder.prepare(self.renderer.background())   # calque fond commun + calques de contenu vides
        self.vad = VadMonitor(cfg.camera_url, cfg.vad_aggressiveness, cfg.vad_ratio, log=log.info)
        self.lock = threading.RLock()
        self._last_scene_key = None
        self._stop = threading.Event()
        self.api = None
        self.t0 = time.monotonic()

    # -------------------------------------------------------------- initialisation
    def _setup_logging(self):
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        root = logging.getLogger("minbar")
        root.setLevel(logging.INFO)
        for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(self.run_dir / "orchestrator.log", encoding="utf-8")):
            h.setFormatter(fmt)
            root.addHandler(h)

    def _load_times(self) -> list[str]:
        if self.cfg.jumua_times_override:
            log.info("Horaires imposés par la config : %s", self.cfg.jumua_times_override)
            return list(self.cfg.jumua_times_override)
        try:
            d = mawaqit.get_jumua(self.cfg.mawaqit_url, self.cfg.mawaqit_cache)
            log.info("Mawaqit (%s) : jumua=%s jumua2=%s jumua3=%s — origine : %s",
                     d.get("name"), d.get("jumua"), d.get("jumua2"), d.get("jumua3"), d.get("origin"))
            if d["jumua_list"]:
                return d["jumua_list"]
        except Exception as e:
            log.error("Mawaqit indisponible et pas de cache : %s", e)
        log.warning("Repli : horaires par défaut 12:30 / 13:30")
        return ["12:30", "13:30"]

    # --------------------------------------------------------------- événements
    def _on_change(self, old: State, new: State, reason: str):
        # le mic suit la scène (khutba et janaza uniquement)
        if self.machine.stream_on and not self.encoder.wanted:
            self._start_stream()
        self._refresh_scene(force=True)
        self.encoder.mic(new in (State.KHUTBA, State.JANAZA))
        if new == State.FIN and self.encoder.wanted:
            self._finish_stream()
        self._save_sessions()

    def _start_stream(self):
        self._refresh_scene(force=True)
        self.encoder.start()

    def _finish_stream(self):
        self.encoder.stop()
        self._save_sessions()
        log.info("Stream arrêté. Enregistrements : %s", self.encoder.recordings)

    def _refresh_scene(self, force=False):
        m = self.machine
        planned = m.session
        now = self.clock.now()
        sc = m.scene
        key = (sc, now.strftime("%H:%M:%S") if sc in ("entracte",) else None, planned)
        if not force and key == self._last_scene_key:
            return
        self._last_scene_key = key
        # calques séparés : l'encodeur enchaîne les scènes par un fondu (transitions.py), sans couper le flux
        full, content = self.renderer.layers(sc or "avant", now, planned)
        self.encoder.show(sc or "avant", full, content)

    def _save_sessions(self):
        data = {
            "run_id": self.run_id, "recordings": self.encoder.recordings,
            "recordings_clean": self.encoder.recordings_clean, "clock_sim": self.cfg.sim_start,
            "sessions": [r.__dict__ for r in self.machine.records],
            "mosque": self.cfg.mosque_name,
        }
        (self.run_dir / "sessions.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ API
    def signals(self) -> Signals:
        v, s = self.vad.runs()
        return Signals(now=self.clock.now(), t=time.monotonic(), voice_run=v, silence_run=s, presence=None)

    def manual(self, cmd: str, arg=None) -> str:
        with self.lock:
            msg = self.machine.command(cmd, self.signals(), arg)
            self._refresh_scene(force=True)
            return msg

    def status(self) -> dict:
        with self.lock:
            st = self.machine.status()
            v, s = self.vad.runs()
            st.update({
                "clock": self.clock.now().strftime("%H:%M:%S"), "clock_speed": self.clock.speed,
                "vad": {"connected": self.vad.connected, "voice_run_s": round(v, 1), "silence_run_s": round(s, 1)},
                "encoder": {"alive": self.encoder.alive(), "restarts": self.encoder.restarts,
                            "recording": self.encoder.recording, "pos_s": round(self.encoder.pos(), 1)},
                "mic": self.encoder.mic_on,
            })
            return st

    # ---------------------------------------------------------------- boucle
    def _target_speed(self) -> float:
        m, now, p = self.machine, self.clock.now(), self.machine.p
        s = m.session
        if m.override or s is None:
            return 1.0
        if m.state in (State.IDLE, State.PRIERE):
            return self.accel
        if m.state == State.ARME and now < s - dt.timedelta(minutes=p.avant_before_min):
            return self.accel
        if m.state == State.ENTRACTE and now < s - dt.timedelta(minutes=p.arm_before_min):
            return self.accel
        return 1.0

    def run(self):
        cfg = self.cfg
        self.vad.start()
        self.api = make_server(self, cfg.api_host, cfg.api_port, cfg.api_token)
        log.info("API de contrôle : http://%s:%d/   | état initial %s", cfg.api_host, cfg.api_port, self.machine.state.value)
        for sig_ in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig_, lambda *a: self._stop.set())
        last_status = 0.0
        while not self._stop.is_set():
            with self.lock:
                want = self._target_speed()
                if want != self.clock.speed:
                    self.clock.set_speed(want)
                    log.info("Horloge simulée : vitesse x%g", want)
                self.machine.tick(self.signals())
                if self.machine.stream_on:
                    self._refresh_scene()
                    # reprise du flux si l'encodeur n'a pas démarré (ex. override start depuis IDLE)
                    if not self.encoder.wanted:
                        self._start_stream()
            if time.monotonic() - last_status > 30:
                last_status = time.monotonic()
                st = self.status()
                log.info("statut : état=%s horloge=%s vad=%s enc=%s", st["state"], st["clock"], st["vad"], st["encoder"])
            self._stop.wait(0.5)
        log.info("Arrêt demandé")
        self.shutdown()

    def shutdown(self):
        self.vad.stop()
        if self.encoder.wanted:
            self._finish_stream()
        if self.api:
            self.api.shutdown()
        self._save_sessions()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minbar Live — orchestrateur")
    ap.add_argument("--config")
    ap.add_argument("--camera-url")
    ap.add_argument("--rtmp-url", default=os.environ.get("MINBAR_RTMP_URL"))
    ap.add_argument("--sim-start", help="HH:MM : heure simulée de départ")
    ap.add_argument("--sim-speed", type=float)
    ap.add_argument("--times", help="horaires imposés, ex. 12:30,13:30")
    ap.add_argument("--silence-stop", type=float, dest="silence_stop_s")
    ap.add_argument("--grace", type=float, dest="grace_s")
    ap.add_argument("--terminated-hold", type=float, dest="terminated_hold_s")
    ap.add_argument("--api-port", type=int)
    a = ap.parse_args(argv)
    ov = {k: v for k, v in vars(a).items() if k not in ("config", "times") and v is not None}
    if a.times:
        ov["jumua_times_override"] = a.times.split(",")
    cfg = Config.load(a.config, **{k.replace("-", "_"): v for k, v in ov.items()})
    Orchestrator(cfg).run()


if __name__ == "__main__":
    main()
