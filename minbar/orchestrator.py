"""Orchestrator: schedule + signals + state machine + encoder + API.

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

from . import events as events_mod
from . import mawaqit, notify, runs
from .api import make_server
from .clock import SimClock, parse_hhmm
from .config import Config
from .encoder import Encoder
from .scenes import SCENES, Moment, SceneRenderer
from .state import Machine, SessionParams, Signals
from .faces import FaceGate, FaceMonitor, yunet_detector
from .presence import MotionGate, PresenceMonitor
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
        jumua = [parse_hhmm(t, day) for t in self.times]
        # today's programme: dars and conferences are added to the khutbas, on the same footing
        self.program, ev_err = events_mod.load(cfg.events_file)
        if ev_err:
            log.warning(ev_err)
        self.instances = events_mod.instances_on(self.program, day, self.prayer_times)
        sessions = jumua + [i.starts for i in self.instances]
        extras = [{} for _ in jumua] + [i.as_event() for i in self.instances]
        log.info("Sessions du jour (horloge mosquée) : %s", [s.strftime("%H:%M") for s in sessions])
        for i in self.instances:
            log.info("Programme : %s à %s — %s", i.label, i.starts.strftime("%H:%M"), i.speaker or "intervenant à préciser")
        self.renderer = SceneRenderer(cfg)
        self.encoder = Encoder(cfg, self.run_dir, log=log.info)
        self.encoder.prepare(self.renderer.background())
        self.machine = Machine(sessions, cfg, log=log.info, pos_fn=self.encoder.position, extras=extras)
        self.notifier = notify.from_config(cfg, log=log.info)
        self._notified_state = None
        if self.notifier.enabled:
            log.info("Notifications à l'imam : %s", self.notifier.destination)
        else:
            log.info("Notifications à l'imam : aucune destination configurée (messages dans le journal)")
        self.vad = VadMonitor(cfg.camera_url, cfg.vad_aggressiveness, cfg.vad_ratio, log=log.info)
        self.presence = None
        if cfg.presence == "motion":
            gate = MotionGate(width=cfg.presence_width, height=cfg.presence_height,
                              roi=tuple(cfg.presence_roi), threshold=cfg.presence_threshold,
                              pixel_delta=cfg.presence_pixel_delta, hold_s=cfg.presence_hold_s)
            self.presence = PresenceMonitor(cfg.camera_url, gate, fps=cfg.presence_fps, log=log.info)
            log.info("Présence : mouvement dans la zone %s (seuil %.3f)" % (tuple(cfg.presence_roi), cfg.presence_threshold))
        self.faces = None
        if cfg.face == "yunet":
            gate = FaceGate(detect=yunet_detector(cfg.face_threshold, (cfg.face_width, cfg.face_height)),
                            roi=tuple(cfg.face_roi), threshold=cfg.face_threshold, hold_s=cfg.face_hold_s)
            self.faces = FaceMonitor(cfg.camera_url, gate, width=cfg.face_width, height=cfg.face_height,
                                     fps=cfg.face_fps, log=log.info)
            log.info("Visage : YuNet, zone %s, seuil %.2f" % (tuple(cfg.face_roi), cfg.face_threshold))
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

    @property
    def prayer_times(self) -> dict:
        """Today's times ("after Isha" etc.); empty if Mawaqit is silent."""
        return mawaqit.prayer_times(self.mawaqit_data or {})

    def _load_times(self) -> list[str]:
        self.mawaqit_data = None
        if self.cfg.jumua_times_override:
            log.info("Horaires imposés par la config : %s", self.cfg.jumua_times_override)
            return list(self.cfg.jumua_times_override)
        try:
            d = mawaqit.get_jumua(self.cfg.mawaqit_url, self.cfg.mawaqit_cache)
            self.mawaqit_data = d
            log.info("Mawaqit (%s) : jumua=%s — origine : %s", d.get("name"), d["jumua_list"], d.get("origin"))
            if d["jumua_list"]:
                return d["jumua_list"]
        except Exception as e:
            log.error("Mawaqit indisponible et pas de cache : %s", e)
        log.warning("Repli : horaires par défaut 12:30 / 13:30")
        return ["12:30", "13:30"]

    # --- events ---------------------------------------------------------------
    def _next_event(self, now: dt.datetime):
        """Next class, announced on the end screen and during the interlude."""
        found = events_mod.next_instances(self.program, now, count=1, days=21,
                                          times_for=lambda d: self.prayer_times)
        return found[0] if found else None

    # --- broadcast -----------------------------------------------------------
    def _sync(self):
        """Aligns the encoder on the machine: displayed scene, mic, stream started or stopped."""
        m, enc = self.machine, self.encoder
        if m.scene is not None:
            now = self.clock.now()
            spec = SCENES[m.scene]
            ev = m.rec.event() if m.scene == "dars" and m.rec is not None else None
            nxt = self._next_event(now) if m.scene in ("dars_fin", "entracte") else None
            key = (m.scene, m.session, m.janaza_name, m.janaza_info, (ev or {}).get("title"),
                   getattr(nxt, "starts", None), now.strftime("%H:%M:%S") if spec.ticking else None)
            if key != self._shown_key:
                self._shown_key = key
                moment = Moment(now, m.session, m.janaza_name, m.janaza_info, event=ev, next_event=nxt)
                enc.show(m.scene, spec.full, self.renderer.content(m.scene, moment))
            if spec.mic != enc.mic_on:
                enc.mic(spec.mic)
        if m.state != self._notified_state:
            self._notified_state = m.state
            if notify.should_send(m.state, m, self.clock.now()):
                self.notifier.notify_state(m.state, m)
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
        pres = self.presence.present() if self.presence else None
        face_run = self.faces.absence_run() if self.faces else None
        return Signals(now=self.clock.now(), t=time.monotonic(), voice_run=v, silence_run=s,
                       presence=pres, face_run=face_run)

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
            face_abs = self.faces.absence_run() if self.faces else None
            face_score = self.faces.last_score() if self.faces else None
            st.update({
                "run_id": self.run_id,
                "clock": self.clock.now().strftime("%H:%M:%S"), "clock_speed": self.clock.speed,
                "vad": {"connected": self.vad.connected, "voice_run_s": round(v, 1), "silence_run_s": round(s, 1)},
                "encoder": {"alive": self.encoder.alive(), "restarts": self.encoder.restarts,
                            "recording": self.encoder.current_recording(),
                            "pos_s": round(pos, 1)},
                "mic": self.encoder.mic_on,
                "presence": (
                    {"mode": "motion", "present": self.presence.present(),
                     "score": self.presence.last_score()}
                    if self.presence else {"mode": "off", "present": None, "score": None}
                ),
                "face": (
                    {"mode": self.cfg.face, "present": self.faces.present(),
                     "absence_s": round(face_abs, 1) if face_abs is not None else None,
                     "score": round(face_score, 2) if face_score is not None else None}
                    if self.faces else {"mode": "off", "present": None, "absence_s": None, "score": None}
                ),
            })
            return st

    # --- loop --------------------------------------------------------------
    def _target_speed(self) -> float:
        return self.accel if self.machine.clock_only(self.clock.now()) else 1.0

    def run(self):
        cfg = self.cfg
        self.vad.start()
        if self.presence:
            self.presence.start()
        if self.faces:
            self.faces.start()
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
        if self.presence:
            self.presence.stop()
        if self.faces:
            self.faces.stop()
        if self.encoder.wanted:
            self.encoder.stop()
        if self.api:
            self.api.shutdown()
        self._save_sessions()


