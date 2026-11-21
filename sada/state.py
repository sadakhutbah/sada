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
    IDLE = "IDLE"  # waiting for the next window
    ARMED = "ARMED"  # T-15 min: we listen, the stream is not started yet
    BEFORE = "BEFORE"  # T-2 min: stream started, "starting soon" screen
    KHUTBA = "KHUTBA"  # camera + sound
    PRAYER = "PRAYER"  # "Prayer in progress" screen, camera and mic off
    INTERMISSION = "INTERMISSION"  # countdown to the next khutba
    ENDED = "ENDED"  # end screen, before the stream stops
    JANAZA = "JANAZA"  # manual mode: dedicated full screen, without camera or banner
    DARS = "DARS"  # dars or conference: camera + event banner
    DARS_END = "DARS_END"  # end screen of a dars (next class), before the stream stops
    TARAWIH = "TARAWIH"  # Ramadan night prayer: sound + fixed full screen, or camera if the mosque chose it
    STOPPED = "STOPPED"  # stream stopped


# scene displayed by state (the visual, rendered by scenes.py); no scene = no stream
SCENE_OF = {
    State.IDLE: None,
    State.ARMED: None,
    State.STOPPED: None,
    State.BEFORE: "avant",
    State.KHUTBA: "khutba",
    State.PRAYER: "priere",
    State.INTERMISSION: "entracte",
    State.ENDED: "termine",
    State.JANAZA: "janaza",
    State.DARS: "dars",
    State.DARS_END: "dars_fin",
    State.TARAWIH: "tarawih",
}
#: Scene of a tarawih whose mosque chose to film it (see Machine.scene); "tarawih" is the audio one.
TARAWIH_VIDEO_SCENE = "tarawih_video"
# a tarawih is never forced by the scene command: only its planned night, in the mosque's mode
STATE_OF_SCENE = {v: k for k, v in SCENE_OF.items() if v and v != "tarawih"}
#: States in which a session is open and broadcast with the mic on.
SESSION_STATES = (State.KHUTBA, State.DARS, State.TARAWIH)
WAITING = (State.IDLE, State.ARMED, State.BEFORE, State.INTERMISSION)

# Values of the “extend” command, in minutes: the engine default, the local API buttons and the
# back-office choices all read this one tuple.
EXTEND_MINUTES = ("5", "15")

# Scenes that can be picked by hand, in display order. Janaza has its own command, which opens the
# form for the name of the deceased. Protocol values only: the front end translates them.
# Tarawih is never picked by hand: a prayer is only broadcast on its planned night, in the mode chosen.
PICKABLE_SCENES = tuple(sc for sc in STATE_OF_SCENE if sc != "janaza")


@dataclass
class Signals:
    now: dt.datetime
    t: float
    voice_run: float = 0.0
    silence_run: float = 0.0
    presence: bool | None = None  # motion at the minbar; None = detection disabled
    face_run: float | None = None  # seconds without a frontal face at the minbar; None = detection unavailable


@dataclass
class SessionParams:
    """Timing of one session type: when it may start, and when it ends.

    A khutba is bounded by the prayer that follows it, a dars is not: each type carries its own
    numbers instead of a set of `if kind == "khutba"` ternaries.
    """

    window_after_min: float  # no signal beyond that: session skipped
    max_s: float  # no session lasts longer
    silence_stop_s: float  # prolonged silence => end
    grace_s: float  # grace delay before cutting
    ended_hold_s: float  # end screen, before the stream stops


KHUTBA_TIMING = SessionParams(
    window_after_min=45.0, max_s=3600.0, silence_stop_s=180.0, grace_s=45.0, ended_hold_s=45.0
)
DARS_TIMING = SessionParams(window_after_min=90.0, max_s=7200.0, silence_stop_s=180.0, grace_s=90.0, ended_hold_s=45.0)
# Tarawih: no end screen (the stream stops). No fixed end either: the night ends on a silence longer
# than the pauses between sets of rak'as, with nobody at the front (see Machine._tick_tarawih);
# max_s is only a safety cap, replaced by the mosque's own.
TARAWIH_TIMING = SessionParams(
    window_after_min=60.0, max_s=10800.0, silence_stop_s=360.0, grace_s=90.0, ended_hold_s=0.0
)


