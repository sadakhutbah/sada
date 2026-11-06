"""Encodeur continu : UN seul ffmpeg produit le flux RTMP + l'enregistrement MKV + l'enregistrement PROPRE.

Graphe : [caméra] -> scale/fps -> overlay( PNG de scène relu en boucle ) -> x264 -> tee (RTMP | live_*.mkv) ;
         [audio caméra] -> volume@mic (piloté en direct via ZeroMQ) -> asplit -> aac (live) / aac (propre).
Sortie propre (live_*_clean.mkv) : la vidéo caméra SANS habillage (copie du flux 0:v si clean_copy, sinon x264 crf 18
depuis la caméra mise à l'échelle) + le même audio. Même processus, même démarrage : les positions temporelles
de live_*.mkv et live_*_clean.mkv sont alignées (les clips sont taillés dans le propre aux positions du live).
Changer de scène = réécrire le PNG + envoyer « volume@mic volume 0|1 » : le flux ne s'interrompt pas.
"""
from __future__ import annotations
import datetime as dt
import subprocess
import threading
import time
from pathlib import Path

ZMQ_ADDR = "tcp://127.0.0.1:5556"
ZMQ_ESC = "tcp" + "\\" * 2 + "://127.0.0.1" + "\\" * 2 + ":5556"  # « : » échappés deux fois (graphe puis options du filtre)


class Encoder:
    def __init__(self, cfg, scene_png: str, log=print):
        self.cfg, self.scene_png, self.log = cfg, scene_png, log
        self.proc: subprocess.Popen | None = None
        self.out_time = 0.0       # position (s) dans l'enregistrement, d'après -progress
        self.recording: str | None = None
        self.started_at: float = 0.0
        self.restarts = 0
        self._lock = threading.Lock()
        self.mic_on = False
        self.wanted = False
        self.recordings: list[str] = []
        self.recordings_clean: list[str] = []   # même index que recordings (caméra sans habillage)
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ commande
    def _cmd(self) -> list[str]:
        c = self.cfg
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        rec = str(Path(c.record_dir) / f"live_{stamp}.mkv")
        clean = str(Path(c.record_dir) / f"live_{stamp}_clean.mkv") if c.record_clean else None
        self.recording = rec
        self.recordings.append(rec)
        if clean:
            self.recordings_clean.append(clean)
        copy = bool(clean and c.clean_copy)
        fg = (
            f"[0:v]scale={c.width}:{c.height}:force_original_aspect_ratio=decrease,"
            f"pad={c.width}:{c.height}:(ow-iw)/2:(oh-ih)/2,fps={c.fps},setsar=1,format=yuv420p"
            + ("[cam];" if not clean or copy else ",split=2[cam][camclean];")
            + f"[1:v]format=rgba,fps={c.fps}[ov];"
            f"[cam][ov]overlay=format=auto:shortest=0:repeatlast=1,format=yuv420p[v];"
            f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume@mic=volume={1 if self.mic_on else 0}:eval=frame,azmq=bind_address={ZMQ_ESC}"
            + ("[a]" if not clean else ",asplit=2[a][aclean]")
        )
        out_clean: list[str] = []
        if clean:
            vmap = ["-map", "0:v:0", "-c:v:0", "copy"] if copy else [
                "-map", "[camclean]", "-c:v:0", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(c.fps * 2), "-pix_fmt", "yuv420p"]
            out_clean = vmap + ["-map", "[aclean]", "-c:a:0", "aac", "-b:a:0", "160k", "-ar", "48000", "-f", "matroska", clean]
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1",
            "-rtsp_transport", "tcp", "-i", c.camera_url,
            "-re", "-f", "image2", "-loop", "1", "-framerate", "4", "-i", self.scene_png,
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
        t = threading.Thread(target=self._watch, daemon=True, name="encoder-watch")
        t.start()
        self._threads.append(t)

    def _spawn(self):
        cmd = self._cmd()
        self.log("ENCODEUR : démarrage ffmpeg -> " + self.cfg.rtmp_url + "  | enreg. " + str(self.recording)
                 + (f"  | propre {self.recordings_clean[-1]}" if self.cfg.record_clean else ""))
        self.log_file = open(Path(self.cfg.state_dir) / "encoder.log", "ab")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self.log_file, text=True, bufsize=1)
        self.started_at = time.monotonic()
        self.out_time = 0.0
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
                    self._spawn()
                    self.mic(self.mic_on)

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

    def pos(self) -> float:
        """Position courante dans l'enregistrement (s)."""
        return self.out_time

    # ------------------------------------------------------------------- audio live
    def mic(self, on: bool):
        self.mic_on = on
        if not self.alive():
            return
        self._zmq_send(f"volume@mic volume {1 if on else 0}")

    def _zmq_send(self, msg: str):
        try:
            import zmq  # type: ignore
        except ImportError:
            self.log("ENCODEUR : pyzmq manquant, audio non commutable")
            return
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