# Command-line aliases: the flag keeps the field name, unless a shorter name is in use.
CLI_ALIASES = {
    "jumua_times_override": ("--times",),
    "mosque_name": ("--mosque-name",),
    "mosque_name_ar": ("--mosque-name-ar",),
}


def cli_type(template):
    """Argument type, deduced from the default value of the setting."""
    if isinstance(template, bool):
        return lambda v: str(v).strip().lower() in ("1", "true", "yes", "on", "oui")
    if isinstance(template, int):
        return int
    if isinstance(template, float):
        return float
    return str


def parser() -> argparse.ArgumentParser:
    """Command line built from Config: one flag per setting, same name as the JSON key.

    Nothing is defaulted here: an absent flag stays None and Config.load applies the one precedence
    (flag > MINBAR_<FIELD> environment > --config file > default). Per-session timings (khutba, dars)
    are only read from the JSON file: `{"khutba": {"silence_stop_s": 60}}`.
    """
    from dataclasses import MISSING, fields

    from .config import env_name

    ap = argparse.ArgumentParser(description="Minbar Live — orchestrateur")
    ap.add_argument("--config", help="fichier JSON de configuration")
    for f in fields(Config):
        template = f.default if f.default is not MISSING else (
            f.default_factory() if f.default_factory is not MISSING else None)
        if isinstance(template, SessionParams):
            continue
        flags = (f"--{f.name.replace('_', '-')}",) + tuple(CLI_ALIASES.get(f.name, ()))
        ap.add_argument(*flags, dest=f.name, type=cli_type(template), default=None,
                        metavar=f.name.upper(),
                        help=f"défaut {template!r} ; env {env_name(f.name)}")
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    ov = {k: v for k, v in vars(a).items() if k != "config" and v is not None}
    Orchestrator(Config.load(a.config, **ov)).run()


if __name__ == "__main__":
    main()
