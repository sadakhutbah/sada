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
    DARS = "DARS"          # dars ou conférence : caméra + bandeau de l'événement
    DARS_FIN = "DARS_FIN"  # écran de fin d'un dars (prochain cours), avant arrêt du stream
    FIN = "FIN"            # stream arrêté


# scène affichée par état (le visuel, rendu par scenes.py) ; pas de scène = pas de stream
SCENE_OF = {
    State.IDLE: None, State.ARME: None, State.FIN: None,
    State.AVANT: "avant", State.KHUTBA: "khutba", State.PRIERE: "priere",
    State.ENTRACTE: "entracte", State.TERMINE: "termine", State.JANAZA: "janaza",
    State.DARS: "dars", State.DARS_FIN: "dars_fin",
}
STATE_OF_SCENE = {v: k for k, v in SCENE_OF.items() if v}
WAITING = (State.IDLE, State.ARME, State.AVANT, State.ENTRACTE)
# libellés d'affichage du back-office (états bruts : DARS, DARS_FIN…)
STATE_LABELS = {
    State.IDLE: "En veille", State.ARME: "Armé (fenêtre ouverte)", State.AVANT: "Début imminent",
    State.KHUTBA: "Khutba en direct", State.PRIERE: "Prière en cours", State.ENTRACTE: "Entracte",
    State.TERMINE: "Terminé", State.JANAZA: "Salat al-Janaza", State.DARS: "Dars en direct",
    State.DARS_FIN: "Dars terminé", State.FIN: "Live terminé",
}


@dataclass
class Signals:
    now: dt.datetime
    t: float
    voice_run: float = 0.0
    silence_run: float = 0.0
    presence: bool | None = None    # mouvement au minbar ; None = détection désactivée
    face_run: float | None = None    # secondes sans visage de face au minbar ; None = détection indisponible


@dataclass
class MachineParams:
    # minutes d'horloge mosquée, comptées depuis l'horaire de la session (T)
    arm_before_min: float = 15.0        # armement : on écoute la voix
    avant_before_min: float = 2.0       # scène Avant : stream démarré
    window_after_min: float = 45.0      # sans signal au-delà : session sautée
    prayer_min: float = 12.0            # durée supposée de la prière
    # durées en secondes RÉELLES : le signal est en temps réel
    voice_start_s: float = 20.0         # voix soutenue => début de khutba
    silence_stop_s: float = 60.0        # silence prolongé => fin de khutba
    grace_s: float = 30.0               # délai de grâce avant de couper
    min_khutba_s: float = 120.0         # pas d'arrêt avant cette durée
    # visage au minbar : l'imam tourne le dos pendant la prière (0 = signal ignoré)
    face_absence_s: float = 45.0        # plus de visage de face depuis ce délai ...
    face_silence_ratio: float = 0.5     # ... ET au moins cette part du seuil de silence
    face_return_s: float = 3.0          # visage de retour sous ce délai : la grâce s'annule
    max_khutba_s: float = 3600.0
    terminated_hold_s: float = 45.0     # écran Terminé avant arrêt du stream
    # dars et conférences : plus souples que la khutba (aucune prière ne suit)
    dars_window_after_min: float = 90.0 # un dars peut commencer plus tard qu'une khutba
    dars_max_s: float = 7200.0
    dars_silence_stop_s: float = 180.0
    dars_grace_s: float = 90.0
    dars_fin_hold_s: float = 45.0       # écran « Dars terminé » avant arrêt du stream


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
    kind: str = "khutba"                # khutba | dars | conference
    title: str = ""
    speaker: str = ""
    series: str = ""
    session_no: Optional[int] = None

    def event(self) -> Optional[dict]:
        """Événement porté par la session (None pour une khutba ordinaire) ; même forme que EventInstance.as_event()."""
        if self.kind == "khutba":
            return None
        return {"kind": self.kind, "title": self.title, "speaker": self.speaker,
                "series": self.series, "session_no": self.session_no}