class SessionPolicy:
    """Rules that depend on the kind of the planned session: entry state, timing, window.

    Chosen once per session from its kind (`POLICIES`), so the machine never branches on
    `kind == "..."`. This base is the dars / conference policy, also used for any unknown kind.
    """

    entry = State.DARS  # state a session of this kind starts in

    def base(self, p: MachineParams) -> SessionParams:
        """Configured timing of the kind (replan reads it for sessions that have no record yet)."""
        return p.dars

    def timing(self, p: MachineParams, rec: SessionRec | None) -> SessionParams:
        return self.base(p)

    def window_after_min(self, p: MachineParams, rec: SessionRec | None) -> float:
        return self.timing(p, rec).window_after_min

    def wait(self, m: Machine, sig: Signals, s: dt.datetime) -> None:
        """One tick in a waiting state (IDLE, ARMED, BEFORE, INTERMISSION) before session `s`."""
        m._WAIT_TICK[m.state](m, sig, s)


class KhutbaPolicy(SessionPolicy):
    entry = State.KHUTBA

    def base(self, p: MachineParams) -> SessionParams:
        return p.khutba


class TarawihPolicy(SessionPolicy):
    entry = State.TARAWIH

    def base(self, p: MachineParams) -> SessionParams:
        return p.tarawih

    def timing(self, p: MachineParams, rec: SessionRec | None) -> SessionParams:
        """A tarawih ends at its mosque's maximum."""
        t = self.base(p)
        if rec is not None and rec.max_min:
            t = replace(t, max_s=float(rec.max_min) * 60)
        return t

    def window_after_min(self, p: MachineParams, rec: SessionRec | None) -> float:
        late = super().window_after_min(p, rec)
        if rec is not None:
            late += rec.offset_min  # counted from the tarawih proper, not from the end of isha
        return late

    def wait(self, m: Machine, sig: Signals, s: dt.datetime) -> None:
        m._wait_tarawih(sig, s)


#: Policy of each session kind; any other kind (dars, conference...) follows `DEFAULT_POLICY`.
DEFAULT_POLICY = SessionPolicy()
POLICIES: dict[str, SessionPolicy] = {"khutba": KhutbaPolicy(), "tarawih": TarawihPolicy()}


@dataclass
class MachineParams:
    # mosque-clock minutes, counted from the session time (T)
    arm_before_min: float = 15.0  # arming: we listen to the voice
    avant_before_min: float = 2.0  # BEFORE scene: stream started
    prayer_min: float = 12.0  # assumed duration of the prayer
    # durations in REAL seconds: the signal is in real time
    voice_start_s: float = 20.0  # sustained voice => start of khutba
    min_session_s: float = 120.0  # no stop before this duration, whatever the session
    # face at the minbar: the imam turns his back during the prayer (0 = signal ignored)
    face_absence_s: float = 45.0  # no frontal face for this long ...
    face_silence_ratio: float = 0.5  # ... AND at least this share of FACE_SILENCE_REF_S
    face_silence_ref_s: float = 60.0  # fixed reference, independent of the (longer) khutba threshold: in a
    # noisy room the face is the only signal, so it must not inherit 3 min
    face_return_s: float = 3.0  # face back within this delay: the grace is cancelled
    # khutba cut by mistake: if the voice comes back during the prayer, the SAME session resumes
    prayer_resume_s: float = 20.0  # sustained voice during the prayer (0 = never resume)
    prayer_resume_window_min: float = 10.0  # ... only this long after the prayer screen appeared
    # per session type: a khutba is bounded by the prayer that follows it, a dars is not
    khutba: SessionParams = field(default_factory=lambda: replace(KHUTBA_TIMING))
    dars: SessionParams = field(default_factory=lambda: replace(DARS_TIMING))
    tarawih: SessionParams = field(default_factory=lambda: replace(TARAWIH_TIMING))
    tarawih_min_s: float = 1200.0  # a tarawih night never ends by itself before this


