"""Encodeur continu : UN seul ffmpeg produit le flux RTMP + l'enregistrement MKV + l'enregistrement PROPRE.

Graphe : [caméra] -> scale/fps -> zmq (commandes vidéo)
                  -> overlay@fond (écrans pleins : fond commun, opacité pilotée)
                  -> overlay@ca / overlay@cb (contenu de scène, double tampon : opacité + position pilotées)
                  -> x264 -> tee (RTMP | live_*.mkv) ;
         [audio caméra] -> volume@mic (piloté en direct via ZeroMQ) -> asplit -> aac (live) / aac (propre).
Sortie propre (live_*_clean.mkv) : la vidéo caméra SANS habillage (copie du flux 0:v si clean_copy, sinon x264 crf 18
depuis la caméra mise à l'échelle) + le même audio. Même processus, même démarrage : les positions temporelles
de live_*.mkv et live_*_clean.mkv sont alignées (les clips sont taillés dans le propre aux positions du live).
Changer de scène = écrire le contenu dans le calque inactif puis l'animer (transitions.py) : fondu enchaîné,
glissement d'entrée, fondu sonore du micro. Le processus ffmpeg n'est jamais relancé : le flux ne s'interrompt pas.
"""
from __future__ import annotations
import datetime as dt
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import zmq
from PIL import Image

from . import transitions as tr


def _zmq_esc(port: int) -> str:
    return "tcp" + "\\" * 2 + "://127.0.0.1" + "\\" * 2 + f":{port}"  # « : » échappés deux fois (graphe puis options du filtre)


ZMQ_ADDR = "tcp://127.0.0.1:5556"        # audio (azmq)
ZMQ_ESC = _zmq_esc(5556)
# vidéo : un filtre zmq (donc un port) par paramètre animé d'une transition, cf. Encoder._animate
ZMQ_VPORTS = (5557, 5558, 5559, 5560)
ZMQ_VADDRS = [f"tcp://127.0.0.1:{p}" for p in ZMQ_VPORTS]


def write_png_atomic(img: Image.Image, path: str) -> None:
    tmp = f"{path}.tmp.png"
    img.save(tmp, "PNG", compress_level=1)
    os.replace(tmp, path)


