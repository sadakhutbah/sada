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
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from enum import Enum

from .i18n import _


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


KHUTBA_TIMING = SessionParams(window_after_min=45.0, max_s=3600.0, silence_stop_s=180.0,
                              grace_s=45.0, ended_hold_s=45.0)
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
    face_silence_ratio: float = 0.5     # ... AND at least this share of FACE_SILENCE_REF_S
    face_silence_ref_s: float = 60.0    # fixed reference, independent of the (longer) khutba threshold: in a
                                        # noisy room the face is the only signal, so it must not inherit 3 min
    face_return_s: float = 3.0          # face back within this delay: the grace is cancelled
    # khutba cut by mistake: if the voice comes back during the prayer, the SAME session resumes
    prayer_resume_s: float = 20.0       # sustained voice during the prayer (0 = never resume)
    prayer_resume_window_min: float = 10.0   # ... only this long after the prayer screen appeared
    # per session type: a khutba is bounded by the prayer that follows it, a dars is not
    khutba: SessionParams = field(default_factory=lambda: replace(KHUTBA_TIMING))
    dars: SessionParams = field(default_factory=lambda: replace(DARS_TIMING))


@dataclass
class SessionRec:
    index: int
    planned: str                        # "HH:MM"; "manuel@HH:MM" or "janaza@HH:MM" outside the programme
    status: str = "pending"             # pending | live | done | skipped | janaza
    recording: int | None = None     # number of the recording that contains start_pos / end_pos
    start_pos: float | None = None   # offset (s) in the recording
    end_pos: float | None = None
    start_clock: str | None = None
    end_clock: str | None = None
    end_reason: str | None = None
    kind: str = "khutba"                # khutba | dars | conference
    title: str = ""
    speaker: str = ""
    series: str = ""
    session_no: int | None = None

    def event(self) -> dict | None:
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
            raise ValueError(_("extras ({extras}) and sessions ({sessions}) must have the same length",
                               extras=len(extras), sessions=len(sessions)))
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
        self.live: SessionRec | None = None   # open session (khutba or janaza)
        self.entered_t = 0.0
        self.entered_now: dt.datetime | None = None
        self.grace_until: float | None = None
        self.grace_reason: str | None = None
        self.extend_until_t: float = 0.0
        self.janaza_name: str | None = None   # janaza record: name of the deceased and note (family consent)
        self.janaza_info: str | None = None
        self.paused_from: State | None = None
        self.cut: tuple[SessionRec | None, State] | tuple[None, None] = (None, None)  # session cut by the prayer
        self.last_reason = ""

    # ------------------------------------------------------------------ helpers
    @property
    def scene(self) -> str | None:
        return SCENE_OF[self.state]

    @property
    def stream_on(self) -> bool:
        return self.scene is not None

    @property
    def session(self) -> dt.datetime | None:
        """Time of the planned session in progress or upcoming."""
        return self.sessions[self.idx] if self.idx < len(self.sessions) else None

    @property
    def rec(self) -> SessionRec | None:
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
        if (old in (State.KHUTBA, State.DARS) and new == State.PRAYER and self.paused_from is None
                and reason in ("silence prolongé", "minbar sans visage de face")):
            self.cut = (self.live, old)      # doubtful end: the session can resume (see PRAYER)
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
        self.log(_("STATE {old} → {new}  ({reason})  [clock {now}]",
                   old=old.value, new=new.value, reason=reason, now=f"{sig.now:%H:%M:%S}"))

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
            self.log(_("Session {planned} skipped: {why}", planned=r.planned, why=why))

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
        self.log(_("Manual command: {cmd} {arg}", cmd=cmd, arg=arg if arg is not None else ""))
        fn = self.COMMANDS.get(cmd)
        return fn(self, sig, arg) if fn else _("unknown command: {cmd}", cmd=cmd)

    def _start(self, sig, arg):
        self.override = True
        st = self._entry_state()
        self._go(st, sig, "commande start")
        sc = SCENE_OF[st]
        return _("stream started, scene {scene} (auto suspended: “auto” to resume)", scene=sc)

    def _stop(self, sig, arg):
        self.override = True
        self._go(self._end_state(), sig, "commande stop")
        return _("end screen, then the stream stops")

    def _pause(self, sig, arg):
        if self.state == State.PRAYER:
            return _("already paused")
        self.paused_from = self.state       # before _go: the open session stays open during the pause
        self.override = True
        self._go(State.PRAYER, sig, "commande pause")
        return _("paused (Prayer screen)")

    def _resume(self, sig, arg):
        if self.paused_from is None:
            return _("not paused")
        back = self.paused_from
        self._go(back, sig, "commande resume")
        return _("resumed on {state}", state=back.value)

    def _auto(self, sig, arg):
        if self.paused_from is not None:
            self._go(self.paused_from, sig, "commande auto (fin de pause)")
        if self.state == State.JANAZA:           # end of the janaza: the remaining sessions resume automatically
            self._go(State.INTERMISSION if self.session else self._end_state(), sig, "commande auto (fin de janaza)")
        self.override = False
        return _("automatic mode resumed")

    def _scene(self, sig, arg):
        sc = str(arg or "").lower()
        if sc not in STATE_OF_SCENE:
            return _("unknown scene: {scene} (valid: {valid})", scene=sc, valid=sorted(STATE_OF_SCENE))
        self.override = True
        self._go(STATE_OF_SCENE[sc], sig, f"commande scène {sc}")
        return _("scene {scene} (auto suspended)", scene=sc)

    def _extend(self, sig, arg):
        try:
            mins = float(arg or EXTEND_MINUTES[0])
        except ValueError:
            return _("invalid duration: {value} (minutes)", value=arg)
        base = max(self.extend_until_t, sig.t)
        self.extend_until_t = base + mins * 60  # REAL minutes (khutba played in real time)
        return _("extended by {minutes:g} min (no automatic stop before)", minutes=mins)

    def _skip_cmd(self, sig, arg):
        if self.session is None:
            return _("no session to skip")
        self._skip("commande skip")
        nxt = self._next_session(sig)
        self._go(nxt, sig, "commande skip")
        return _("session skipped, state {state}", state=nxt.value)

    def _cancel(self, sig, arg):
        for r in self.records:
            if r.status in ("pending", "live"):
                r.status = "skipped"
                r.end_reason = "annulé"
        self.idx = len(self.sessions)       # ENDED first if the stream is running: nothing left to come from now on
        self.override = False
        self._go(self._end_state(), sig, "live du jour annulé")
        return _("today's live stream cancelled")

    def _janaza(self, sig, arg):
        """arg = "name" or "name|note": the record entered in the dashboard (optional fields).

        Nothing is named without the family's consent: an empty field stays empty.
        """
        name, _sep, info = (arg or "").partition("|")
        name, info = name.strip()[:80] or None, info.strip()[:60] or None
        if not name:
            info = None                     # a note alone makes no sense without a name
        self.janaza_name, self.janaza_info = name, info
        self.override = True
        self._go(State.JANAZA, sig, "commande janaza")
        return _("Janaza mode (full screen; excluded from the AI pipeline)")

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
                         and sig.silence_run >= self.p.face_silence_ref_s * self.p.face_silence_ratio)
            if self.grace_until is None:
                if sig.silence_run >= silence_s and elapsed >= self.p.min_session_s:
                    self.grace_until = sig.t + grace_s
                    self.grace_reason = "silence prolongé"
                    self.log(_("silence {silence:.0f}s: grace delay {grace:.0f}s",
                               silence=sig.silence_run, grace=grace_s))
                elif face_gone and elapsed >= self.p.min_session_s:
                    self.grace_until = sig.t + grace_s
                    self.grace_reason = "minbar sans visage de face"
                    self.log(_("no frontal face for {absent:.0f}s: grace delay {grace:.0f}s",
                               absent=sig.face_run, grace=grace_s))
            else:
                face_back = (sig.face_run is not None and sig.face_run <= self.p.face_return_s)
                if sig.voice_run >= 2.0 or face_back:
                    self.grace_until = None
                    self.log(_("voice or face back during the grace period: carrying on"))
                elif sig.t >= self.grace_until:
                    self._go(end, sig, getattr(self, "grace_reason", None) or "silence prolongé")
            return

        if st == State.PRAYER:
            if self.paused_from is not None:
                return
            if self._resume_cut_session(sig):
                return
            if sig.now >= self.entered_now + dt.timedelta(minutes=self.p.prayer_min):
                self.cut = (None, None)
                self._go(self._next_session(sig), sig, "fin de la prière (durée prévue)")

    def _resume_cut_session(self, sig: Signals) -> bool:
        """The prayer was displayed on a mistaken end (short silence): the voice comes back, so the
        SAME khutba resumes instead of leaving a four-minute session behind."""
        rec, from_state = self.cut
        if rec is None or from_state is None or self.p.prayer_resume_s <= 0:
            return False
        if sig.voice_run < self.p.prayer_resume_s:
            return False
        if sig.t - self.entered_t > self.p.prayer_resume_window_min * 60:
            return False
        # reopened before _go, which would otherwise start a fresh session
        rec.status, rec.end_pos, rec.end_clock, rec.end_reason = "live", None, None, None
        self.live, self.cut = rec, (None, None)
        self.log(_("voice back during the prayer ({voice:.0f}s): session {planned} continues",
                   voice=sig.voice_run, planned=rec.planned))
        self._go(from_state, sig, "voix reprise : la khutba continue")
        return True

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
