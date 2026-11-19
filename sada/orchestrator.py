"""Orchestrator: schedule + signals + state machine + encoder + API.

python -m sada.orchestrator --sim-start 12:12 --sim-speed 40
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from PIL import Image

from . import destinations, gabarit_antenne, mawaqit, notify, runs, screens, source, telemetry, upload
from . import events as events_mod
from .api import make_server
from .clock import SimClock, parse_hhmm
from .config import Config
from .encoder import Encoder
from .faces import FaceGate, FaceMonitor, yunet_detector
from .i18n import _
from .presence import MotionGate, PresenceMonitor
from .scenes import SCENES, Moment, SceneRenderer
from .state import Machine, SessionParams, Signals
from .vad import VadMonitor

log = logging.getLogger("sada")

# Back office unreachable at start (default times, no programme): the times and the programme are
# fetched again, with a bounded backoff, so the day does not stay on the fallback until tomorrow.
RETRY_DELAY_S = 30.0
RETRY_MAX_S = 300.0


def wait_for_backoffice(url: str, limit_s: float, probe=None, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Wait (bounded) until the back office answers its /health, so the day is not opened on fallbacks.

    True when it answers; False after `limit_s` — then the start goes on, and the background retry
    takes over (see Orchestrator._retry_plan).
    """
    if not url or limit_s <= 0:
        return False

    def _probe() -> bool:  # pragma: no cover (network)
        import urllib.request

        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3) as r:  # noqa: S310 — configured back office
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    probe = probe or _probe
    end, delay, said = clock() + limit_s, 1.0, False
    while True:
        if probe():
            if said:
                log.info(_("Back office ready"))
            return True
        if clock() >= end:
            log.warning(_("Back office still unreachable after {limit:g} s: start on the caches", limit=limit_s))
            return False
        if not said:
            log.info(_("Waiting for the back office (at most {limit:g} s)", limit=limit_s))
            said = True
        sleep(min(delay, max(0.0, end - clock())))
        delay = min(delay * 2, 10.0)


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
        wait_for_backoffice(cfg.backoffice_url, 0.0 if cfg.sim_start else cfg.backoffice_wait_s)
        self.times = self._load_times()
        day = self.clock.now().date()
        # today's programme: dars and conferences are added to the khutbas, on the same footing
        # programme of the mosque: kept by the back office, the local file only as fallback
        remote_program = events_mod.from_backoffice(cfg.backoffice_url, cfg.api_port, log=log.info)
        self._program_remote = remote_program is not None
        if remote_program is not None:
            self.program = remote_program
        else:
            self.program, ev_err = events_mod.load(cfg.events_file)
            if ev_err:
                log.warning(ev_err)
        self.instances = events_mod.instances_on(self.program, day, self.prayer_times)
        sessions, extras = self.day_plan(day)
        log.info(_("Sessions of the day (mosque clock): {times}", times=[s.strftime("%H:%M") for s in sessions]))
        for i in self.instances:
            log.info(
                _(
                    "Programme: {label} at {time} — {speaker}",
                    label=i.label,
                    time=i.starts.strftime("%H:%M"),
                    speaker=i.speaker or _("speaker to be confirmed"),
                )
            )
        # Live destinations come from the back office when an administrator saved some there.
        remote = destinations.from_backoffice(cfg.backoffice_url, cfg.api_port, log=log.info)
        if remote:
            cfg.rtmp_urls = remote
        # Video input (camera or a pushed stream) comes from the back office too, same rule.
        entree = source.from_backoffice(cfg.backoffice_url, cfg.api_port, log=log.info)
        if entree:
            cfg.camera_url = entree
        # Texts of the live screens (slates), also edited in the interface.
        textes = screens.from_backoffice(cfg.backoffice_url, cfg.api_port, log=log.info)
        if textes:
            cfg.screen_texts = textes
        # Replays: after the live, the recordings are sent to the bucket saved in the interface.
        self.uploader = upload.Uploader(
            upload.from_backoffice(cfg.backoffice_url, cfg.api_port, log=log.info), log=log.info
        )
        # Le gabarit importé par la mosquée est ce qui s'affiche : le dessin du moteur ne sert plus
        # que de repli quand aucun fichier n'a été importé, ou quand le navigateur de rendu manque.
        self.gabarit = gabarit_antenne.depuis_backoffice(cfg, cfg.backoffice_url, cfg.api_port, log_fn=log.info)
        self.renderer = SceneRenderer(cfg)
        self.encoder = Encoder(cfg, self.run_dir, log=log.info)
        self.encoder.prepare(self.renderer.background())
        self.machine = Machine(sessions, cfg, log=log.info, pos_fn=self.encoder.position, extras=extras)
        self.notifier = notify.from_config(cfg, log=log.info)
        self._notified_state = None
        if self.notifier.enabled:
            log.info(_("Notifications to the imam: {destination}", destination=self.notifier.destination))
        else:
            log.info(_("Notifications to the imam: no destination configured (messages in the log)"))
        self.vad = VadMonitor(cfg.camera_url, cfg.vad_aggressiveness, cfg.vad_ratio, log=log.info)
        self.presence = None
        if cfg.presence == "motion":
            gate = MotionGate(
                width=cfg.presence_width,
                height=cfg.presence_height,
                roi=tuple(cfg.presence_roi),
                threshold=cfg.presence_threshold,
                pixel_delta=cfg.presence_pixel_delta,
                hold_s=cfg.presence_hold_s,
            )
            self.presence = PresenceMonitor(cfg.camera_url, gate, fps=cfg.presence_fps, log=log.info)
            log.info(
                _(
                    "Presence: motion in zone {zone} (threshold {threshold:.3f})",
                    zone=tuple(cfg.presence_roi),
                    threshold=cfg.presence_threshold,
                )
            )
        self.faces = None
        if cfg.face == "yunet":
            gate = FaceGate(
                detect=yunet_detector(cfg.face_threshold, (cfg.face_width, cfg.face_height)),
                roi=tuple(cfg.face_roi),
                threshold=cfg.face_threshold,
                hold_s=cfg.face_hold_s,
            )
            self.faces = FaceMonitor(
                cfg.camera_url, gate, width=cfg.face_width, height=cfg.face_height, fps=cfg.face_fps, log=log.info
            )
            log.info(
                _(
                    "Face: YuNet, zone {zone}, threshold {threshold:.2f}",
                    zone=tuple(cfg.face_roi),
                    threshold=cfg.face_threshold,
                )
            )
        self.lock = threading.RLock()
        self._stream_since: float | None = None  # monotonic time the stream started, None while it is off
        self.telemetry = telemetry.start("sada-orchestrator", cfg.mosque_slug)
        self.telemetry.watch_orchestrator(self.telemetry_snapshot)
        self._shown_key = None
        self._saved = None
        self._stop = threading.Event()
        self.api = None
        # a day opened on the fallback (back office down) is retried in the background: the live
        # must not wait on the network, but the plan must not stay wrong until tomorrow either.
        self._plan_day = day
        self._rolled = False
        self._stale = bool(cfg.backoffice_url) and (self._times_fallback or not self._program_remote)
        self._retry_delay = RETRY_DELAY_S
        self._retry_at = 0.0
        self._retry_thread: threading.Thread | None = None
        self._fetched: tuple[dict | None, list | None] | None = None
        if self._stale:
            log.warning(
                _(
                    "Back office unreachable at start: the times and the programme are fetched again every {delay:g} s",
                    delay=RETRY_DELAY_S,
                )
            )

    # -------------------------------------------------------------- initialisation
    def _setup_logging(self):
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        root = logging.getLogger("sada")
        root.setLevel(logging.INFO)
        for h in (
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(self.run_dir / "orchestrator.log", encoding="utf-8"),
        ):
            h.setFormatter(fmt)
            root.addHandler(h)

    @property
    def prayer_times(self) -> dict:
        """Today's times ("after Isha" etc.); empty if Mawaqit is silent."""
        return mawaqit.prayer_times(self.mawaqit_data or {})

    def _load_times(self) -> list[str]:
        self.mawaqit_data = None
        self._times_fallback = False
        if self.cfg.jumua_times_override:
            log.info(_("Times forced by the config: {times}", times=self.cfg.jumua_times_override))
            return list(self.cfg.jumua_times_override)
        try:
            # the back office keeps each mosque's cache; the local file only if it is unreachable
            d = mawaqit.from_backoffice(self.cfg.backoffice_url, self.cfg.api_port, log=log.info)
            if d is None:
                d = mawaqit.get_jumua(self.cfg.mawaqit_url, self.cfg.mawaqit_cache)
            self.mawaqit_data = d
            log.info(
                _(
                    "Mawaqit ({name}): jumua={jumua} — origin: {origin}",
                    name=d.get("name"),
                    jumua=d["jumua_list"],
                    origin=d.get("origin"),
                )
            )
            if d["jumua_list"]:
                return d["jumua_list"]
        except Exception as e:
            log.error(_("Mawaqit unavailable and no cache: {error}", error=e))
        self._times_fallback = True
        log.warning(_("Fallback: default times 12:30 / 13:30"))
        return ["12:30", "13:30"]

    def day_plan(self, day: dt.date) -> tuple[list[dt.datetime], list[dict]]:
        """(sessions, events) of a day: the jumua times, then the programme, on the same footing."""
        # the khutbas only on Friday; times forced by hand (--times, simulation) apply to any day
        forced = bool(self.cfg.jumua_times_override or self.cfg.sim_start)
        jumua = [parse_hhmm(t, day) for t in self.times] if forced or day.weekday() == 4 else []
        instances = events_mod.instances_on(self.program, day, self.prayer_times)
        return jumua + [i.starts for i in instances], [{} for _ in jumua] + [i.as_event() for i in instances]

    # --- late back office -----------------------------------------------------
    def _roll_day(self, sig: Signals):
        """Midnight: plan the new day at once with what is known, then fetch its times again.

        The process runs for days: without this, the plan of the day it started on would stay.
        """
        day = sig.now.date()
        if day == self._plan_day:
            return
        self.instances = events_mod.instances_on(self.program, day, self.prayer_times)
        sessions, extras = self.day_plan(day)
        if not self.machine.new_day(sessions, extras, sig):
            return  # a session runs over midnight: retried on the next tick
        self._plan_day = day
        log.info(_("Sessions of the day (mosque clock): {times}", times=[s.strftime("%H:%M") for s in sessions]))
        if self.cfg.backoffice_url and not self.cfg.jumua_times_override:
            # today's prayer times (and programme) from the back office: applied by _retry_plan
            self._stale, self._rolled = True, True
            self._retry_delay, self._retry_at = RETRY_DELAY_S, 0.0

    def _retry_plan(self, sig: Signals):
        """Back office down at start: get the times and the programme again, then replan today.

        The fetch runs in a background thread — the live never waits on the network — and its
        result is applied here, under the loop lock. Backoff is bounded (30 s, then 5 min), and
        only the day that was opened on the fallback is retried.
        """
        if not self._stale or sig.now.date() != self._plan_day:
            return
        if self._fetched is not None:
            times, program = self._fetched
            self._fetched = None
            self._apply_plan(times, program, sig)
            return
        if (self._retry_thread and self._retry_thread.is_alive()) or time.monotonic() < self._retry_at:
            return
        self._retry_at = time.monotonic() + self._retry_delay
        self._retry_delay = min(self._retry_delay * 2, RETRY_MAX_S)
        self._retry_thread = threading.Thread(target=self._fetch_plan, name="sada-backoffice", daemon=True)
        self._retry_thread.start()

    def _fetch_plan(self):
        """(times, programme) from the back office, None where it is still unreachable."""
        times = mawaqit.from_backoffice(self.cfg.backoffice_url, self.cfg.api_port, log=log.info)
        program = events_mod.from_backoffice(self.cfg.backoffice_url, self.cfg.api_port, log=log.info)
        if times is None and program is None:
            delay = max(0.0, self._retry_at - time.monotonic())
            log.info(_("Back office still unreachable: next try in {delay:.0f} s", delay=delay))
            return
        self._fetched = (times, program)

    def _apply_plan(self, times: dict | None, program: list | None, sig: Signals):
        """Times / programme received late: today's sessions are replanned where they stand."""
        if times is not None and times.get("jumua_list"):
            self.mawaqit_data, self.times = times, list(times["jumua_list"])
            self._times_fallback = False
            log.info(
                _(
                    "Mawaqit ({name}): jumua={jumua} — origin: {origin}",
                    name=times.get("name"),
                    jumua=times["jumua_list"],
                    origin=times.get("origin"),
                )
            )
        if program is not None:
            self.program, self._program_remote = program, True
        self._stale = self._times_fallback or not self._program_remote
        day = sig.now.date()
        self.instances = events_mod.instances_on(self.program, day, self.prayer_times)
        sessions, extras = self.day_plan(day)
        if self._rolled and not any(rec.status != "pending" for rec in self.machine.records):
            # fresh times right after midnight, nothing played yet: the day is planned again whole
            self._rolled = False
            if self.machine.new_day(sessions, extras, sig):
                log.info(
                    _("Sessions of the day (mosque clock): {times}", times=[s.strftime("%H:%M") for s in sessions])
                )
                return
        added = self.machine.replan(sessions, extras, sig)
        log.info(
            _(
                "Back office reached again: {count} session(s) added to today's plan ({times})",
                count=added,
                times=[s.strftime("%H:%M") for s in self.machine.sessions],
            )
        )

    # --- events ---------------------------------------------------------------
    def _next_event(self, now: dt.datetime):
        """Next class, announced on the end screen and during the interlude."""
        classes = [e for e in self.program if e.kind != "tarawih"]  # a prayer is not a class to announce
        found = events_mod.next_instances(classes, now, count=1, days=21, times_for=lambda d: self.prayer_times)
        return found[0] if found else None

    # --- broadcast -----------------------------------------------------------
    def _sync(self):
        """Aligns the encoder on the machine: displayed scene, mic, stream started or stopped."""
        m, enc = self.machine, self.encoder
        if m.scene is not None:
            now = self.clock.now()
            spec = SCENES[m.scene]
            ev = m.rec.event() if m.scene in ("dars", "tarawih", "tarawih_video") and m.rec is not None else None
            nxt = self._next_event(now) if m.scene in ("dars_fin", "entracte") else None
            key = (
                m.scene,
                m.session,
                m.janaza_name,
                m.janaza_info,
                (ev or {}).get("title"),
                (ev or {}).get("reciter"),
                getattr(nxt, "starts", None),
                now.strftime("%H:%M:%S") if spec.ticking else None,
            )
            if key != self._shown_key:
                self._shown_key = key
                moment = Moment(now, m.session, m.janaza_name, m.janaza_info, event=ev, next_event=nxt)
                if not self._afficher_gabarit(m.scene, spec.full, moment):
                    enc.show(m.scene, spec.full, self.renderer.content(m.scene, moment))
            if spec.mic != enc.mic_on:
                enc.mic(spec.mic)
        if m.state != self._notified_state:
            if self._notified_state is not None:
                self.telemetry.state_changed(self._notified_state.value, m.state.value)
            self._notified_state = m.state
            if notify.should_send(m.state, m, self.clock.now()):
                self.notifier.notify_state(m.state, m)
        if m.stream_on and not enc.wanted:
            enc.start()
            self._stream_since = time.monotonic()
        elif not m.stream_on and enc.wanted:
            enc.stop()
            self._stream_since = None
            log.info(_("Stream stopped. Recordings: {files}", files=[r for r, _x in enc.recordings]))
            self._envoyer_rediffusions()
        self._save_sessions()

    def _afficher_gabarit(self, scene: str, full: bool, moment) -> bool:
        """Le fichier de la mosquée pour cet écran ; ``False`` s'il n'en fournit pas.

        Une image déjà rendue pour cet état n'est pas réécrite : le direct n'a rien à changer.
        Les écrans pleins passent en fond opaque ; le bandeau (khutba, dars) reste transparent hors
        de son panneau et passe en calque de contenu, la caméra restant visible autour.
        """
        if self.gabarit is None or not self.gabarit.actif:
            return False
        from .gabarit_antenne import BANDEAUX, ECRANS

        if scene not in ECRANS and scene not in BANDEAUX:
            return False
        png = self.gabarit.image(scene, moment)
        if png is None:
            return False
        cle = f"{scene}|{self.gabarit.variable_de_l_instant(scene)}"
        try:
            image = Image.open(io.BytesIO(png)).convert("RGBA")
        except Exception as e:  # noqa: BLE001 — image illisible : on dessine
            log.warning(
                _("Template: image of screen {screen} unreadable ({error}) — engine drawing", screen=scene, error=e)
            )
            return False
        if scene in BANDEAUX:
            self.encoder.show(scene, full, image)
        else:
            self.encoder.show_image(scene, full, image, cle)
        return True

    def _envoyer_rediffusions(self):
        """Les fichiers du direct partent en tâche de fond : le moteur n'attend pas le réseau."""
        fichiers = [f for paire in self.encoder.recordings for f in paire if f]
        fil = self.uploader.envoyer(fichiers)
        if fil:
            log.info(_("Upload: {count} file(s) being sent", count=len(fichiers)))

    def _save_sessions(self):
        recs = self.encoder.recordings
        data = {
            "run_id": self.run_id,
            "recordings": [r for r, _ in recs],
            "recordings_clean": [c for _, c in recs],
            "clock_sim": self.cfg.sim_start,
            "sessions": self.machine.sessions_dicts(),
            "mosque": self.cfg.mosque_name,
        }
        if data != self._saved:
            runs.write(self.run_dir, data)
            self._saved = data

    # ------------------------------------------------------------------ API
    def signals(self) -> Signals:
        v, s = self.vad.runs()
        pres = self.presence.present() if self.presence else None
        face_run = self.faces.absence_run() if self.faces else None
        return Signals(
            now=self.clock.now(), t=time.monotonic(), voice_run=v, silence_run=s, presence=pres, face_run=face_run
        )

    def telemetry_snapshot(self) -> telemetry.OrchestratorSnapshot:
        """Numbers only (no title, name or address), read without the lock: a stale value is harmless."""
        since = self._stream_since
        return telemetry.OrchestratorSnapshot(
            state=self.machine.state.value,
            stream_on=self.encoder.wanted,
            stream_live_s=time.monotonic() - since if since is not None else 0.0,
            encoder_alive=self.encoder.alive(),
            encoder_restarts=self.encoder.restarts,
            ingest_connected=self.vad.connected,
        )

    def manual(self, cmd: str, arg=None) -> dict:
        with self.lock:
            msg = self.machine.command(cmd, self.signals(), arg)
            self._sync()
            return {"message": msg, "state": self.machine.state.value}

    def status(self) -> dict:
        with self.lock:
            st = self.machine.status()
            v, s = self.vad.runs()
            _tok, pos = self.encoder.position()
            face_abs = self.faces.absence_run() if self.faces else None
            face_score = self.faces.last_score() if self.faces else None
            st.update(
                {
                    "run_id": self.run_id,
                    "clock": self.clock.now().strftime("%H:%M:%S"),
                    "clock_speed": self.clock.speed,
                    "vad": {"connected": self.vad.connected, "voice_run_s": round(v, 1), "silence_run_s": round(s, 1)},
                    "encoder": {
                        "alive": self.encoder.alive(),
                        "restarts": self.encoder.restarts,
                        "recording": self.encoder.current_recording(),
                        "pos_s": round(pos, 1),
                    },
                    "mic": self.encoder.mic_on,
                    "presence": (
                        {"mode": "motion", "present": self.presence.present(), "score": self.presence.last_score()}
                        if self.presence
                        else {"mode": "off", "present": None, "score": None}
                    ),
                    "face": (
                        {
                            "mode": self.cfg.face,
                            "present": self.faces.present(),
                            "absence_s": round(face_abs, 1) if face_abs is not None else None,
                            "score": round(face_score, 2) if face_score is not None else None,
                        }
                        if self.faces
                        else {"mode": "off", "present": None, "absence_s": None, "score": None}
                    ),
                }
            )
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
        log.info(
            _(
                "Control API: http://{host}:{port}/   | initial state {state}",
                host=cfg.api_host,
                port=cfg.api_port,
                state=self.machine.state.value,
            )
        )
        for sig_ in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig_, lambda *a: self._stop.set())
        last_status = 0.0
        while not self._stop.is_set():
            with self.lock:
                want = self._target_speed()
                if want != self.clock.speed:
                    self.clock.set_speed(want)
                    log.info(_("Simulated clock: speed x{speed:g}", speed=want))
                sig = self.signals()
                self.machine.tick(sig)
                self._roll_day(sig)
                self._retry_plan(sig)
                self._sync()
            if time.monotonic() - last_status > 30:
                last_status = time.monotonic()
                st = self.status()
                log.info(
                    _(
                        "status: state={state} clock={clock} vad={vad} enc={enc}",
                        state=st["state"],
                        clock=st["clock"],
                        vad=st["vad"],
                        enc=st["encoder"],
                    )
                )
            self._stop.wait(0.5)
        log.info(_("Stop requested"))
        self.shutdown()

    def shutdown(self):
        self.vad.stop()
        if self.presence:
            self.presence.stop()
        if self.faces:
            self.faces.stop()
        if self.encoder.wanted:
            self.encoder.stop()
        self._envoyer_rediffusions()
        if self.api:
            self.api.shutdown()
        self._save_sessions()
        self.telemetry.shutdown()


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
    (flag > SADA_<FIELD> environment > --config file > default). Per-session timings (khutba, dars)
    are only read from the JSON file: `{"khutba": {"silence_stop_s": 60}}`.
    """
    from dataclasses import MISSING, fields

    from .config import env_name

    ap = argparse.ArgumentParser(description=_("Sadā — orchestrator"))
    ap.add_argument("--config", help=_("JSON configuration file"))
    for f in fields(Config):
        template = (
            f.default
            if f.default is not MISSING
            else (f.default_factory() if f.default_factory is not MISSING else None)
        )
        if isinstance(template, SessionParams):
            continue
        flags = (f"--{f.name.replace('_', '-')}",) + tuple(CLI_ALIASES.get(f.name, ()))
        ap.add_argument(
            *flags,
            dest=f.name,
            type=cli_type(template),
            default=None,
            metavar=f.name.upper(),
            help=_("default {default} ; env {env}", default=repr(template), env=env_name(f.name)),
        )
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    ov = {k: v for k, v in vars(a).items() if k != "config" and v is not None}
    Orchestrator(Config.load(a.config, **ov)).run()


if __name__ == "__main__":
    main()