@dataclass
class SessionRec:
    index: int
    planned: str  # "HH:MM"; "manuel@HH:MM" or "janaza@HH:MM" outside the programme
    status: str = "pending"  # pending | live | done | skipped | janaza
    recording: int | None = None  # number of the recording that contains start_pos / end_pos
    start_pos: float | None = None  # offset (s) in the recording
    end_pos: float | None = None
    start_clock: str | None = None
    end_clock: str | None = None
    end_reason: str | None = None
    kind: str = "khutba"  # khutba | dars | conference | tarawih
    title: str = ""
    speaker: str = ""
    series: str = ""
    session_no: int | None = None
    mode: str = ""  # tarawih: audio | video
    max_min: float | None = None  # tarawih: safety cap chosen by the mosque
    after_isha: bool = False  # tarawih: the time right after isha (announcements) may be broadcast
    offset_min: float = 0.0  # tarawih: from the end of isha to the tarawih proper
    silence_end_min: float | None = None  # tarawih: silence that ends the night
    night: int | None = None  # tarawih: night number in the period
    reciters: list[str] = field(default_factory=list)  # tarawih: the night's reciters, in order
    reciter: str | None = None  # tarawih: current reciter, only when known (never guessed)
    reciter_marks: list[dict] = field(default_factory=list)  # tarawih: {"at": s from start, "name"}

    def event(self) -> dict | None:
        """Event carried by the session (None for an ordinary khutba); same shape as EventInstance.as_event()."""
        if self.kind == "khutba":
            return None
        out = {
            "kind": self.kind,
            "title": self.title,
            "speaker": self.speaker,
            "series": self.series,
            "session_no": self.session_no,
        }
        if self.kind == "tarawih":
            out.update(
                mode=self.mode,
                max_min=self.max_min,
                night=self.night,
                reciters=list(self.reciters),
                reciter=self.reciter,
            )
        return out


def _rows(sessions: list[dt.datetime], extras: list[dict] | None) -> list[tuple[dt.datetime, dict]]:
    """(start, event) pairs of a day, sorted: `extras` follows `sessions` before sorting."""
    if extras is not None and len(extras) != len(sessions):
        raise ValueError(
            _(
                "extras ({extras}) and sessions ({sessions}) must have the same length",
                extras=len(extras),
                sessions=len(sessions),
            )
        )
    return sorted(zip(sessions, extras or [{} for _ in sessions], strict=True), key=lambda row: row[0])


