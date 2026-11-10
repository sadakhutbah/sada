"""Broadcast state machine (pure: no I/O, fully testable).

ARMED → BEFORE → KHUTBA → PRAYER → INTERMISSION → KHUTBA (2) → ENDED  (then STOPPED: stream stopped)

Inputs at each tick:
  now          "mosque" time (simulated clock)                -> time windows
  t            real monotonic time in seconds                 -> signal durations
  voice_run    real seconds of continuous voice               -> VAD
  silence_run  real seconds of continuous silence             -> VAD
  presence     True/False, or None if detection is disabled

The automatic proposes, the human disposes: any manual scene command suspends
the automatic (override) until the "auto" command.

The stream is broadcast exactly when the state has a scene (SCENE_OF). The KHUTBA state always has an open
session (the planned session if it is waiting, otherwise a manual session); the JANAZA state, a janaza session.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Callable, Optional


class State(str, Enum):
    IDLE = "IDLE"          # waiting for the next window
    ARMED = "ARMED"          # T-15 min: we listen, the stream is not started yet
    BEFORE = "BEFORE"        # T-2 min: stream started, "starting soon" screen
    KHUTBA = "KHUTBA"      # camera + sound
    PRAYER = "PRAYER"      # "Prayer in progress" screen, camera and mic off
    INTERMISSION = "INTERMISSION"  # countdown to the next khutba
    ENDED = "ENDED"    # end screen, before the stream stops
    JANAZA = "JANAZA"      # manual mode: dedicated full screen, without camera or banner
    DARS = "DARS"          # dars or conference: camera + event banner
    DARS_END = "DARS_END"  # end screen of a dars (next class), before the stream stops
    STOPPED = "STOPPED"            # stream stopped


# scene displayed by state (the visual, rendered by scenes.py); no scene = no stream
SCENE_OF = {
    State.IDLE: None, State.ARMED: None, State.STOPPED: None,
    State.BEFORE: "avant", State.KHUTBA: "khutba", State.PRAYER: "priere",
    State.INTERMISSION: "entracte", State.ENDED: "termine", State.JANAZA: "janaza",
    State.DARS: "dars", State.DARS_END: "dars_fin",
}
STATE_OF_SCENE = {v: k for k, v in SCENE_OF.items() if v}
WAITING = (State.IDLE, State.ARMED, State.BEFORE, State.INTERMISSION)

# Values of the “extend” command, in minutes: the engine default, the local API buttons and the
# back-office choices all read this one tuple.
EXTEND_MINUTES = ("5", "15")

# Scenes that can be picked by hand, in display order. Janaza has its own command, which opens the
# form for the name of the deceased. Protocol values only: the front end translates them.
PICKABLE_SCENES = tuple(sc for sc in SCENE_OF.values() if sc and sc != "janaza")



@dataclass
class Signals:
    now: dt.datetime
    t: float
    voice_run: float = 0.0
    silence_run: float = 0.0
    presence: bool | None = None    # motion at the minbar; None = detection disabled
    face_run: float | None = None    # seconds without a frontal face at the minbar; None = detection unavailable


@dataclass
class SessionParams:
    """Timing of one session type: when it may start, and when it ends.

    A khutba is bounded by the prayer that follows it, a dars is not: each type carries its own
    numbers instead of a set of `if kind == "khutba"` ternaries.
    """
    window_after_min: float             # no signal beyond that: session skipped
    max_s: float                        # no session lasts longer
    silence_stop_s: float               # prolonged silence => end
    grace_s: float                      # grace delay before cutting
    ended_hold_s: float                 # end screen, before the stream stops


KHUTBA_TIMING = SessionParams(window_after_min=45.0, max_s=3600.0, silence_stop_s=60.0,
                              grace_s=30.0, ended_hold_s=45.0)
DARS_TIMING = SessionParams(window_after_min=90.0, max_s=7200.0, silence_stop_s=180.0,
                            grace_s=90.0, ended_hold_s=45.0)


@dataclass
class MachineParams:
    # mosque-clock minutes, counted from the session time (T)
    arm_before_min: float = 15.0        # arming: we listen to the voice
    avant_before_min: float = 2.0       # BEFORE scene: stream started
    prayer_min: float = 12.0            # assumed duration of the prayer
    # durations in REAL seconds: the signal is in real time
    voice_start_s: float = 20.0         # sustained voice => start of khutba
    min_session_s: float = 120.0        # no stop before this duration, whatever the session
    # face at the minbar: the imam turns his back during the prayer (0 = signal ignored)
    face_absence_s: float = 45.0        # no frontal face for this long ...
    face_silence_ratio: float = 0.5     # ... AND at least this share of the silence threshold
    face_return_s: float = 3.0          # face back within this delay: the grace is cancelled
    # per session type: a khutba is bounded by the prayer that follows it, a dars is not
    khutba: SessionParams = field(default_factory=lambda: replace(KHUTBA_TIMING))
    dars: SessionParams = field(default_factory=lambda: replace(DARS_TIMING))


@dataclass
class SessionRec:
    index: int
    planned: str                        # "HH:MM"; "manuel@HH:MM" or "janaza@HH:MM" outside the programme
    status: str = "pending"             # pending | live | done | skipped | janaza
    recording: Optional[int] = None     # number of the recording that contains start_pos / end_pos
    start_pos: Optional[float] = None   # offset (s) in the recording
    end_pos: Optional[float] = None
    start_clock: Optional[str] = None
    end_clock: Optional[str] = None
    end_reason: Optional[str] = None
    kind: str = "khutba"                # khutba | dars | conference
    title: str = ""
    speaker: str = ""
    series: str = ""
    session_no: Optional[int] = None

    def event(self) -> Optional[dict]:
        """Event carried by the session (None for an ordinary khutba); same shape as EventInstance.as_event()."""
        if self.kind == "khutba":
            return None
        return {"kind": self.kind, "title": self.title, "speaker": self.speaker,
                "series": self.series, "session_no": self.session_no}


class Machine:
    def __init__(self, sessions: list[dt.datetime], params: MachineParams | None = None,
                 log: Callable[[str], None] | None = None,
                 pos_fn: Callable[[], tuple[int, float]] | None = None,
                 extras: list[dict] | None = None):
        """pos_fn() -> (recording number, position in s): where the live recording stands.

        `extras` follows `sessions` (same order before sorting): event of each session (shape
        `EventInstance.as_event()`, `{}` for an ordinary khutba). No parallel list: the session
        type is in its event.
        """
        self.p = params or MachineParams()
        order = sorted(range(len(sessions)), key=lambda i: sessions[i])
        if extras is not None and len(extras) != len(sessions):
            raise ValueError(f"extras ({len(extras)}) et sessions ({len(sessions)}) doivent avoir la même longueur")
        self.sessions = [sessions[i] for i in order]
        extras = [extras[i] if extras else {} for i in order]
        self.idx = 0
        self.state = State.IDLE
        self.override = False
        self.log = log or (lambda m: None)
        self.pos_fn = pos_fn or (lambda: (0, 0.0))
        self.records = []
        for i, s in enumerate(self.sessions):
            m = extras[i]
            self.records.append(SessionRec(i, s.strftime("%H:%M"), kind=m.get("kind") or "khutba",
                                           title=m.get("title", ""), speaker=m.get("speaker", ""),
                                           series=m.get("series", ""), session_no=m.get("session_no")))
        self.live: Optional[SessionRec] = None   # open session (khutba or janaza)
        self.entered_t = 0.0
        self.entered_now: Optional[dt.datetime] = None
        self.grace_until: Optional[float] = None
        self.grace_reason: Optional[str] = None
        self.extend_until_t: float = 0.0
        self.janaza_name: Optional[str] = None   # janaza record: name of the deceased and note (family consent)
        self.janaza_info: Optional[str] = None
        self.paused_from: Optional[State] = None
        self.last_reason = ""

    # ------------------------------------------------------------------ helpers
    @property
    def scene(self) -> Optional[str]:
        return SCENE_OF[self.state]

    @property
    def stream_on(self) -> bool:
        return self.scene is not None

    @property
    def session(self) -> Optional[dt.datetime]:
        """Time of the planned session in progress or upcoming."""
        return self.sessions[self.idx] if self.idx < len(self.sessions) else None

    @property
    def rec(self) -> Optional[SessionRec]:
        return self.records[self.idx] if self.idx < len(self.sessions) else None

    def _go(self, new: State, sig: Signals, reason: str) -> None:
        old = self.state
        if old == new:
            return
        self.state = new
        self.entered_t = sig.t
        self.entered_now = sig.now
        self.grace_until = None
        self.last_reason = reason
        if new != State.PRAYER:
            self.paused_from = None
        if old == State.JANAZA and self.paused_from is None:
            self.janaza_name = self.janaza_info = None
        if self.live and new not in (State.KHUTBA, State.DARS, State.JANAZA) and self.paused_from is None:
            self._close(sig, reason)        # the open session stops as soon as we leave its screen (except pause)
        if new in (State.KHUTBA, State.DARS) and not (self.live and self.live.status == "live"):
            r = self.rec if self.rec and self.rec.status == "pending" else self._extra("manuel", sig)
            r.status = "live"
            self._open(r, sig)
        if new == State.STOPPED:                # the day is over: nothing left to come
            for r in self.records:
                if r.status == "pending":
                    r.status, r.end_reason = "skipped", reason
            self.idx = len(self.sessions)
        if new == State.JANAZA and not (self.live and self.live.status == "janaza"):
            # the Janaza produces NO derived content (runs.produces_content)
            r = self._extra("janaza", sig)
            r.status = "janaza"
            self._open(r, sig)
        self.log(f"ÉTAT {old.value} → {new.value}  ({reason})  [horloge {sig.now:%H:%M:%S}]")

    def _extra(self, kind: str, sig: Signals) -> SessionRec:
        """Off-programme session, started by hand: it is a khutba (the Janaza is marked by its status)."""
        r = SessionRec(len(self.records), f"{kind}@{sig.now:%H:%M}")
        self.records.append(r)
        return r

    def _open(self, r: SessionRec, sig: Signals) -> None:
        if self.live is not None:
            self._close(sig, f"remplacée par {r.planned}")
        r.recording, r.start_pos = self.pos_fn()
        r.start_clock = f"{sig.now:%H:%M:%S}"
        self.live = r

    def _close(self, sig: Signals, reason: str) -> None:
        r = self.live
        if r is None:
            return
        if r.status == "live":
            r.status = "done"
        rec, pos = self.pos_fn()
        # encoder restarted during the session: the end is in another file, we keep the first one to the end
        r.end_pos = pos if rec == r.recording else None
        r.end_clock = f"{sig.now:%H:%M:%S}"
        r.end_reason = reason
        self.live = None

    def _skip(self, why: str) -> None:
        r = self.rec
        if r and r.status == "pending":
            r.status = "skipped"
            r.end_reason = why
            self.log(f"Session {r.planned} sautée : {why}")

    @property
    def kind(self) -> str:
        """Type of the planned session in progress (khutba, dars, conference)."""
        return self.rec.kind if self.rec else "khutba"

    def _entry_state(self) -> State:
        """Entry state of a session: DARS for a dars / a conference, KHUTBA otherwise."""
        return State.KHUTBA if self.kind == "khutba" else State.DARS

    def timing(self) -> SessionParams:
        """Timing of the session in progress (or of the next one): the khutba's, otherwise the dars's."""
        return self.p.khutba if self.kind == "khutba" else self.p.dars

    def _window_end(self) -> dt.datetime:
        assert self.session is not None
        late = self.timing().window_after_min
        return self.session + dt.timedelta(minutes=late)

    def _next_session(self, sig: Signals) -> State:
        """Move on to the next planned session (skipping those whose window is closed) -> waiting state.

        A session that did not happen (prayer displayed by hand before it) remains the next one."""
        if not (self.rec and self.rec.status == "pending"):
            self.idx += 1
        while self.session and sig.now > self._window_end():
            self._skip("fenêtre dépassée")
            self.idx += 1
        if self.session is None:
            return self._end_state()
        return State.INTERMISSION if self.stream_on else State.IDLE

    def _end_state(self) -> State:
        """End of the live: Terminé screen if the stream is running, STOPPED directly otherwise."""
        return State.ENDED if self.stream_on else State.STOPPED

    def _voice_ok(self, sig: Signals) -> bool:
        return sig.voice_run >= self.p.voice_start_s

    def clock_only(self, now: dt.datetime) -> bool:
        """True if only the clock can move the machine forward (no signal expected): the simulated clock may speed up."""
        s, p = self.session, self.p
        if self.override or s is None:
            return False
        if self.state == State.IDLE or (self.state == State.PRAYER and self.paused_from is None):
            return True
        if self.state == State.ARMED:
            return now < s - dt.timedelta(minutes=p.avant_before_min)
        if self.state == State.INTERMISSION:
            return now < s - dt.timedelta(minutes=p.arm_before_min)
        return False

    # ----------------------------------------------------------------- commandes
    def command(self, cmd: str, sig: Signals, arg=None) -> str:
        """Manual command. Returns a confirmation message (or an error)."""
        cmd = cmd.lower()
        self.log(f"COMMANDE manuelle : {cmd} {arg if arg is not None else ''}")
        fn = self.COMMANDS.get(cmd)
        return fn(self, sig, arg) if fn else f"commande inconnue : {cmd}"

    def _start(self, sig, arg):
        self.override = True
        st = self._entry_state()
        self._go(st, sig, "commande start")
        sc = SCENE_OF[st]
        return f"stream démarré, scène {sc} (auto suspendu : « auto » pour reprendre)"

    def _stop(self, sig, arg):
        self.override = True
        self._go(self._end_state(), sig, "commande stop")
        return "écran Terminé puis arrêt du stream"

    def _pause(self, sig, arg):
        if self.state == State.PRAYER:
            return "déjà en pause"
        self.paused_from = self.state       # before _go: the open session stays open during the pause
        self.override = True
        self._go(State.PRAYER, sig, "commande pause")
        return "pause (écran Prière en cours)"

    def _resume(self, sig, arg):
        if self.paused_from is None:
            return "pas en pause"
        back = self.paused_from
        self._go(back, sig, "commande resume")
        return f"reprise sur {back.value}"

    def _auto(self, sig, arg):
        if self.paused_from is not None:
            self._go(self.paused_from, sig, "commande auto (fin de pause)")
        if self.state == State.JANAZA:           # end of the janaza: the remaining sessions resume automatically
            self._go(State.INTERMISSION if self.session else self._end_state(), sig, "commande auto (fin de janaza)")
        self.override = False
        return "automatique repris"

    def _scene(self, sig, arg):
        sc = str(arg or "").lower()
        if sc not in STATE_OF_SCENE:
            return f"scène inconnue : {sc} (valides : {sorted(STATE_OF_SCENE)})"
        self.override = True
        self._go(STATE_OF_SCENE[sc], sig, f"commande scène {sc}")
        return f"scène {sc} (auto suspendu)"

    def _extend(self, sig, arg):
        try:
            mins = float(arg or EXTEND_MINUTES[0])
        except ValueError:
            return f"durée invalide : {arg} (minutes)"
        base = max(self.extend_until_t, sig.t)
        self.extend_until_t = base + mins * 60  # REAL minutes (khutba played in real time)
        return f"prolongé de {mins:g} min (pas d'arrêt automatique avant)"

    def _skip_cmd(self, sig, arg):
        if self.session is None:
            return "aucune session à sauter"
        self._skip("commande skip")
        nxt = self._next_session(sig)
        self._go(nxt, sig, "commande skip")
        return f"session sautée, état {nxt.value}"

    def _cancel(self, sig, arg):
        for r in self.records:
            if r.status in ("pending", "live"):
                r.status = "skipped"
                r.end_reason = "annulé"
        self.idx = len(self.sessions)       # ENDED first if the stream is running: nothing left to come from now on
        self.override = False
        self._go(self._end_state(), sig, "live du jour annulé")
        return "live du jour annulé"

    def _janaza(self, sig, arg):
        """arg = "name" or "name|note": the record entered in the dashboard (optional fields).

        Nothing is named without the family's consent: an empty field stays empty.
        """
        name, _, info = (arg or "").partition("|")
        name, info = name.strip()[:80] or None, info.strip()[:60] or None
        if not name:
            info = None                     # a note alone makes no sense without a name
        self.janaza_name, self.janaza_info = name, info
        self.override = True
        self._go(State.JANAZA, sig, "commande janaza")
        return "mode Janaza (écran plein ; exclue du pipeline IA)"

    # Commands accepted by the engine, in dashboard display order. This is the single list of
    # commands: the local API and the back-office derive theirs from this dict.
    COMMANDS = {"start": _start, "pause": _pause, "resume": _resume, "auto": _auto, "extend": _extend,
                "skip": _skip_cmd, "scene": _scene, "janaza": _janaza, "stop": _stop, "cancel": _cancel}

    # --------------------------------------------------------------------- tick
    def tick(self, sig: Signals) -> None:
        st = self.state

        # Terminé -> end: always active (even in override, this is the exit)
        if st == State.ENDED:
            if sig.t - self.entered_t >= self.p.khutba.ended_hold_s:
                self._go(State.STOPPED, sig, "écran Terminé affiché, arrêt du stream")
            return
        if st == State.DARS_END:
            if sig.t - self.entered_t >= self.p.dars.ended_hold_s:
                self._go(State.STOPPED, sig, "écran de fin du dars affiché, arrêt du stream")
            return
        if st == State.JANAZA or self.override:
            return  # Janaza: manual only

        s = self.session
        if st in WAITING and s is None:
            self._go(self._end_state(), sig, "plus aucune session")
            return

        if st == State.IDLE:
            if sig.now >= s - dt.timedelta(minutes=self.p.arm_before_min):
                self._go(State.ARMED, sig, f"fenêtre armée pour {s:%H:%M}")
            return

        if st in (State.ARMED, State.BEFORE, State.INTERMISSION):
            if sig.now > self._window_end():
                self._skip("aucun signal dans la fenêtre")
                nxt = self._next_session(sig)
                self._go(nxt, sig, "session suivante" if self.session else "plus aucune session")
                return
            entry = self._entry_state()
            if st == State.INTERMISSION:
                if sig.now >= s and self._voice_ok(sig):
                    self._go(entry, sig, "voix (2e session)")
                return
            if self._voice_ok(sig) and sig.now >= s:
                if st == State.ARMED:
                    self._go(State.BEFORE, sig, "démarrage anticipé : voix détectée")
                self._go(entry, sig, f"voix {sig.voice_run:.0f}s (horaire)")
                return
            if st == State.ARMED and self._voice_ok(sig):
                self._go(State.BEFORE, sig, "démarrage anticipé : voix détectée")
                return
            if st == State.ARMED and sig.now >= s - dt.timedelta(minutes=self.p.avant_before_min):
                self._go(State.BEFORE, sig, f"T-{self.p.avant_before_min:g} min : stream lancé")
            return

        if st in (State.KHUTBA, State.DARS):
            dars = st == State.DARS
            timing = self.timing()
            max_s, silence_s, grace_s = timing.max_s, timing.silence_stop_s, timing.grace_s
            end = State.DARS_END if dars else State.PRAYER
            elapsed = sig.t - self.entered_t
            if elapsed >= max_s and sig.t >= self.extend_until_t:
                self._go(end, sig, "durée maximale")
                return
            if sig.t < self.extend_until_t:
                self.grace_until = None
                return
            # silence alone concludes nothing (a full room is never silent):
            # we need silence AND a minbar empty of any frontal face
            face_gone = (not dars and sig.face_run is not None and self.p.face_absence_s > 0
                         and sig.face_run >= self.p.face_absence_s
                         and sig.silence_run >= silence_s * self.p.face_silence_ratio)
            if self.grace_until is None:
                if sig.silence_run >= silence_s and elapsed >= self.p.min_session_s:
                    self.grace_until = sig.t + grace_s
                    self.grace_reason = "silence prolongé"
                    self.log(f"silence {sig.silence_run:.0f}s : délai de grâce {grace_s:.0f}s")
                elif face_gone and elapsed >= self.p.min_session_s:
                    self.grace_until = sig.t + grace_s
                    self.grace_reason = "minbar sans visage de face"
                    self.log(f"plus de visage de face depuis {sig.face_run:.0f}s : délai de grâce {grace_s:.0f}s")
            else:
                face_back = (sig.face_run is not None and sig.face_run <= self.p.face_return_s)
                if sig.voice_run >= 2.0 or face_back:
                    self.grace_until = None
                    self.log("voix ou visage repris pendant la grâce : on continue")
                elif sig.t >= self.grace_until:
                    self._go(end, sig, getattr(self, "grace_reason", None) or "silence prolongé")
            return

        if st == State.PRAYER:
            if self.paused_from is not None:
                return
            if sig.now >= self.entered_now + dt.timedelta(minutes=self.p.prayer_min):
                self._go(self._next_session(sig), sig, "fin de la prière (durée prévue)")

    # ------------------------------------------------------------------- statut
    def status(self) -> dict:
        s = self.session
        return {
            "state": self.state.value,
            "scene": self.scene,
            "override": self.override,
            "stream_on": self.stream_on,
            "next_session": s.strftime("%H:%M") if s else None,
            "last_reason": self.last_reason,
            "janaza_name": self.janaza_name, "janaza_info": self.janaza_info,
            "kind": self.kind,
            "event": self.rec.event() if self.rec else None,
            "sessions": self.sessions_dicts(),
        }

    def sessions_dicts(self) -> list[dict]:
        return [asdict(r) for r in self.records]
