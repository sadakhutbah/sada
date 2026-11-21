"""Continuous encoder: ONE single ffmpeg produces the RTMP stream + the MKV recording + the CLEAN recording.

Graph: [camera] -> scale/fps -> zmq (video commands)
                  -> overlay@fond (full screens: shared background, driven opacity)
                  -> overlay@ca / overlay@cb (scene content, double buffer: driven opacity + position)
                  -> x264 -> tee (RTMP | live_*.mkv);
         [camera audio] -> volume@mic (driven live via ZeroMQ) -> asplit -> aac (live) / aac (clean).
Clean output (live_*_clean.mkv): the camera video WITHOUT overlay (copy of stream 0:v if clean_copy, otherwise x264 crf 18
from the scaled camera) + the same audio. Same process, same start: the time positions
of live_*.mkv and live_*_clean.mkv are aligned (clips are cut from the clean file at the live positions).
Changing scene = writing the content into the inactive layer then animating it (transitions.py): crossfade,
slide-in, audio fade of the mic. The ffmpeg process is never restarted: the stream is not interrupted.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import zmq
from PIL import Image

from . import source
from . import transitions as tr
from .i18n import _

PREVIEW_UDP = "udp://127.0.0.1:23456"


def _zmq_esc(port: int) -> str:
    return "tcp" + "\\" * 2 + "://127.0.0.1" + "\\" * 2 + f":{port}"  # ":" escaped twice (graph then filter options)


ZMQ_APORT = 5556  # default base port: audio (azmq); the four next ones drive the video
# video: one zmq filter (hence one port) per animated parameter of a transition, see Encoder._animate
ZMQ_VIDEO = 4


def zmq_ports(base: int = ZMQ_APORT) -> tuple[int, tuple[int, ...]]:
    """Audio port and video ports from one base (`zmq_port`): two orchestrators on one machine
    get distinct bases, in a container the default is fine."""
    return base, tuple(range(base + 1, base + 1 + ZMQ_VIDEO))


ZMQ_ADDR = f"tcp://127.0.0.1:{ZMQ_APORT}"
ZMQ_ESC = _zmq_esc(ZMQ_APORT)
ZMQ_VPORTS = zmq_ports()[1]
ZMQ_VADDRS = [f"tcp://127.0.0.1:{p}" for p in ZMQ_VPORTS]


def write_png_atomic(img: Image.Image, path: str) -> None:
    tmp = f"{path}.tmp.png"
    img.save(tmp, "PNG", compress_level=1)
    os.replace(tmp, path)


# tee muxer message when one output (destination or recording) was dropped for good
SLAVE_FAILED = re.compile(r"Slave muxer #\d+ failed")

# an ffmpeg younger than this may simply not have bound its zmq ports yet: never judged stuck
STUCK_GRACE_S = 20.0

# Relaunch delay after a failed ffmpeg start: doubles from BACKOFF_MIN_S up to BACKOFF_MAX_S, reset once the
# source answers (a source that is not published yet must not produce a relaunch storm).
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0


def backoff_delay(failures: int) -> float:
    """Delay before relaunch number `failures` (1 = first consecutive failure): 1, 2, 4, ... capped at 30 s."""
    return min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** max(0, failures - 1))


class Encoder:
    def __init__(self, cfg, run_dir: Path, log=print):
        self.cfg, self.log = cfg, log
        # layers: shared background of the full screens + two content layers (double buffer)
        self.run_dir = Path(run_dir)
        self.layer_png = {k: str(self.run_dir / f"scene_{k}.png") for k in ("fond", "a", "b")}
        # current layer values (also the graph's initial values if ffmpeg is restarted)
        self.levels: dict[str, float] = {
            "fond": 1.0,
            "a_a": 1.0,
            "x_a": 0.0,
            "y_a": 0.0,
            "a_b": 0.0,
            "x_b": 0.0,
            "y_b": 0.0,
        }
        self.active = "a"  # layer of the displayed content
        self.shown: tuple | None = None  # (scene key, layer): content shown or coming in
        self.full = True
        self.vol = 0.0  # mic level (0..1), target of the last fade
        self._mx = threading.Lock()  # protects pending / shown
        self._anim = threading.Lock()  # one transition at a time
        self._pending = None
        self._ev = threading.Event()
        self._worker: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.preview: subprocess.Popen | None = None
        self.zmq_aport, self.zmq_vports = zmq_ports(int(getattr(cfg, "zmq_port", ZMQ_APORT) or ZMQ_APORT))
        self.out_time = 0.0  # position (s) in the current recording, from -progress
        self.restarts = 0
        self._lock = threading.Lock()
        self.mic_on = False
        self.wanted = False
        # one recording per ffmpeg launch: (live_*.mkv, live_*_clean.mkv or None)
        self.recordings: list[tuple[str, str | None]] = []
        self._log_file = None
        self._spawned_at = 0.0  # monotonic time of the last ffmpeg launch
        self._dropped = False  # the tee muxer dropped an output of the current ffmpeg
        self._opened = False  # the current ffmpeg opened its source (it produced output)
        self.failures = 0  # consecutive launches that never opened the source
        self._next_spawn = 0.0  # monotonic time before which no relaunch happens

    # ------------------------------------------------------------------- command
    def new_recording(self) -> tuple[str, str | None]:
        c = self.cfg
        Path(c.record_dir).mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        rec = str(Path(c.record_dir) / f"live_{stamp}.mkv")
        return rec, (str(Path(c.record_dir) / f"live_{stamp}_clean.mkv") if c.record_clean else None)

    def ffmpeg_cmd(self, rec: str, clean: str | None) -> list[str]:
        c = self.cfg
        copy = bool(clean and c.clean_copy)
        L = {k: f"{v:g}" for k, v in self.levels.items()}
        fg = (
            f"[0:v]scale={c.width}:{c.height}:force_original_aspect_ratio=decrease,"
            f"pad={c.width}:{c.height}:(ow-iw)/2:(oh-ih)/2,fps={c.fps},setsar=1,format=yuv420p"
            + ("[cam];" if not clean or copy else ",split=2[cam][camclean];")
            + "[cam]"
            + ",".join(f"zmq@z{i}=bind_address={_zmq_esc(p)}" for i, p in enumerate(self.zmq_vports))
            + "[camz];"
            # background: a single image, repeated in memory (no re-read)
            f"[1:v]format=rgba,loop=loop=-1:size=1,colorchannelmixer@fond=aa={L['fond']}[fond];"
            f"[2:v]format=rgba,fps={c.fps},colorchannelmixer@ca=aa={L['a_a']}[ca];"
            f"[3:v]format=rgba,fps={c.fps},colorchannelmixer@cb=aa={L['a_b']}[cb];"
            f"[camz][fond]overlay@fond=format=auto:shortest=0:repeatlast=1[v0];"
            f"[v0][ca]overlay@ca=x={L['x_a']}:y={L['y_a']}:format=auto:shortest=0:repeatlast=1[v1];"
            f"[v1][cb]overlay@cb=x={L['x_b']}:y={L['y_b']}:format=auto:shortest=0:repeatlast=1,format=yuv420p[v];"
            f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume@mic=volume={self.vol:g}:eval=frame,azmq=bind_address={_zmq_esc(self.zmq_aport)}"
            + ("[a]" if not clean else ",asplit=2[a][aclean]")
        )
        # content layers re-read at {fps} fps: ffmpeg's queue (~15 frames) bounds the delay
        # between writing a PNG and its appearance at ~0.6 s (at 4 fps: ~4 s), see tr.DELAI_CHARGEMENT
        slot = ["-re", "-f", "image2", "-loop", "1", "-framerate", str(c.fps), "-i"]
        out_clean: list[str] = []
        if clean:
            vmap = (
                ["-map", "0:v:0", "-c:v:0", "copy"]
                if copy
                else [
                    "-map",
                    "[camclean]",
                    "-c:v:0",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-g",
                    str(c.fps * 2),
                    "-pix_fmt",
                    "yuv420p",
                ]
            )
            out_clean = vmap + [
                "-map",
                "[aclean]",
                "-c:a:0",
                "aac",
                "-b:a:0",
                "160k",
                "-ar",
                "48000",
                "-f",
                "matroska",
                clean,
            ]
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostats",
            "-progress",
            "pipe:1",
            *source.input_options(c.camera_url),
            "-i",
            c.camera_url,
            "-framerate",
            str(c.fps),
            "-i",
            self.layer_png["fond"],
            *slot,
            self.layer_png["a"],
            *slot,
            self.layer_png["b"],
            "-filter_complex",
            fg,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "superfast",
            "-tune",
            "zerolatency",
            "-b:v",
            c.video_bitrate,
            "-maxrate",
            c.video_bitrate,
            "-bufsize",
            "5000k",
            "-g",
            str(c.fps * 2),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-flags",
            "+global_header",
            "-f",
            "tee",
            f"{self._tee_targets()}|[f=matroska]{rec}",
        ] + out_clean

    def _tee_targets(self) -> str:
        """One tee output per destination: a platform that drops must not interrupt the others."""
        outs = [f"[f=flv:onfail=ignore]{url}" for url in self.cfg.rtmp_urls]
        if self.cfg.preview_url:
            # the monitoring copy leaves through local UDP (never blocks) towards a separate ffmpeg
            outs.append(f"[f=mpegts:onfail=ignore]{PREVIEW_UDP}?pkt_size=1316")
        return "|".join(outs)

    def _spawn_preview(self):
        """Separate ffmpeg: UDP -> RTMP of the receiver. If it stalls, the programme is not affected."""
        if not self.cfg.preview_url or (self.preview and self.preview.poll() is None):
            return
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats", "-fflags", "nobuffer",
            "-i", f"{PREVIEW_UDP}?fifo_size=2000000&overrun_nonfatal=1",
            "-c", "copy", "-f", "flv", self.cfg.preview_url,
        ]  # fmt: skip
        self.preview = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ life cycle
    def start(self):
        with self._lock:
            self.wanted = True
            self._spawn_preview()
            self._spawn()
        threading.Thread(target=self._watch, daemon=True, name="encoder-watch").start()

    def _spawn(self):
        rec, clean = self.new_recording()
        if self.failures == 0:  # retries after a failure stay silent (the first failure was logged)
            self.log(
                _(
                    "ENCODER: starting ffmpeg -> {targets}  | recording {recording}{clean}",
                    targets=" , ".join(self.cfg.rtmp_urls),
                    recording=rec,
                    clean=f"  | clean {clean}" if clean else "",
                )
            )
        if self._log_file:
            self._log_file.close()
        self._log_file = open(self.run_dir / "encoder.log", "ab")
        self.out_time = 0.0
        self._opened = False
        self._dropped = False
        self._spawned_at = time.monotonic()
        self.recordings.append((rec, clean))
        self.proc = subprocess.Popen(
            self.ffmpeg_cmd(rec, clean), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            encoding="utf-8", errors="replace",
        )  # fmt: skip
        threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._read_errors, args=(self.proc, self._log_file), daemon=True).start()

    def _read_errors(self, proc, log_file):
        """Copies ffmpeg's stderr to encoder.log and notes a destination dropped for good.

        The tee muxer is `onfail=ignore`: when a destination breaks (typically after the source changed: the
        output starves long enough for the platform to cut the connection) ffmpeg keeps recording and never
        reconnects it. `_watch` relaunches such a process (new recording, every destination reconnected)."""
        assert proc.stderr is not None
        for line in proc.stderr:
            try:
                log_file.write(line.encode())
                log_file.flush()
            except (OSError, ValueError):
                pass  # log file closed by a relaunch: keep draining
            if proc is self.proc and SLAVE_FAILED.search(line):
                self._dropped = True

    def _read_progress(self, proc):
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_us="):
                try:
                    self.out_time = int(line.split("=")[1]) / 1e6
                    if proc is self.proc and not self._opened:
                        self._opened = True  # ffmpeg is producing output: the source answered
                        if self.failures:
                            self.log(
                                _("ENCODER: source available again after {count} failed attempts", count=self.failures)
                            )
                            self.failures = 0
                except ValueError:
                    pass

    def _watch(self):
        while True:
            time.sleep(1.0)
            with self._lock:
                if not self.wanted:
                    return
                self._tick()

    def _tick(self):
        """One supervision pass (caller holds the lock)."""
        if self.preview and self.preview.poll() is not None:
            self.preview = None
            self._spawn_preview()
        if self.proc and self.proc.poll() is not None:
            self._on_exit()
        elif self.proc and self._dropped:
            self._relaunch_dropped()
        elif not self.proc and self.wanted and time.monotonic() >= self._next_spawn:
            self._spawn()

    def _on_exit(self):
        """ffmpeg died (caller holds the lock). If it never opened its source, drop its empty recordings, back
        off exponentially and log only the first failure; otherwise relaunch quickly. Logged once per outage."""
        code, (rec, clean) = self.proc.returncode, self.recordings[-1]
        self.proc = None
        self.restarts += 1
        if self._opened:
            self.log(
                _("ENCODER: ffmpeg stopped (code {code}), automatic restart #{count}", code=code, count=self.restarts)
            )
        else:
            self.failures += 1
            self._discard(rec, clean)
            if self.failures == 1:
                self.log(_("ENCODER: source unavailable (ffmpeg code {code}), retrying with backoff", code=code))
        self._next_spawn = time.monotonic() + (BACKOFF_MIN_S if self._opened else backoff_delay(self.failures))

    def _discard(self, *paths: str | None):
        """Remove the recordings of an attempt that never opened its source (empty or header-only files)."""
        for path in paths:
            if not path:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass
        if self.recordings and self.recordings[-1][0] in paths:
            self.recordings.pop()

    def stop(self):
        with self._lock:
            self.wanted = False
            p = self.proc
        if self.preview and self.preview.poll() is None:
            self.preview.terminate()
        self.preview = None
        if p and p.poll() is None:
            p.terminate()  # SIGTERM: ffmpeg finalises the MKV cleanly
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        self.log(_("ENCODER: stopped"))

    def _relaunch_dropped(self):
        """A destination was dropped by the tee muxer: kill ffmpeg so `_tick` relaunches it on every destination.
        A destination that breaks right at start-up (wrong key) is not retried in a loop: the grace applies."""
        if time.monotonic() - self._spawned_at >= STUCK_GRACE_S:
            self.log(_("ENCODER: ffmpeg alive but stuck ({why}), forced restart", why="destination dropped"))
            self._dropped = False
            self.proc.kill()

    def restart_if_stuck(self, why: str) -> bool:
        """ffmpeg is alive but its filter graph no longer answers (typically the source changed: OBS left, a
        Studio device took the relay path and the input read is wedged). Kills it: `_watch` relaunches it with the
        current state and a fresh connection to the source. A graph still starting up is left alone."""
        with self._lock:
            p = self.proc
            if not self.wanted or not p or p.poll() is not None or time.monotonic() - self._spawned_at < STUCK_GRACE_S:
                return False
        self.log(_("ENCODER: ffmpeg alive but stuck ({why}), forced restart", why=why))
        p.kill()
        return True

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def current_recording(self) -> str | None:
        """Recording currently being written (None = stream stopped)."""
        return self.recordings[-1][0] if self.wanted and self.recordings else None

    def position(self) -> tuple[int, float]:
        """(recording number, position in s within it). Stream stopped: the next recording, at 0 s."""
        if not self.wanted:
            return len(self.recordings), 0.0
        return len(self.recordings) - 1, self.out_time

    # -------------------------------------------------------------------- overlay
    def prepare(self, background) -> None:
        """Writes the shared background of the full screens and empty content layers (before starting)."""
        write_png_atomic(background, self.layer_png["fond"])
        empty = Image.new("RGBA", background.size, (0, 0, 0, 0))
        for k in ("a", "b"):
            write_png_atomic(empty, self.layer_png[k])

    def show(self, scene: str, full: bool, content) -> None:
        """Displays a scene: content (transparent layer) + background if full screen (scenes.SCENES).

        Same scene as on screen (e.g. the interlude countdown): the layer is updated in place.
        Other scene: transition animated by the "encoder-transitions" thread (the stream is never cut).
        Encoder not started yet: state set directly, without animation."""
        with self._mx:
            if self._pending and self._pending[0] == scene:
                self._pending = (scene, full, content)
                return
            if self.shown and self.shown[0] == scene:
                self._pending = None  # back to the displayed scene: nothing to animate
                write_png_atomic(content, self.layer_png[self.shown[1]])
                return
            if not self.wanted:
                write_png_atomic(content, self.layer_png[self.active])
                self.shown, self.full = (scene, self.active), full
                self.levels.update({"fond": 1.0 if full else 0.0, f"a_{self.active}": 1.0})
                return
            self._pending = (scene, full, content)
            self._ensure_worker()
            self._ev.set()

    def show_image(self, scene: str, full: bool, image, cle: str = "") -> None:
        """Displays a screen drawn elsewhere (the imported template) as one image.

        The transitions work on layers: an image replaces the whole visible surface, background and
        content together. It is therefore written as a new background, and the content layers are
        cleared — otherwise the engine's drawing would show through where the image is transparent.
        An identical image for the same scene is not rewritten: nothing changes, so nothing moves.
        """
        if self.shown and self.shown[0] == scene and len(self.shown) > 2 and self.shown[2] == cle:
            return
        with self._mx:
            write_png_atomic(image, self.layer_png["fond"])
            vide = Image.new("RGBA", image.size, (0, 0, 0, 0))
            for k in ("a", "b"):
                write_png_atomic(vide, self.layer_png[k])
            self.shown = (scene, self.active, cle)  # type: ignore[assignment]
            self.full = full
            self.levels.update({"fond": 1.0 if full else 0.0, f"a_{self.active}": 0.0})

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
                    # the inactive layer is transparent (end of the previous transition): we prepare the scene there
                    write_png_atomic(job[2], self.layer_png[dst])
                    self.shown = (job[0], dst)
            while job:
                time.sleep(tr.DELAI_CHARGEMENT)  # ffmpeg re-reads the PNG before we reveal it
                with self._mx:  # scene changed meanwhile (e.g. prayer -> janaza):
                    if not self._pending:  # we prepare the most recent one directly
                        break
                    job, self._pending = self._pending, None
                    write_png_atomic(job[2], self.layer_png[dst])
                    self.shown = (job[0], dst)
            target = 1.0 if self.mic_on else 0.0
            if target != self.vol:  # audio fade, same start as the image
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
                self.log(
                    _("TRANSITION -> {scene} (layer {layer}, {frames} frames)", scene=job[0], layer=dst, frames=frames)
                )

    # ZeroMQ channel (port) per animated parameter: one zmq filter per channel on the camera chain
    _TARGET = {
        "fond": "colorchannelmixer@fond aa",
        "a_a": "colorchannelmixer@ca aa",
        "a_b": "colorchannelmixer@cb aa",
        "x_a": "overlay@ca x",
        "y_a": "overlay@ca y",
        "x_b": "overlay@cb x",
        "y_b": "overlay@cb y",
    }

    def _animate(self, p: tr.Plan) -> int:
        """Plays the transition frame by frame, paced on the stream (and not on the wall clock).

        One REQ socket per channel (background, outgoing content, incoming content, position): the zmq filter (REP)
        handles at most one request per frame and only replies while handling it; the value of frame n+1 is sent
        only after the reply to frame n. The animation therefore advances one step per produced frame: no stutter
        even if ffmpeg processes frames in bursts (at worst it runs a few frames longer).
        Returns the number of frames played (0 if ffmpeg does not reply: the final state is set on restart)."""
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
                s.connect(f"tcp://127.0.0.1:{self.zmq_vports[ch]}")
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
                        self.log(_("TRANSITION: ffmpeg no longer answers, final state set for the restart"))
                        self.restart_if_stuck("transition")
                        break
            finally:
                for s in socks:
                    s.close()
        self.levels.update({k: round(v, 3) for k, v in final.items()})
        return played

    # -------------------------------------------------------------------- live audio
    def mic(self, on: bool):
        """Live microphone (khutba, janaza): fade of tr.AUDIO_DUR, synchronised with the pending transition."""
        with self._mx:
            self.mic_on = on
            if not self.wanted:
                self.vol = 1.0 if on else 0.0  # initial value of the graph at start-up
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
            s.connect(f"tcp://127.0.0.1:{self.zmq_aport}")
            try:
                s.send_string(msg)
                rep = s.recv_string()
                self.log(_("ZMQ “{message}” -> {reply}", message=msg, reply=rep.strip()))
                return
            except Exception as e:  # noqa
                self.log(
                    _("ZMQ attempt {attempt} failed ({message}): {error}", attempt=attempt + 1, message=msg, error=e)
                )
            finally:
                s.close()
            time.sleep(1.0)
        self.log(_("ZMQ “{message}” abandoned: no answer from the audio graph", message=msg))
        self.restart_if_stuck("audio")