class Machine:
    def __init__(
        self,
        sessions: list[dt.datetime],
        params: MachineParams | None = None,
        log: Callable[[str], None] | None = None,
        pos_fn: Callable[[], tuple[int, float]] | None = None,
        extras: list[dict] | None = None,
    ):
        """pos_fn() -> (recording number, position in s): where the live recording stands.

        `extras` follows `sessions` (same order before sorting): event of each session (shape
        `EventInstance.as_event()`, `{}` for an ordinary khutba). No parallel list: the session
        type is in its event.
        """
        self.p = params or MachineParams()
        rows = _rows(sessions, extras)
        self.sessions = [s for s, _m in rows]
        self.idx = 0
        self.state = State.IDLE
        self.override = False
        self.log = log or (lambda m: None)
        self.pos_fn = pos_fn or (lambda: (0, 0.0))
        # one record per session, same order: records[i] describes sessions[i]
        self.records = [self._rec_of(s, m, i) for i, (s, m) in enumerate(rows)]
        self.live: SessionRec | None = None  # open session (khutba or janaza)
        self.entered_t = 0.0
        self.entered_now: dt.datetime | None = None
        self.grace_until: float | None = None
        self.grace_reason: str | None = None
        self.extend_until_t: float = 0.0
        self.janaza_name: str | None = None  # janaza record: name of the deceased and note (family consent)
        self.janaza_info: str | None = None
        self.paused_from: State | None = None
        self.cut: tuple[SessionRec | None, State] | tuple[None, None] = (None, None)  # session cut by the prayer
        self.last_reason = ""
        self._adhoc: SessionRec | None = None  # off-programme session about to open (see start_adhoc)
        self._adhoc_over = False  # the off-programme session just ended: its end screen hands back to the plan

    # ------------------------------------------------------------------ helpers
    def _rec_of(self, start: dt.datetime, meta: dict, index: int) -> SessionRec:
        """Record of a planned session; `meta` is the event carried ({} for an ordinary khutba)."""
        return SessionRec(
            index,
            start.strftime("%H:%M"),
            kind=meta.get("kind") or "khutba",
            title=meta.get("title", ""),
            speaker=meta.get("speaker", ""),
            series=meta.get("series", ""),
            session_no=meta.get("session_no"),
            mode=meta.get("mode") or "",
            max_min=meta.get("max_min"),
            after_isha=bool(meta.get("after_isha")),
            offset_min=float(meta.get("offset_min") or 0.0),
            silence_end_min=meta.get("silence_end_min"),
            night=meta.get("night"),
            reciters=list(meta.get("reciters") or []),
            # a lone reciter is known; with several, nobody is shown until the control room says who
            reciter=(meta.get("reciters") or [None])[0] if len(meta.get("reciters") or []) == 1 else None,
        )

    @property
    def scene(self) -> str | None:
        """Scene of the state; a tarawih is filmed only when its mosque chose `video` explicitly."""
        if self.state == State.TARAWIH and self.rec is not None and self.rec.mode == "video":
            return TARAWIH_VIDEO_SCENE
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
        if (
            old in (State.KHUTBA, State.DARS)
            and new == State.PRAYER
            and self.paused_from is None
            and reason in ("silence prolongé", "minbar sans visage de face")
        ):
            self.cut = (self.live, old)  # doubtful end: the session can resume (see PRAYER)
        if self.live and new not in (*SESSION_STATES, State.JANAZA) and self.paused_from is None:
            self._close(sig, reason)  # the open session stops as soon as we leave its screen (except pause)
        if new in SESSION_STATES and not (self.live and self.live.status == "live"):
            if self._adhoc is not None:
                r, self._adhoc = self._adhoc, None
            else:
                r = self.rec if self.rec and self.rec.status == "pending" else self._extra("manuel", sig)
            r.status = "live"
            self._open(r, sig)
        if new == State.STOPPED:  # the day is over: nothing left to come
            for r in self.records:
                if r.status == "pending":
                    r.status, r.end_reason = "skipped", reason
            self.idx = len(self.sessions)
        if new == State.JANAZA and not (self.live and self.live.status == "janaza"):
            # the Janaza produces NO derived content (runs.produces_content)
            r = self._extra("janaza", sig)
            r.status = "janaza"
            self._open(r, sig)
        self.log(
            _(
                "STATE {old} → {new}  ({reason})  [clock {now}]",
                old=old.value,
                new=new.value,
                reason=reason,
                now=f"{sig.now:%H:%M:%S}",
            )
        )

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

    @property
    def policy(self) -> SessionPolicy:
        """Rules of the planned session in progress (or of the next one), from its kind."""
        return POLICIES.get(self.kind, DEFAULT_POLICY)

    def _entry_state(self) -> State:
        """Entry state of a session: KHUTBA, TARAWIH, or DARS for a dars / a conference."""
        return self.policy.entry

    def _timing_of(self, kind: str) -> SessionParams:
        return POLICIES.get(kind, DEFAULT_POLICY).base(self.p)

    def timing(self) -> SessionParams:
        """Timing of the session in progress (or of the next one); a tarawih ends at its mosque's maximum."""
        return self.policy.timing(self.p, self.rec)

    def _window_end(self) -> dt.datetime:
        assert self.session is not None
        return self.session + dt.timedelta(minutes=self.policy.window_after_min(self.p, self.rec))

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

    def _after_tarawih(self, sig: Signals) -> State:
        """Where a finished tarawih leads: the next session if any, otherwise the stream stops."""
        nxt = self._next_session(sig)
        return State.STOPPED if nxt == State.ENDED else nxt

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

    # ------------------------------------------------------------------- replan
    def engaged(self) -> bool:
        """A session is open or about to be: the plan must not be swapped under it."""
        return self.live is not None or self.state in (
            State.ARMED,
            State.BEFORE,
            State.INTERMISSION,
            *SESSION_STATES,
            State.PRAYER,
        )

    def new_day(self, sessions: list[dt.datetime], extras: list[dict] | None, sig: Signals) -> bool:
        """The date changed: yesterday's plan gives way to today's. False (retry later) while engaged."""
        if self.engaged() or self.paused_from is not None:
            return False
        rows = _rows(sessions, extras)
        self.sessions = [s for s, _m in rows]
        self.records = [self._rec_of(s, m, i) for i, (s, m) in enumerate(rows)]
        self.idx = 0
        self.live, self.cut = None, (None, None)
        self.janaza_name = self.janaza_info = None
        self.grace_until, self.grace_reason, self.extend_until_t = None, None, 0.0
        self.override = False
        if self.state != State.IDLE:
            self._go(State.IDLE, sig, "nouveau jour")
        self.log(_("New day: {count} session(s) planned", count=len(self.sessions)))
        return True

    def replan(self, sessions: list[dt.datetime], extras: list[dict] | None = None, sig: Signals | None = None) -> int:
        """Replace the sessions still to come (real timetable or programme known only late).

        The day opened on default times because the back office was unreachable: as soon as it
        answers, the sessions that had not started yet give way to the real ones. Sessions already
        played (or skipped) and the one in progress are kept as they are. Returns how many
        sessions were added.
        """
        if sig is None:
            raise ValueError(_("replan needs the current signals"))
        rows = _rows(sessions, extras)
        n = len(self.sessions)  # records of the planned sessions come first, off-programme ones after
        engaged = self.engaged()
        keep = min(self.idx + 1 if engaged else self.idx, n)
        seen = {(s, r.kind) for s, r in zip(self.sessions[:n], self.records[:n], strict=True)}
        fresh: list[tuple[dt.datetime, dict]] = []
        for s, meta in rows:
            kind = meta.get("kind") or "khutba"
            late = self._timing_of(kind).window_after_min
            if (s, kind) in seen or s <= sig.now - dt.timedelta(minutes=late):
                continue  # already planned, or its window is closed: nothing left to play
            seen.add((s, kind))
            fresh.append((s, meta))
        if not fresh:
            return 0
        kept = list(zip(self.sessions[:keep], self.records[:keep], strict=True))
        added = [(s, self._rec_of(s, meta, 0)) for s, meta in fresh]
        out = sorted(kept + added, key=lambda row: row[0])
        self.sessions = [s for s, _r in out]
        self.records = [r for _s, r in out] + self.records[n:]  # off-programme sessions stay last
        new = {id(r) for _s, r in added}
        self.idx = next(i for i, (_s, r) in enumerate(out) if id(r) in new)
        for i, r in enumerate(self.records):
            r.index = i  # records[i] describes sessions[i]
        if self.state in (State.ENDED, State.STOPPED) and not self.override:
            self._go(State.IDLE, sig, "programme reçu : nouvelles sessions")
        self.log(_("Timetable replaced: {count} session(s) added", count=len(fresh)))
        return len(fresh)

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
        sc = self.scene
        return _("stream started, scene {scene} (auto suspended: “auto” to resume)", scene=sc)

    def _stop(self, sig, arg):
        self.override = True
        self._go(self._end_state(), sig, "commande stop")
        return _("end screen, then the stream stops")

    # --- sessions ordered from outside the plan (Sada Studio manual live) -------------------
    @property
    def playing(self) -> SessionRec | None:
        """The session on air (planned, manual or off-programme), else the planned one that comes next."""
        return self.live if self.live is not None and self.live.status == "live" else self.rec

    @property
    def adhoc_on_air(self) -> bool:
        return self.live is not None and self.live.status == "live" and self.live.planned.startswith("studio@")

    def can_start_adhoc(self) -> bool:
        """An off-programme session may start unless a session or a janaza is on air or paused."""
        return self.live is None and self.state not in (*SESSION_STATES, State.JANAZA, State.PRAYER)

    def start_adhoc(self, sig: Signals, kind: str, title: str) -> str:
        """Opens a session that is not in the plan, with its type and title (they name the replay).

        Automatic ends are suspended (`override`): whoever ordered it ends it (`end_adhoc`)."""
        if not self.can_start_adhoc():
            return _("a session is already on air")
        r = SessionRec(len(self.records), f"studio@{sig.now:%H:%M}", kind=kind, title=title)
        self.records.append(r)
        self._adhoc = r
        self.override = True
        self._go(POLICIES.get(kind, DEFAULT_POLICY).entry, sig, "direct Studio manuel")
        return _("studio live started: {kind}", kind=kind)

    def end_adhoc(self, sig: Signals) -> str:
        """Ends the off-programme session on air: end screen, then the stream stops, the plan goes on."""
        if not self.adhoc_on_air:
            return _("no studio live on air")
        self._adhoc_over = True
        self.override = True
        self._go(State.ENDED if self.live.kind == "khutba" else State.DARS_END, sig, "fin du direct Studio")
        return _("end screen, then the stream stops")

    def end_planned(self, sig: Signals, reason: str) -> str:
        """Ends the planned session on air the way its own end does (prayer screen for a khutba, end screen
        for a dars), for an end decided outside: the studio slot is over."""
        r = self.live
        if r is None or r.status != "live" or self.adhoc_on_air or self.state not in (State.KHUTBA, State.DARS):
            return _("no planned session to end")
        if self.state == State.KHUTBA:
            self._go(State.PRAYER, sig, reason)
        else:
            self._go(State.DARS_END, sig, reason)
        return _("session ended: {reason}", reason=reason)

    def _pause(self, sig, arg):
        if self.state == State.PRAYER:
            return _("already paused")
        self.paused_from = self.state  # before _go: the open session stays open during the pause
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
        if self.state == State.JANAZA:  # end of the janaza: the remaining sessions resume automatically
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
        self.idx = len(self.sessions)  # ENDED first if the stream is running: nothing left to come from now on
        self.override = False
        self._go(self._end_state(), sig, "live du jour annulé")
        return _("today's live stream cancelled")

    def _tarawih_now(self, sig, arg):
        """Start tonight's tarawih now (the control room saw it begin). Refused when no tarawih is
        planned today: outside the period or broadcast off, the back office sends none."""
        n = len(self.sessions)
        i = next(
            (i for i, r in enumerate(self.records[:n]) if r.kind == "tarawih" and r.status in ("pending", "live")),
            None,
        )
        if i is None:
            return _("no tarawih tonight: outside the period or broadcast off")
        if self.state == State.TARAWIH:
            return _("the tarawih is already on air")
        for r in self.records[self.idx : i]:
            if r.status == "pending":
                r.status, r.end_reason = "skipped", "commande tarawih"
        if self.live is not None:
            self._close(sig, "commande tarawih")
        self.idx = i
        self.paused_from = None
        self.override = False  # the end stays automatic: silence, presence, safety cap
        self._go(State.TARAWIH, sig, "commande tarawih")
        return _("tarawih started by hand, scene {scene}", scene=self.scene)

    def _next_reciter(self, sig, arg):
        """The next reciter of the night takes over: shown on the slate, kept as a chapter."""
        r = self.live
        if self.state != State.TARAWIH or r is None or r.kind != "tarawih":
            return _("no tarawih on air")
        if not r.reciters:
            return _("no reciter listed for tonight")
        i = (r.reciters.index(r.reciter) + 1) % len(r.reciters) if r.reciter in r.reciters else 0
        r.reciter = r.reciters[i]
        rec, pos = self.pos_fn()
        at = pos - r.start_pos if rec == r.recording and r.start_pos is not None else None
        r.reciter_marks.append({"at": round(max(at, 0.0), 1) if at is not None else None, "name": r.reciter})
        return _("current reciter: {name}", name=r.reciter)

    def _janaza(self, sig, arg):
        """arg = "name" or "name|note": the record entered in the dashboard (optional fields).

        Nothing is named without the family's consent: an empty field stays empty.
        """
        name, _sep, info = (arg or "").partition("|")
        name, info = name.strip()[:80] or None, info.strip()[:60] or None
        if not name:
            info = None  # a note alone makes no sense without a name
        self.janaza_name, self.janaza_info = name, info
        self.override = True
        self._go(State.JANAZA, sig, "commande janaza")
        return _("Janaza mode (full screen; excluded from the AI pipeline)")

    # Commands accepted by the engine, in dashboard display order. This is the single list of
    # commands: the local API and the back-office derive theirs from this dict.
    COMMANDS = {
        "start": _start,
        "pause": _pause,
        "resume": _resume,
        "auto": _auto,
        "extend": _extend,
        "skip": _skip_cmd,
        "scene": _scene,
        "janaza": _janaza,
        "tarawih": _tarawih_now,
        "next_reciter": _next_reciter,
        "stop": _stop,
        "cancel": _cancel,
    }

    # --------------------------------------------------------------------- tick
    def tick(self, sig: Signals) -> None:
        handler = self._TICK.get(self.state)
        if handler is None:
            return  # JANAZA (manual only) and STOPPED: nothing moves by itself
        if self.override and self.state not in self._TICK_IN_OVERRIDE:
            return
        handler(self, sig)

    def _tick_ended(self, sig: Signals) -> None:
        if sig.t - self.entered_t >= self.p.khutba.ended_hold_s:
            self._after_end_screen(sig, "écran Terminé affiché, arrêt du stream")

    def _tick_dars_end(self, sig: Signals) -> None:
        if sig.t - self.entered_t >= self.p.dars.ended_hold_s:
            self._after_end_screen(sig, "écran de fin du dars affiché, arrêt du stream")

    def _after_end_screen(self, sig: Signals, reason: str) -> None:
        """Leaves an end screen: the day is over (STOPPED), except after an off-programme session, which
        hands back to the plan: the sessions still to come stay planned (the stream just stops)."""
        if self._adhoc_over and self.session is not None:
            self._adhoc_over = False
            self.override = False
            self._go(State.IDLE, sig, reason + " (programme conservé)")
            return
        self._adhoc_over = False
        self._go(State.STOPPED, sig, reason)

    def _tick_waiting(self, sig: Signals) -> None:
        """IDLE, ARMED, BEFORE, INTERMISSION: the kind's policy drives the wait for the session."""
        s = self.session
        if s is None:
            self._go(self._end_state(), sig, "plus aucune session")
            return
        self.policy.wait(self, sig, s)

    def _wait_idle(self, sig: Signals, s: dt.datetime) -> None:
        if sig.now >= s - dt.timedelta(minutes=self.p.arm_before_min):
            self._go(State.ARMED, sig, f"fenêtre armée pour {s:%H:%M}")

    def _window_closed(self, sig: Signals) -> bool:
        """Past the window of the session without any signal: skip it and move on."""
        if sig.now <= self._window_end():
            return False
        self._skip("aucun signal dans la fenêtre")
        nxt = self._next_session(sig)
        self._go(nxt, sig, "session suivante" if self.session else "plus aucune session")
        return True

    def _wait_armed(self, sig: Signals, s: dt.datetime) -> None:
        if self._window_closed(sig):
            return
        if self._voice_ok(sig) and sig.now >= s:
            self._go(State.BEFORE, sig, "démarrage anticipé : voix détectée")
            self._go(self._entry_state(), sig, f"voix {sig.voice_run:.0f}s (horaire)")
        elif self._voice_ok(sig):
            self._go(State.BEFORE, sig, "démarrage anticipé : voix détectée")
        elif sig.now >= s - dt.timedelta(minutes=self.p.avant_before_min):
            self._go(State.BEFORE, sig, f"T-{self.p.avant_before_min:g} min : stream lancé")

    def _wait_before(self, sig: Signals, s: dt.datetime) -> None:
        if self._window_closed(sig):
            return
        if self._voice_ok(sig) and sig.now >= s:
            self._go(self._entry_state(), sig, f"voix {sig.voice_run:.0f}s (horaire)")

    def _wait_intermission(self, sig: Signals, s: dt.datetime) -> None:
        if self._window_closed(sig):
            return
        if sig.now >= s and self._voice_ok(sig):
            self._go(self._entry_state(), sig, "voix (2e session)")

    def _tick_khutba(self, sig: Signals) -> None:
        self._tick_session(sig, end=State.PRAYER, face_ends=True)

    def _tick_dars(self, sig: Signals) -> None:
        # a dars is not ended by the face: the imam turns his back in prayer
        self._tick_session(sig, end=State.DARS_END, face_ends=False)

    def _tick_session(self, sig: Signals, end: State, face_ends: bool) -> None:
        """KHUTBA / DARS: end on the maximum duration, or on silence (and, for a khutba, an empty
        minbar) followed by a grace delay. `end` is the screen shown on leaving."""
        timing = self.timing()
        max_s, silence_s, grace_s = timing.max_s, timing.silence_stop_s, timing.grace_s
        elapsed = sig.t - self.entered_t
        if elapsed >= max_s and sig.t >= self.extend_until_t:
            self._go(end, sig, "durée maximale")
            return
        if sig.t < self.extend_until_t:
            self.grace_until = None
            return
        # silence alone concludes nothing (a full room is never silent):
        # we need silence AND a minbar empty of any frontal face
        face_gone = (
            face_ends
            and sig.face_run is not None
            and self.p.face_absence_s > 0
            and sig.face_run >= self.p.face_absence_s
            and sig.silence_run >= self.p.face_silence_ref_s * self.p.face_silence_ratio
        )
        if self.grace_until is None:
            if sig.silence_run >= silence_s and elapsed >= self.p.min_session_s:
                self.grace_until = sig.t + grace_s
                self.grace_reason = "silence prolongé"
                self.log(_("silence {silence:.0f}s: grace delay {grace:.0f}s", silence=sig.silence_run, grace=grace_s))
            elif face_gone and elapsed >= self.p.min_session_s:
                self.grace_until = sig.t + grace_s
                self.grace_reason = "minbar sans visage de face"
                self.log(
                    _(
                        "no frontal face for {absent:.0f}s: grace delay {grace:.0f}s",
                        absent=sig.face_run,
                        grace=grace_s,
                    )
                )
        else:
            face_back = face_ends and sig.face_run is not None and sig.face_run <= self.p.face_return_s
            if sig.voice_run >= 2.0 or face_back:
                self.grace_until = None
                self.log(_("voice or face back during the grace period: carrying on"))
            elif sig.t >= self.grace_until:
                self._go(end, sig, self.grace_reason or "silence prolongé")

    def _tick_prayer(self, sig: Signals) -> None:
        if self.paused_from is not None:
            return
        if self._resume_cut_session(sig):
            return
        if sig.now >= self.entered_now + dt.timedelta(minutes=self.p.prayer_min):
            self.cut = (None, None)
            self._go(self._next_session(sig), sig, "fin de la prière (durée prévue)")

    def _wait_tarawih(self, sig: Signals, s: dt.datetime) -> None:
        """Before a tarawih night. `s` is the estimated end of isha: nothing is armed earlier, so
        isha itself is never on air. Then, if the mosque broadcasts the time after isha
        (announcements, appeals for donations), sustained voice starts at once; otherwise only
        from the tarawih proper (`offset_min` later), on sustained voice."""
        r = self.rec
        assert r is not None
        if self._window_closed(sig):
            return
        if sig.now < s:
            return  # isha in progress: never armed, never broadcast
        if self.state == State.IDLE:
            self._go(State.ARMED, sig, f"isha terminée ({s:%H:%M}) : écoute des tarawih")
        start_at = s + dt.timedelta(minutes=r.offset_min)
        if self._voice_ok(sig) and (r.after_isha or sig.now >= start_at):
            why = "voix après l'isha" if sig.now < start_at else "voix (tarawih)"
            self._go(State.TARAWIH, sig, f"{why} {sig.voice_run:.0f}s")

    def _tick_tarawih(self, sig: Signals) -> None:
        """End of a tarawih night: no fixed end. The pauses between sets of rak'as (a few minutes
        of silence, people seated) must not end it, so the end needs a silence longer than a pause
        (`silence_end_min`), nobody at the front (presence, frontal face) and a minimum length.
        A silence twice as long ends it even if someone is still there. Grace delay as for a
        dars: the voice coming back cancels it. The maximum duration is only a safety cap."""
        timing = self.timing()
        r = self.live or self.rec
        elapsed = sig.t - self.entered_t
        if elapsed >= timing.max_s and sig.t >= self.extend_until_t:
            self._go(self._after_tarawih(sig), sig, "durée maximale (sécurité)")
            return
        if sig.t < self.extend_until_t:
            self.grace_until = None
            return
        need = float(r.silence_end_min) * 60 if r is not None and r.silence_end_min else timing.silence_stop_s
        someone = sig.presence is True or (
            sig.face_run is not None and self.p.face_absence_s > 0 and sig.face_run < self.p.face_absence_s
        )
        very_long = sig.silence_run >= 2 * need
        if self.grace_until is None:
            if elapsed >= self.p.tarawih_min_s and sig.silence_run >= need and (very_long or not someone):
                self.grace_until = sig.t + timing.grace_s
                self.grace_reason = "silence prolongé"
                self.log(
                    _("silence {silence:.0f}s: grace delay {grace:.0f}s", silence=sig.silence_run, grace=timing.grace_s)
                )
        elif sig.voice_run >= 2.0 or (someone and not very_long):
            self.grace_until = None
            self.log(_("voice or face back during the grace period: carrying on"))
        elif sig.t >= self.grace_until:
            self._go(self._after_tarawih(sig), sig, self.grace_reason or "silence prolongé")

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
        self.log(
            _(
                "voice back during the prayer ({voice:.0f}s): session {planned} continues",
                voice=sig.voice_run,
                planned=rec.planned,
            )
        )
        self._go(from_state, sig, "voix reprise : la khutba continue")
        return True

    # State -> handler of one tick, in the style of COMMANDS. States absent from the table (JANAZA,
    # STOPPED) never move by themselves; all but the end screens stand still under a manual override.
    _TICK = {
        State.IDLE: _tick_waiting,
        State.ARMED: _tick_waiting,
        State.BEFORE: _tick_waiting,
        State.INTERMISSION: _tick_waiting,
        State.KHUTBA: _tick_khutba,
        State.DARS: _tick_dars,
        State.TARAWIH: _tick_tarawih,
        State.PRAYER: _tick_prayer,
        State.ENDED: _tick_ended,
        State.DARS_END: _tick_dars_end,
    }
    _TICK_IN_OVERRIDE = (State.ENDED, State.DARS_END)  # the exit from the end screen is always active
    # Waiting states: what each one does before session `s` (see SessionPolicy.wait).
    _WAIT_TICK = {
        State.IDLE: _wait_idle,
        State.ARMED: _wait_armed,
        State.BEFORE: _wait_before,
        State.INTERMISSION: _wait_intermission,
    }

    # ------------------------------------------------------------------- statut
    def status(self) -> dict:
        s = self.session
        return {
            "state": self.state.value,
            "scene": self.scene,
            "override": self.override,
            "paused": self.paused_from is not None,
            "stream_on": self.stream_on,
            "next_session": s.strftime("%H:%M") if s else None,
            "last_reason": self.last_reason,
            "janaza_name": self.janaza_name,
            "janaza_info": self.janaza_info,
            "kind": self.kind,
            "event": self.rec.event() if self.rec else None,
            "sessions": self.sessions_dicts(),
        }

    def sessions_dicts(self) -> list[dict]:
        return [asdict(r) for r in self.records]
