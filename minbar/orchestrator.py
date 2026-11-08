"""Orchestrateur : horaires + signaux + machine à états + encodeur + API.

    python -m minbar.orchestrator --sim-start 12:12 --sim-speed 40
"""
from __future__ import annotations
import argparse
import datetime as dt
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import mawaqit, runs
from .api import make_server
from .clock import SimClock, parse_hhmm
from .config import Config
from .encoder import Encoder
from .scenes import SCENES, Moment, SceneRenderer
from .state import Machine, Signals
from .vad import VadMonitor

log = logging.getLogger("minbar")


class Orchestrator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        start = parse_hhmm(cfg.sim_start, dt.date.today()) if cfg.sim_start else None
        self.clock = SimClock(start)
        self.accel = cfg.sim_speed if cfg.sim_start else 1.0
        self.run_id = runs.new_run_id()
        self.run_dir = Path(cfg.state_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()
        self.times = self._load_times()
        day = self.clock.now().date()
        sessions = [parse_hhmm(t, day) for t in self.times]
        log.info("Sessions du jour (horloge mosquée) : %s", [s.strftime("%H:%M") for s in sessions])
        self.renderer = SceneRenderer(cfg)
        self.encoder = Encoder(cfg, self.run_dir, log=log.info)
        self.encoder.prepare(self.renderer.background())
        self.machine = Machine(sessions, cfg, log=log.info, pos_fn=self.encoder.position)
        self.vad = VadMonitor(cfg.camera_url, cfg.vad_aggressiveness, cfg.vad_ratio, log=log.info)
        self.lock = threading.RLock()
        self._shown_key = None
        self._saved = None
        self._stop = threading.Event()
        self.api = None

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
            log.info("Mawaqit (%s) : jumua=%s — origine : %s", d.get("name"), d["jumua_list"], d.get("origin"))
            if d["jumua_list"]:
                return d["jumua_list"]
        except Exception as e:
            log.error("Mawaqit indisponible et pas de cache : %s", e)
        log.warning("Repli : horaires par défaut 12:30 / 13:30")
        return ["12:30", "13:30"]

    # --------------------------------------------------------------- diffusion
    def _sync(self):
        """Aligne l'encodeur sur la machine : scène affichée, micro, flux lancé ou arrêté."""
        m, enc = self.machine, self.encoder
        if m.scene is not None:
            now = self.clock.now()
            spec = SCENES[m.scene]
            key = (m.scene, m.session, m.janaza_name, now.strftime("%H:%M:%S") if spec.ticking else None)
            if key != self._shown_key:
                self._shown_key = key
                enc.show(m.scene, spec.full, self.renderer.content(m.scene, Moment(now, m.session, m.janaza_name)))
            if spec.mic != enc.mic_on:
                enc.mic(spec.mic)
        if m.stream_on and not enc.wanted:
            enc.start()
        elif not m.stream_on and enc.wanted:
            enc.stop()
            log.info("Stream arrêté. Enregistrements : %s", [r for r, _ in enc.recordings])
        self._save_sessions()

    def _save_sessions(self):
        recs = self.encoder.recordings
        data = {"run_id": self.run_id, "recordings": [r for r, _ in recs], "recordings_clean": [c for _, c in recs],
                "clock_sim": self.cfg.sim_start, "sessions": self.machine.sessions_dicts(), "mosque": self.cfg.mosque_name}
        if data != self._saved:
            runs.write(self.run_dir, data)
            self._saved = data

    # ------------------------------------------------------------------ API
    def signals(self) -> Signals:
        v, s = self.vad.runs()
        return Signals(now=self.clock.now(), t=time.monotonic(), voice_run=v, silence_run=s)

    def manual(self, cmd: str, arg=None) -> dict:
        with self.lock:
            msg = self.machine.command(cmd, self.signals(), arg)
            self._sync()
            return {"message": msg, "state": self.machine.state.value}

    def status(self) -> dict:
        with self.lock:
            st = self.machine.status()
            v, s = self.vad.runs()
            _, pos = self.encoder.position()
            st.update({
                "run_id": self.run_id,
                "clock": self.clock.now().strftime("%H:%M:%S"), "clock_speed": self.clock.speed,
                "vad": {"connected": self.vad.connected, "voice_run_s": round(v, 1), "silence_run_s": round(s, 1)},
                "encoder": {"alive": self.encoder.alive(), "restarts": self.encoder.restarts,
                            "recording": self.encoder.current_recording(),
                            "pos_s": round(pos, 1)},
                "mic": self.encoder.mic_on,
            })
            return st

    # ---------------------------------------------------------------- boucle
    def _target_speed(self) -> float:
        return self.accel if self.machine.clock_only(self.clock.now()) else 1.0

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
                self._sync()
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
            self.encoder.stop()
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
    Orchestrator(Config.load(a.config, **ov)).run()


if __name__ == "__main__":
    main()
