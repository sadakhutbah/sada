"""Machine à états de la diffusion (pure : aucune E/S, entièrement testable).

ARMÉ → AVANT → KHUTBA → PRIÈRE → ENTRACTE → KHUTBA (2) → TERMINÉ  (puis FIN : stream coupé)

Entrées à chaque tick :
  now          heure « mosquée » (horloge simulée)            -> fenêtres horaires
  t            temps monotone réel en secondes                -> durées des signaux
  voice_run    secondes réelles de voix continue              -> VAD
  silence_run  secondes réelles de silence continu            -> VAD
  presence     True/False, ou None si la détection est désactivée

L'automatique propose, l'humain dispose : toute commande manuelle de scène suspend
l'automatique (override) jusqu'à la commande « auto ».

Le stream est diffusé exactement quand l'état a une scène (SCENE_OF). L'état KHUTBA a toujours une session
ouverte (la session prévue si elle attend, sinon une session manuelle) ; l'état JANAZA, une session janaza.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable, Optional


class State(str, Enum):
    IDLE = "IDLE"          # en attente de la prochaine fenêtre
    ARME = "ARMÉ"          # T-15 min : on écoute, le stream n'est pas encore lancé
    AVANT = "AVANT"        # T-2 min : stream lancé, écran « début imminent »
    KHUTBA = "KHUTBA"      # caméra + son
    PRIERE = "PRIÈRE"      # écran « Prière en cours », caméra et micro coupés
    ENTRACTE = "ENTRACTE"  # compte à rebours vers la prochaine khutba
    TERMINE = "TERMINÉ"    # écran de fin, avant arrêt du stream
    JANAZA = "JANAZA"      # mode manuel : caméra minbar sans bandeau
    FIN = "FIN"            # stream arrêté


# scène affichée par état (le visuel, rendu par scenes.py) ; pas de scène = pas de stream
SCENE_OF = {
    State.IDLE: None, State.ARME: None, State.FIN: None,
    State.AVANT: "avant", State.KHUTBA: "khutba", State.PRIERE: "priere",
    State.ENTRACTE: "entracte", State.TERMINE: "termine", State.JANAZA: "janaza",
}
STATE_OF_SCENE = {v: k for k, v in SCENE_OF.items() if v}
WAITING = (State.IDLE, State.ARME, State.AVANT, State.ENTRACTE)


@dataclass
class Signals:
    now: dt.datetime
    t: float
    voice_run: float = 0.0
    silence_run: float = 0.0


@dataclass
class MachineParams:
    arm_before_min: float = 15.0        # armement T-15 min
    avant_before_min: float = 2.0       # scène Avant T-2 min (stream démarré)
    window_after_min: float = 45.0      # au-delà de T+45 min sans signal : session sautée
    prayer_min: float = 12.0            # durée supposée de la prière (min d'horloge)
    # durées en secondes RÉELLES : le signal est en temps réel
    voice_start_s: float = 20.0         # voix soutenue pendant 20 s => début de khutba
    silence_stop_s: float = 60.0        # silence prolongé => fin de khutba
    grace_s: float = 30.0               # délai de grâce avant de couper
    min_khutba_s: float = 120.0         # pas d'arrêt avant cette durée
    max_khutba_s: float = 3600.0
    terminated_hold_s: float = 45.0     # écran Terminé avant arrêt du stream


@dataclass
class SessionRec:
    index: int
    planned: str                        # « HH:MM » ; « manuel@HH:MM » ou « janaza@HH:MM » hors programme
    status: str = "pending"             # pending | live | done | skipped | janaza
    recording: Optional[int] = None     # n° de l'enregistrement qui contient start_pos / end_pos
    start_pos: Optional[float] = None   # offset (s) dans l'enregistrement
    end_pos: Optional[float] = None
    start_clock: Optional[str] = None
    end_clock: Optional[str] = None
    end_reason: Optional[str] = None


class Machine:
    def __init__(self, sessions: list[dt.datetime], params: MachineParams | None = None,
                 log: Callable[[str], None] | None = None,
                 pos_fn: Callable[[], tuple[int, float]] | None = None):
        """pos_fn() -> (n° de l'enregistrement, position en s) : où en est l'enregistrement du live."""
        self.p = params or MachineParams()
        self.sessions = sorted(sessions)
        self.idx = 0
        self.state = State.IDLE
        self.override = False
        self.log = log or (lambda m: None)
        self.pos_fn = pos_fn or (lambda: (0, 0.0))
        self.records = [SessionRec(i, s.strftime("%H:%M")) for i, s in enumerate(self.sessions)]
        self.live: Optional[SessionRec] = None   # session ouverte (khutba ou janaza)
        self.entered_t = 0.0
        self.entered_now: Optional[dt.datetime] = None
        self.grace_until: Optional[float] = None
        self.extend_until_t: float = 0.0
        self.janaza_name: Optional[str] = None
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
        """Horaire de la session prévue en cours ou à venir."""
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
        if new != State.PRIERE:
            self.paused_from = None
        if self.live and new not in (State.KHUTBA, State.JANAZA) and self.paused_from is None:
            self._close(sig, reason)        # la session ouverte s'arrête dès qu'on quitte son écran (sauf pause)
        if new == State.KHUTBA and not (self.live and self.live.status == "live"):
            r = self.rec if self.rec and self.rec.status == "pending" else self._extra("manuel", sig)
            r.status = "live"
            self._open(r, sig)
        if new == State.JANAZA and not (self.live and self.live.status == "janaza"):
            # la Janaza ne produit AUCUN contenu dérivé (runs.is_khutba)
            r = self._extra("janaza", sig)
            r.status = "janaza"
            self._open(r, sig)
        self.log(f"ÉTAT {old.value} → {new.value}  ({reason})  [horloge {sig.now:%H:%M:%S}]")

    def _extra(self, kind: str, sig: Signals) -> SessionRec:
        """Session hors programme (lancée à la main, janaza)."""
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
        # encodeur relancé pendant la session : la fin est dans un autre fichier, on garde le premier jusqu'au bout
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

    def _window_end(self) -> dt.datetime:
        assert self.session is not None
        return self.session + dt.timedelta(minutes=self.p.window_after_min)

    def _next_session(self, sig: Signals) -> State:
        """Passe à la session prévue suivante (en sautant celles dont la fenêtre est close) -> état d'attente."""
        self.idx += 1
        while self.session and sig.now > self._window_end():
            self._skip("fenêtre dépassée")
            self.idx += 1
        if self.session is None:
            return self._end_state()
        return State.ENTRACTE if self.stream_on else State.IDLE

    def _end_state(self) -> State:
        """Fin du live : écran Terminé si le stream tourne, sinon directement FIN."""
        return State.TERMINE if self.stream_on else State.FIN

    def _voice_ok(self, sig: Signals) -> bool:
        return sig.voice_run >= self.p.voice_start_s

    def clock_only(self, now: dt.datetime) -> bool:
        """Vrai si seule l'horloge peut faire avancer la machine (aucun signal attendu) : l'horloge simulée peut accélérer."""
        s, p = self.session, self.p
        if self.override or s is None:
            return False
        if self.state == State.IDLE or (self.state == State.PRIERE and self.paused_from is None):
            return True
        if self.state == State.ARME:
            return now < s - dt.timedelta(minutes=p.avant_before_min)
        if self.state == State.ENTRACTE:
            return now < s - dt.timedelta(minutes=p.arm_before_min)
        return False

    # ----------------------------------------------------------------- commandes
    def command(self, cmd: str, sig: Signals, arg=None) -> str:
        """Commande manuelle. Renvoie un message de confirmation (ou d'erreur)."""
        cmd = cmd.lower()
        self.log(f"COMMANDE manuelle : {cmd} {arg if arg is not None else ''}")
        fn = self.COMMANDS.get(cmd)
        return fn(self, sig, arg) if fn else f"commande inconnue : {cmd}"

    def _start(self, sig, arg):
        self.override = True
        self._go(State.KHUTBA, sig, "commande start")
        return "stream démarré, scène khutba (auto suspendu : « auto » pour reprendre)"

    def _stop(self, sig, arg):
        self.override = True
        self._go(self._end_state(), sig, "commande stop")
        return "écran Terminé puis arrêt du stream"

    def _pause(self, sig, arg):
        if self.state == State.PRIERE:
            return "déjà en pause"
        self.paused_from = self.state       # avant _go : la session ouverte reste ouverte pendant la pause
        self.override = True
        self._go(State.PRIERE, sig, "commande pause")
        return "pause (écran Prière en cours)"

    def _resume(self, sig, arg):
        back = self.paused_from or State.KHUTBA
        self.paused_from = None
        self._go(back, sig, "commande resume")
        return f"reprise sur {back.value}"

    def _auto(self, sig, arg):
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
            mins = float(arg or 5)
        except ValueError:
            return f"durée invalide : {arg} (minutes)"
        base = max(self.extend_until_t, sig.t)
        self.extend_until_t = base + mins * 60  # minutes RÉELLES (khutba jouée en temps réel)
        return f"prolongé de {mins:g} min (pas d'arrêt automatique avant)"

    def _skip_cmd(self, sig, arg):
        if self.session is None:
            return "aucune session à sauter"
        self._skip("commande skip")
        nxt = self._next_session(sig)
        self._go(nxt, sig, "commande skip")
        return f"session sautée, état {nxt.value}"

    def _cancel(self, sig, arg):
        if self.live and self.live.status == "janaza":
            self._close(sig, "live du jour annulé")
        for r in self.records:
            if r.status in ("pending", "live"):
                r.status = "skipped"
                r.end_reason = "annulé"
        self.live = None
        self.idx = len(self.sessions)
        self.override = False
        self._go(self._end_state(), sig, "live du jour annulé")
        return "live du jour annulé"

    def _janaza(self, sig, arg):
        self.janaza_name = (arg or None)
        self.override = True
        self._go(State.JANAZA, sig, "commande janaza")
        return "mode Janaza (pas de bandeau ; exclue du pipeline IA)"

    COMMANDS = {"start": _start, "stop": _stop, "pause": _pause, "resume": _resume, "auto": _auto, "scene": _scene,
                "extend": _extend, "skip": _skip_cmd, "cancel": _cancel, "janaza": _janaza}

    # --------------------------------------------------------------------- tick
    def tick(self, sig: Signals) -> None:
        st = self.state

        # Terminé -> fin : toujours actif (même en override, c'est la sortie)
        if st == State.TERMINE:
            if sig.t - self.entered_t >= self.p.terminated_hold_s:
                self._go(State.FIN, sig, "écran Terminé affiché, arrêt du stream")
            return
        if st == State.JANAZA or self.override:
            return  # Janaza : uniquement manuel

        s = self.session
        if st in WAITING and s is None:
            self._go(self._end_state(), sig, "plus aucune session")
            return

        if st == State.IDLE:
            if sig.now >= s - dt.timedelta(minutes=self.p.arm_before_min):
                self._go(State.ARME, sig, f"fenêtre armée pour {s:%H:%M}")
            return

        if st in (State.ARME, State.AVANT, State.ENTRACTE):
            if sig.now > self._window_end():
                self._skip("aucun signal dans la fenêtre")
                nxt = self._next_session(sig)
                self._go(nxt, sig, "session suivante" if self.session else "plus aucune session")
                return
            if st == State.ENTRACTE:
                armed = sig.now >= s - dt.timedelta(minutes=self.p.arm_before_min)
                if armed and self._voice_ok(sig):
                    self._go(State.KHUTBA, sig, "voix (2e session)")
                return
            if self._voice_ok(sig):
                if st == State.ARME:
                    self._go(State.AVANT, sig, "démarrage anticipé : voix détectée")
                self._go(State.KHUTBA, sig, f"voix {sig.voice_run:.0f}s")
                return
            if st == State.ARME and sig.now >= s - dt.timedelta(minutes=self.p.avant_before_min):
                self._go(State.AVANT, sig, f"T-{self.p.avant_before_min:g} min : stream lancé")
            return

        if st == State.KHUTBA:
            elapsed = sig.t - self.entered_t
            if elapsed >= self.p.max_khutba_s and sig.t >= self.extend_until_t:
                self._go(State.PRIERE, sig, "durée maximale")
                return
            if sig.t < self.extend_until_t:
                self.grace_until = None
                return
            if self.grace_until is None:
                if sig.silence_run >= self.p.silence_stop_s and elapsed >= self.p.min_khutba_s:
                    self.grace_until = sig.t + self.p.grace_s
                    self.log(f"silence {sig.silence_run:.0f}s : délai de grâce {self.p.grace_s:.0f}s")
            else:
                if sig.voice_run >= 2.0:
                    self.grace_until = None
                    self.log("voix reprise pendant la grâce : on continue")
                elif sig.t >= self.grace_until:
                    self._go(State.PRIERE, sig, "silence prolongé")
            return

        if st == State.PRIERE:
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
            "janaza_name": self.janaza_name,
            "sessions": self.sessions_dicts(),
        }

    def sessions_dicts(self) -> list[dict]:
        return [asdict(r) for r in self.records]
