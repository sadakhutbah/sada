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
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
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


# scène affichée par état (le visuel, rendu par scenes.py)
SCENE_OF = {
    State.IDLE: None, State.ARME: None, State.FIN: None,
    State.AVANT: "avant", State.KHUTBA: "khutba", State.PRIERE: "priere",
    State.ENTRACTE: "entracte", State.TERMINE: "termine", State.JANAZA: "janaza",
}
MANUAL_SCENES = {"avant", "khutba", "priere", "entracte", "termine", "janaza"}
STATE_OF_SCENE = {v: k for k, v in SCENE_OF.items() if v}


@dataclass
class Signals:
    now: dt.datetime
    t: float
    voice_run: float = 0.0
    silence_run: float = 0.0
    presence: Optional[bool] = None


@dataclass
class MachineParams:
    arm_before_min: float = 15.0
    avant_before_min: float = 2.0
    window_after_min: float = 45.0
    prayer_min: float = 12.0
    voice_start_s: float = 20.0
    silence_stop_s: float = 60.0
    grace_s: float = 30.0
    min_khutba_s: float = 120.0
    max_khutba_s: float = 3600.0
    terminated_hold_s: float = 45.0


@dataclass
class SessionRec:
    index: int
    planned: str
    status: str = "pending"        # pending | live | done | skipped | janaza
    start_pos: Optional[float] = None   # offset (s) dans l'enregistrement
    end_pos: Optional[float] = None
    start_clock: Optional[str] = None
    end_clock: Optional[str] = None
    end_reason: Optional[str] = None
    exclude_from_pipeline: bool = False