class Encoder:
    def __init__(self, cfg, run_dir: Path, log=print):
        self.cfg, self.log = cfg, log
        # calques : fond commun des écrans pleins + deux calques de contenu (double tampon)
        self.run_dir = Path(run_dir)
        self.layer_png = {k: str(self.run_dir / f"scene_{k}.png") for k in ("fond", "a", "b")}
        # valeurs courantes des calques (aussi valeurs initiales du graphe si ffmpeg est relancé)
        self.levels: dict[str, float] = {"fond": 1.0, "a_a": 1.0, "x_a": 0.0, "y_a": 0.0,
                                         "a_b": 0.0, "x_b": 0.0, "y_b": 0.0}
        self.active = "a"                    # calque du contenu affiché
        self.shown: Optional[tuple] = None   # (clé de scène, calque) : contenu affiché ou en cours d'entrée
        self.full = True                     # la scène affichée est un écran plein
        self.vol = 0.0                       # niveau du micro (0..1), cible du dernier fondu
        self._mx = threading.Lock()          # protège pending / shown
        self._anim = threading.Lock()        # une seule transition à la fois
        self._pending = None
        self._ev = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.proc: subprocess.Popen | None = None
        self.out_time = 0.0       # position (s) dans l'enregistrement courant, d'après -progress
        self.restarts = 0
        self._lock = threading.Lock()
        self.mic_on = False
        self.wanted = False
        # un enregistrement par lancement de ffmpeg : (live_*.mkv, live_*_clean.mkv ou None)
        self.recordings: list[tuple[str, Optional[str]]] = []
        self._log_file = None

    # ------------------------------------------------------------------ commande
    def _new_recording(self) -> tuple[str, Optional[str]]:
        c = self.cfg
        Path(c.record_dir).mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        rec = str(Path(c.record_dir) / f"live_{stamp}.mkv")
        return rec, (str(Path(c.record_dir) / f"live_{stamp}_clean.mkv") if c.record_clean else None)

    def _cmd(self, rec: str, clean: Optional[str]) -> list[str]:
        c = self.cfg
        copy = bool(clean and c.clean_copy)
        L = {k: f"{v:g}" for k, v in self.levels.items()}
        fg = (
            f"[0:v]scale={c.width}:{c.height}:force_original_aspect_ratio=decrease,"
            f"pad={c.width}:{c.height}:(ow-iw)/2:(oh-ih)/2,fps={c.fps},setsar=1,format=yuv420p"
            + ("[cam];" if not clean or copy else ",split=2[cam][camclean];")
            + "[cam]" + ",".join(f"zmq@z{i}=bind_address={_zmq_esc(p)}" for i, p in enumerate(ZMQ_VPORTS)) + "[camz];"
            # fond : une seule image, répétée en mémoire (pas de relecture)
            f"[1:v]format=rgba,loop=loop=-1:size=1,colorchannelmixer@fond=aa={L['fond']}[fond];"
            f"[2:v]format=rgba,fps={c.fps},colorchannelmixer@ca=aa={L['a_a']}[ca];"
            f"[3:v]format=rgba,fps={c.fps},colorchannelmixer@cb=aa={L['a_b']}[cb];"
            f"[camz][fond]overlay@fond=format=auto:shortest=0:repeatlast=1[v0];"
            f"[v0][ca]overlay@ca=x={L['x_a']}:y={L['y_a']}:format=auto:shortest=0:repeatlast=1[v1];"
            f"[v1][cb]overlay@cb=x={L['x_b']}:y={L['y_b']}:format=auto:shortest=0:repeatlast=1,format=yuv420p[v];"
            f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume@mic=volume={self.vol:g}:eval=frame,azmq=bind_address={ZMQ_ESC}"
            + ("[a]" if not clean else ",asplit=2[a][aclean]")
        )
        # calques de contenu relus à {fps} i/s : la file d'attente d'ffmpeg (~15 images) borne le délai
        # entre l'écriture d'un PNG et son apparition à ~0,6 s (à 4 i/s : ~4 s), cf. tr.DELAI_CHARGEMENT
        slot = ["-re", "-f", "image2", "-loop", "1", "-framerate", str(c.fps), "-i"]
        out_clean: list[str] = []
        if clean:
            vmap = ["-map", "0:v:0", "-c:v:0", "copy"] if copy else [
                "-map", "[camclean]", "-c:v:0", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(c.fps * 2), "-pix_fmt", "yuv420p"]
            out_clean = vmap + ["-map", "[aclean]", "-c:a:0", "aac", "-b:a:0", "160k", "-ar", "48000", "-f", "matroska", clean]
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1",
            "-rtsp_transport", "tcp", "-i", c.camera_url,
            "-framerate", str(c.fps), "-i", self.layer_png["fond"],
            *slot, self.layer_png["a"], *slot, self.layer_png["b"],
            "-filter_complex", fg, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-b:v", c.video_bitrate,
            "-maxrate", c.video_bitrate, "-bufsize", "5000k", "-g", str(c.fps * 2), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-flags", "+global_header",
            "-f", "tee", f"[f=flv:onfail=ignore]{c.rtmp_url}|[f=matroska]{rec}",
        ] + out_clean

    # ----------------------------------------------------------------- cycle de vie
    def start(self):
        with self._lock:
            self.wanted = True
            self._spawn()
        threading.Thread(target=self._watch, daemon=True, name="encoder-watch").start()

    def _spawn(self):
        rec, clean = self._new_recording()
        self.log("ENCODEUR : démarrage ffmpeg -> " + self.cfg.rtmp_url + "  | enreg. " + rec
                 + (f"  | propre {clean}" if clean else ""))
        if self._log_file:
            self._log_file.close()
        Path(self.cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.run_dir / "encoder.log", "ab")
        self.out_time = 0.0
        self.recordings.append((rec, clean))
        self.proc = subprocess.Popen(self._cmd(rec, clean), stdout=subprocess.PIPE, stderr=self._log_file, text=True, bufsize=1)
        threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()

    def _read_progress(self, proc):
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_us="):
                try:
                    self.out_time = int(line.split("=")[1]) / 1e6
                except ValueError:
                    pass

    def _watch(self):
        while True:
            time.sleep(1.0)
            with self._lock:
                if not self.wanted:
                    return
                if self.proc and self.proc.poll() is not None:
                    self.restarts += 1
                    self.log(f"ENCODEUR : ffmpeg arrêté (code {self.proc.returncode}), reprise automatique #{self.restarts}")
                    time.sleep(2.0)
                    self._spawn()     # le graphe relancé repart de l'état courant (levels, vol)

    def stop(self):
        with self._lock:
            self.wanted = False
            p = self.proc
        if p and p.poll() is None:
            p.stdin and p.stdin.close()
            p.terminate()  # SIGTERM : ffmpeg finalise proprement le MKV
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        self.log("ENCODEUR : arrêté")

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def position(self) -> tuple[int, float]:
        """(n° de l'enregistrement, position en s dans celui-ci). Flux coupé : le prochain enregistrement, à 0 s."""
        if not self.wanted:
            return len(self.recordings), 0.0
        return len(self.recordings) - 1, self.out_time

    # ------------------------------------------------------------------ habillage
    def prepare(self, background) -> None:
        """Écrit le fond commun des écrans pleins et des calques de contenu vides (avant le démarrage)."""
        write_png_atomic(background, self.layer_png["fond"])
        empty = Image.new("RGBA", background.size, (0, 0, 0, 0))
        for k in ("a", "b"):
            write_png_atomic(empty, self.layer_png[k])

    def show(self, scene: str, full: bool, content) -> None:
        """Affiche une scène : contenu (calque transparent) + fond si écran plein (scenes.layers).

        Même scène qu'à l'écran (ex. compte à rebours de l'entracte) : mise à jour du calque en place.
        Autre scène : transition animée par le fil « encoder-transitions » (le flux n'est jamais coupé).
        Encodeur pas encore lancé : état posé directement, sans animation."""
        with self._mx:
            if self._pending and self._pending[0] == scene:
                self._pending = (scene, full, content)
                return
            if self.shown and self.shown[0] == scene:
                self._pending = None                     # retour à la scène affichée : rien à animer
                write_png_atomic(content, self.layer_png[self.shown[1]])
                return
            if not self.wanted:                          # hors direct : pas d'animation
                write_png_atomic(content, self.layer_png[self.active])
                self.shown, self.full = (scene, self.active), full
                self.levels.update({"fond": 1.0 if full else 0.0, f"a_{self.active}": 1.0})
                return
            self._pending = (scene, full, content)
            self._ensure_worker()
            self._ev.set()

    def _ensure_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._transitions, daemon=True, name="encoder-transitions")
            self._worker.start()

    def _transitions(self):
        while True:
            self._ev.wait()
            self._ev.clear()
            src = dst = self.active
            with self._mx:
                job, self._pending = self._pending, None
                if job:
                    src = self.active
                    dst = "b" if src == "a" else "a"
                    # le calque inactif est transparent (fin de la transition précédente) : on y prépare la scène
                    write_png_atomic(job[2], self.layer_png[dst])
                    self.shown = (job[0], dst)
            while job:
                time.sleep(tr.DELAI_CHARGEMENT)          # ffmpeg relit le PNG avant qu'on le dévoile
                with self._mx:                           # scène changée entre-temps (ex. prière -> janaza) :
                    if not self._pending:                # on prépare directement la plus récente
                        break
                    job, self._pending = self._pending, None
                    write_png_atomic(job[2], self.layer_png[dst])
                    self.shown = (job[0], dst)
            target = 1.0 if self.mic_on else 0.0
            if target != self.vol:                       # fondu sonore, même départ que l'image
                expr = tr.volume_ramp(self.vol, target)
                self.vol = target
                if self.alive():
                    threading.Thread(target=self._zmq_send, args=(f"volume@mic volume {expr}",), daemon=True).start()
            if not job:
                continue
            with self._anim:
                p = tr.plan(src, self.full, job[1], scale=self.cfg.height / 720)
                frames = self._animate(p)
                self.active, self.full = dst, job[1]
                self.log(f"TRANSITION -> {job[0]} (calque {dst}, {frames} images)")

    # canal ZeroMQ (port) par paramètre animé : un filtre zmq par canal sur la chaîne caméra
    _TARGET = {"fond": "colorchannelmixer@fond aa", "a_a": "colorchannelmixer@ca aa", "a_b": "colorchannelmixer@cb aa",
               "x_a": "overlay@ca x", "y_a": "overlay@ca y", "x_b": "overlay@cb x", "y_b": "overlay@cb y"}

    def _animate(self, p: "tr.Plan") -> int:
        """Joue la transition image par image, calée sur le flux (et non sur l'horloge murale).

        Un socket REQ par canal (fond, contenu sortant, contenu entrant, position) : le filtre zmq (REP) traite
        au plus une requête par image et ne répond qu'en la traitant ; on n'envoie la valeur de l'image n+1
        qu'après la réponse de l'image n. L'animation avance donc d'un pas par image produite : pas de saccade
        même si ffmpeg traite les images par rafales (elle s'allonge au pire de quelques images).
        Retourne le nombre d'images jouées (0 si ffmpeg ne répond pas : l'état final est posé à la relance)."""
        final = p.values(tr.TOTAL)
        move = f"y_{p.dst}" if p.full else f"x_{p.dst}"
        keys = ([] if p.fond_from == p.fond_to else ["fond"]) + [f"a_{p.src}", f"a_{p.dst}", move]
        n_last = int(round(tr.TOTAL * self.cfg.fps))
        played = 0
        if self.alive():
            ctx = zmq.Context.instance()
            poller, socks, idx = zmq.Poller(), {}, {}

            def send(s, k, i):
                v = p.values(min(i / self.cfg.fps, tr.TOTAL))[k]
                s.send_string(f"{self._TARGET[k]} {round(v, 3):g}")

            for ch, k in enumerate(keys):
                s = ctx.socket(zmq.REQ)
                s.setsockopt(zmq.LINGER, 0)
                s.connect(ZMQ_VADDRS[ch])
                socks[s], idx[s] = k, 0
                poller.register(s, zmq.POLLIN)
                send(s, k, 0)
            quiet_since = time.monotonic()
            try:
                while socks:
                    ready = dict(poller.poll(100))
                    for s in list(socks):
                        if s not in ready:
                            continue
                        s.recv_string()
                        idx[s] += 1
                        played = max(played, idx[s])
                        quiet_since = time.monotonic()
                        if idx[s] > n_last:
                            poller.unregister(s)
                            del socks[s]
                            s.close()
                        else:
                            send(s, socks[s], idx[s])
                    if socks and time.monotonic() - quiet_since > 2.0:
                        self.log("TRANSITION : ffmpeg ne répond plus, état final posé pour la relance")
                        break
            finally:
                for s in socks:
                    s.close()
        self.levels.update({k: round(v, 3) for k, v in final.items()})
        return played

    # ------------------------------------------------------------------- audio live
    def mic(self, on: bool):
        """Micro du direct (khutba, janaza) : fondu de tr.AUDIO_DUR, synchronisé sur la transition en attente."""
        with self._mx:
            self.mic_on = on
            if not self.wanted:
                self.vol = 1.0 if on else 0.0            # valeur initiale du graphe au démarrage
                return
            self._ensure_worker()
            self._ev.set()

    def _zmq_send(self, msg: str):
        ctx = zmq.Context.instance()
        for attempt in range(4):
            s = ctx.socket(zmq.REQ)
            s.setsockopt(zmq.LINGER, 0)
            s.setsockopt(zmq.RCVTIMEO, 3000)
            s.setsockopt(zmq.SNDTIMEO, 3000)
            s.connect(ZMQ_ADDR)
            try:
                s.send_string(msg)
                rep = s.recv_string()
                self.log(f"ZMQ « {msg} » -> {rep.strip()}")
                return
            except Exception as e:  # noqa
                self.log(f"ZMQ essai {attempt + 1} échoué ({msg}) : {e}")
            finally:
                s.close()
            time.sleep(1.0)