class Machine:
    def __init__(self, sessions: list[dt.datetime], params: MachineParams | None = None,
                 log: Callable[[str], None] | None = None,
                 pos_fn: Callable[[], tuple[int, float]] | None = None,
                 extras: list[dict] | None = None):
        """pos_fn() -> (n° de l'enregistrement, position en s) : où en est l'enregistrement du live.

        `extras` suit `sessions` (même ordre avant tri) : événement de chaque session (forme
        `EventInstance.as_event()`, `{}` pour une khutba ordinaire). Aucune liste parallèle : le type
        de la session est dans son événement.
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
        self.live: Optional[SessionRec] = None   # session ouverte (khutba ou janaza)
        self.entered_t = 0.0
        self.entered_now: Optional[dt.datetime] = None
        self.grace_until: Optional[float] = None
        self.grace_reason: Optional[str] = None
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
        if old == State.JANAZA and self.paused_from is None:
            self.janaza_name = None
        if self.live and new not in (State.KHUTBA, State.DARS, State.JANAZA) and self.paused_from is None:
            self._close(sig, reason)        # la session ouverte s'arrête dès qu'on quitte son écran (sauf pause)
        if new in (State.KHUTBA, State.DARS) and not (self.live and self.live.status == "live"):
            r = self.rec if self.rec and self.rec.status == "pending" else self._extra("manuel", sig)
            r.status = "live"
            self._open(r, sig)
        if new == State.FIN:                # la journée est finie : plus rien à venir
            for r in self.records:
                if r.status == "pending":
                    r.status, r.end_reason = "skipped", reason
            self.idx = len(self.sessions)
        if new == State.JANAZA and not (self.live and self.live.status == "janaza"):
            # la Janaza ne produit AUCUN contenu dérivé (runs.produces_content)
            r = self._extra("janaza", sig)
            r.status = "janaza"
            self._open(r, sig)
        self.log(f"ÉTAT {old.value} → {new.value}  ({reason})  [horloge {sig.now:%H:%M:%S}]")

    def _extra(self, kind: str, sig: Signals) -> SessionRec:
        """Session hors programme, lancée à la main : c'est une khutba (la Janaza est marquée par son statut)."""
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

    @property
    def kind(self) -> str:
        """Type de la session prévue en cours (khutba, dars, conference)."""
        return self.rec.kind if self.rec else "khutba"

    def _entry_state(self) -> State:
        """État d'entrée d'une session : DARS pour un dars / une conférence, KHUTBA sinon."""
        return State.KHUTBA if self.kind == "khutba" else State.DARS

    def _window_end(self) -> dt.datetime:
        assert self.session is not None
        late = self.p.window_after_min if self.kind == "khutba" else self.p.dars_window_after_min
        return self.session + dt.timedelta(minutes=late)

    def _next_session(self, sig: Signals) -> State:
        """Passe à la session prévue suivante (en sautant celles dont la fenêtre est close) -> état d'attente.

        Une session qui n'a pas eu lieu (prière affichée à la main avant elle) reste la prochaine."""
        if not (self.rec and self.rec.status == "pending"):
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
        st = self._entry_state()
        self._go(st, sig, "commande start")
        sc = SCENE_OF[st]
        return f"stream démarré, scène {sc} (auto suspendu : « auto » pour reprendre)"

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
        if self.paused_from is None:
            return "pas en pause"
        back = self.paused_from
        self._go(back, sig, "commande resume")
        return f"reprise sur {back.value}"

    def _auto(self, sig, arg):
        if self.paused_from is not None:
            self._go(self.paused_from, sig, "commande auto (fin de pause)")
        if self.state == State.JANAZA:           # fin de la janaza : les sessions restantes reprennent en automatique
            self._go(State.ENTRACTE if self.session else self._end_state(), sig, "commande auto (fin de janaza)")
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
        for r in self.records:
            if r.status in ("pending", "live"):
                r.status = "skipped"
                r.end_reason = "annulé"
        self.idx = len(self.sessions)       # TERMINÉ d'abord si le stream tourne : plus rien à venir dès maintenant
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
        if st == State.DARS_FIN:
            if sig.t - self.entered_t >= self.p.dars_fin_hold_s:
                self._go(State.FIN, sig, "écran de fin du dars affiché, arrêt du stream")
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
            entry = self._entry_state()
            if st == State.ENTRACTE:
                if sig.now >= s and self._voice_ok(sig):
                    self._go(entry, sig, "voix (2e session)")
                return
            if self._voice_ok(sig) and sig.now >= s:
                if st == State.ARME:
                    self._go(State.AVANT, sig, "démarrage anticipé : voix détectée")
                self._go(entry, sig, f"voix {sig.voice_run:.0f}s (horaire)")
                return
            if st == State.ARME and self._voice_ok(sig):
                self._go(State.AVANT, sig, "démarrage anticipé : voix détectée")
                return
            if st == State.ARME and sig.now >= s - dt.timedelta(minutes=self.p.avant_before_min):
                self._go(State.AVANT, sig, f"T-{self.p.avant_before_min:g} min : stream lancé")
            return

        if st in (State.KHUTBA, State.DARS):
            dars = st == State.DARS
            max_s = self.p.dars_max_s if dars else self.p.max_khutba_s
            silence_s = self.p.dars_silence_stop_s if dars else self.p.silence_stop_s
            grace_s = self.p.dars_grace_s if dars else self.p.grace_s
            end = State.DARS_FIN if dars else State.PRIERE
            elapsed = sig.t - self.entered_t
            if elapsed >= max_s and sig.t >= self.extend_until_t:
                self._go(end, sig, "durée maximale")
                return
            if sig.t < self.extend_until_t:
                self.grace_until = None
                return
            # le silence seul ne conclut rien (une salle pleine n'est jamais muette) :
            # il faut le silence ET le minbar vide de tout visage de face
            face_gone = (not dars and sig.face_run is not None and self.p.face_absence_s > 0
                         and sig.face_run >= self.p.face_absence_s
                         and sig.silence_run >= silence_s * self.p.face_silence_ratio)
            if self.grace_until is None:
                if sig.silence_run >= silence_s and elapsed >= self.p.min_khutba_s:
                    self.grace_until = sig.t + grace_s
                    self.grace_reason = "silence prolongé"
                    self.log(f"silence {sig.silence_run:.0f}s : délai de grâce {grace_s:.0f}s")
                elif face_gone and elapsed >= self.p.min_khutba_s:
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
            "kind": self.kind,
            "event": self.rec.event() if self.rec else None,
            "sessions": self.sessions_dicts(),
        }

    def sessions_dicts(self) -> list[dict]:
        return [asdict(r) for r in self.records]