class Machine:
    def __init__(self, sessions: list[dt.datetime], params: MachineParams | None = None,
                 log: Callable[[str], None] | None = None,
                 pos_fn: Callable[[], float] | None = None,
                 on_change: Callable[[State, State, str], None] | None = None):
        self.p = params or MachineParams()
        self.sessions = sorted(sessions)
        self.idx = 0
        self.state = State.IDLE
        self.override = False
        self.stream_on = False
        self.log = log or (lambda m: None)
        self.pos_fn = pos_fn or (lambda: 0.0)
        self.on_change = on_change
        self.records = [SessionRec(i, s.strftime("%H:%M")) for i, s in enumerate(self.sessions)]
        self.entered_t = 0.0
        self.entered_now: Optional[dt.datetime] = None
        self.grace_until: Optional[float] = None
        self.extend_until_t: float = 0.0
        self.janaza_name: Optional[str] = None
        self.paused_from: Optional[State] = None
        self.last_reason = ""
        self.terminated_since: float = 0.0

    # ------------------------------------------------------------------ helpers
    @property
    def scene(self) -> Optional[str]:
        return SCENE_OF[self.state]

    @property
    def session(self) -> Optional[dt.datetime]:
        return self.sessions[self.idx] if self.idx < len(self.sessions) else None

    @property
    def rec(self) -> Optional[SessionRec]:
        return self.records[self.idx] if self.idx < len(self.records) else None

    def next_planned(self) -> Optional[dt.datetime]:
        return self.session

    def _go(self, new: State, sig: Signals, reason: str) -> None:
        old = self.state
        if old == new:
            return
        self.state = new
        self.entered_t = sig.t
        self.entered_now = sig.now
        self.grace_until = None
        self.last_reason = reason
        if new in (State.AVANT, State.KHUTBA, State.PRIERE, State.ENTRACTE, State.TERMINE, State.JANAZA):
            self.stream_on = True
        if new == State.FIN:
            self.stream_on = False
        if new == State.TERMINE:
            self.terminated_since = sig.t
        self.log(f"ÉTAT {old.value} → {new.value}  ({reason})  [horloge {sig.now:%H:%M:%S}]")
        if self.on_change:
            self.on_change(old, new, reason)

    def _open_session(self, sig: Signals) -> None:
        r = self.rec
        if r:
            r.status = "live"
            r.start_pos = self.pos_fn()
            r.start_clock = f"{sig.now:%H:%M:%S}"

    def _close_session(self, sig: Signals, reason: str) -> None:
        r = self.rec
        if r and r.status == "live":
            r.status = "done"
            r.end_pos = self.pos_fn()
            r.end_clock = f"{sig.now:%H:%M:%S}"
            r.end_reason = reason

    def _skip_current(self, sig: Signals, why: str) -> None:
        r = self.rec
        if r:
            r.status = "skipped"
            r.end_reason = why
        self.log(f"Session {r.planned if r else '?'} sautée : {why}")
        self.idx += 1

    def _voice_ok(self, sig: Signals) -> bool:
        if sig.voice_run < self.p.voice_start_s:
            return False
        return sig.presence is None or sig.presence

    def _after_session(self, sig: Signals, reason: str) -> State:
        """Après la prière : entracte s'il reste une session, sinon terminé."""
        self.idx += 1
        # saute les sessions dont la fenêtre est déjà close
        while self.session and sig.now > self.session + dt.timedelta(minutes=self.p.window_after_min):
            self._skip_current(sig, "fenêtre dépassée")
        return State.ENTRACTE if self.session else State.TERMINE

    # ----------------------------------------------------------------- commandes
    def command(self, cmd: str, sig: Signals, arg=None) -> str:
        """Commande manuelle. Renvoie un message de confirmation (ou d'erreur)."""
        cmd = cmd.lower()
        self.log(f"COMMANDE manuelle : {cmd} {arg if arg is not None else ''}")
        if cmd == "start":
            self.override = True
            if self.state in (State.IDLE, State.ARME, State.FIN) and self.session is None:
                self.idx = max(0, len(self.sessions) - 1) if self.sessions else 0
            if self.rec and self.rec.status == "pending":
                self._open_session(sig)
            self._go(State.KHUTBA, sig, "commande start")
            return "stream démarré, scène khutba (auto suspendu : « auto » pour reprendre)"
        if cmd == "stop":
            self.override = True
            if self.state == State.KHUTBA:
                self._close_session(sig, "commande stop")
            self._go(State.TERMINE, sig, "commande stop")
            return "écran Terminé puis arrêt du stream"
        if cmd == "pause":
            if self.state in (State.PRIERE,):
                return "déjà en pause"
            self.paused_from = self.state
            self.override = True
            self._go(State.PRIERE, sig, "commande pause")
            return "pause (écran Prière en cours)"
        if cmd == "resume":
            back = self.paused_from or State.KHUTBA
            self.paused_from = None
            self._go(back, sig, "commande resume")
            return f"reprise sur {back.value}"
        if cmd == "auto":
            self.override = False
            return "automatique repris"
        if cmd == "scene":
            sc = str(arg or "").lower()
            if sc not in MANUAL_SCENES:
                return f"scène inconnue : {sc} (valides : {sorted(MANUAL_SCENES)})"
            self.override = True
            if sc == "khutba" and self.rec and self.rec.status == "pending":
                self._open_session(sig)
            self._go(STATE_OF_SCENE[sc], sig, f"commande scène {sc}")
            return f"scène {sc} (auto suspendu)"
        if cmd == "extend":
            mins = float(arg or 5)
            base = max(self.extend_until_t, sig.t)
            self.extend_until_t = base + mins * 60  # minutes RÉELLES (khutba jouée en temps réel)
            return f"prolongé de {mins:g} min (pas d'arrêt automatique avant)"
        if cmd == "skip":
            if self.session is None:
                return "aucune session à sauter"
            if self.state == State.KHUTBA:
                self._close_session(sig, "commande skip")
            else:
                self._skip_current(sig, "commande skip")
                self.idx -= 1  # _after_session incrémente
            nxt = self._after_session(sig, "skip")
            self._go(nxt, sig, "commande skip")
            return f"session sautée, état {nxt.value}"
        if cmd == "cancel":
            for r in self.records:
                if r.status in ("pending", "live"):
                    r.status = "skipped"
                    r.end_reason = "annulé"
            self.idx = len(self.sessions)
            self.override = False
            self._go(State.TERMINE if self.stream_on else State.FIN, sig, "live du jour annulé")
            return "live du jour annulé"
        if cmd == "janaza":
            self.janaza_name = (arg or None)
            self.override = True
            # la Janaza ne produit AUCUN contenu dérivé : on la note comme exclue
            self.records.append(SessionRec(len(self.records), f"janaza@{sig.now:%H:%M}",
                                           status="janaza", start_pos=self.pos_fn(),
                                           start_clock=f"{sig.now:%H:%M:%S}",
                                           exclude_from_pipeline=True))
            self._go(State.JANAZA, sig, "commande janaza")
            return "mode Janaza (pas de bandeau ; exclue du pipeline IA)"
        return f"commande inconnue : {cmd}"

    # --------------------------------------------------------------------- tick
    def tick(self, sig: Signals) -> None:
        st = self.state

        # Terminé -> fin : toujours actif (même en override, c'est la sortie)
        if st == State.TERMINE:
            if sig.t - self.terminated_since >= self.p.terminated_hold_s:
                for r in self.records:
                    if r.status == "janaza" and r.end_pos is None:
                        r.end_pos = self.pos_fn()
                self._go(State.FIN, sig, "écran Terminé affiché, arrêt du stream")
            return
        if st == State.JANAZA:
            return  # uniquement manuel
        if self.override:
            return

        s = self.session
        if st in (State.IDLE, State.ARME, State.AVANT, State.ENTRACTE) and s is None:
            self._go(State.TERMINE if self.stream_on else State.FIN, sig, "plus aucune session")
            return

        if s is None and st in (State.IDLE, State.ARME, State.AVANT, State.ENTRACTE):
            return
        if st == State.IDLE:
            assert s is not None
            if sig.now >= s - dt.timedelta(minutes=self.p.arm_before_min):
                self._go(State.ARME, sig, f"fenêtre armée pour {s:%H:%M}")
            return

        if st in (State.ARME, State.AVANT, State.ENTRACTE):
            assert s is not None
            window_end = s + dt.timedelta(minutes=self.p.window_after_min)
            if sig.now > window_end:
                self._skip_current(sig, "aucun signal dans la fenêtre")
                if self.session is None:
                    self._go(State.TERMINE if self.stream_on else State.FIN, sig, "plus aucune session")
                else:
                    self._go(State.ENTRACTE if self.stream_on else State.IDLE, sig, "session suivante")
                return
            if st == State.ENTRACTE:
                armed = sig.now >= s - dt.timedelta(minutes=self.p.arm_before_min)
                if armed and self._voice_ok(sig):
                    self._open_session(sig)
                    self._go(State.KHUTBA, sig, "présence + voix (2e session)" if sig.presence is not None else "voix (2e session)")
                return
            if self._voice_ok(sig):
                if st == State.ARME:
                    self._go(State.AVANT, sig, "démarrage anticipé : voix détectée")
                self._open_session(sig)
                self._go(State.KHUTBA, sig, f"voix {sig.voice_run:.0f}s" + (" + présence" if sig.presence else ""))
                return
            if st == State.ARME and sig.now >= s - dt.timedelta(minutes=self.p.avant_before_min):
                self._go(State.AVANT, sig, f"T-{self.p.avant_before_min:g} min : stream lancé")
            return

        if st == State.KHUTBA:
            elapsed = sig.t - self.entered_t
            if elapsed >= self.p.max_khutba_s and sig.t >= self.extend_until_t:
                self._close_session(sig, "durée maximale")
                self._go(self._to_prayer(sig), sig, "garde-fou durée maximale")
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
                    self._close_session(sig, "silence prolongé")
                    self._go(self._to_prayer(sig), sig, "silence prolongé + délai de grâce")
            return

        if st == State.PRIERE:
            if self.paused_from is not None:
                return
            if self.entered_now and sig.now >= self.entered_now + dt.timedelta(minutes=self.p.prayer_min):
                nxt = self._after_session(sig, "fin de prière")
                self._go(nxt, sig, "fin de la prière (durée prévue)")
            return

    def _to_prayer(self, sig: Signals) -> State:
        return State.PRIERE

    # ------------------------------------------------------------------- statut
    def status(self, sig: Signals | None = None) -> dict:
        s = self.session
        return {
            "state": self.state.value,
            "scene": self.scene,
            "override": self.override,
            "stream_on": self.stream_on,
            "next_session": s.strftime("%H:%M") if s else None,
            "last_reason": self.last_reason,
            "janaza_name": self.janaza_name,
            "sessions": [r.__dict__ for r in self.records],
        }
