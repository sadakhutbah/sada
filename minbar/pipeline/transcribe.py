"""Transcription faster-whisper (GPU int8 si la VRAM le permet, sinon CPU int8) avec horodatage par segment."""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path

CUDA_LIBS = [str(Path.home() / ".local/share/raphael/cuda12/nvidia" / p / "lib") for p in ("cublas", "cudnn", "cuda_nvrtc")] + ["/usr/lib/wsl/lib"]
# Ordre de préférence. large-v3 (~3 Go en int8) n'est utilisé que si la VRAM libre le permet ET s'il est en cache/téléchargeable.
MODELS = [("large-v3", 3400), ("large-v3-turbo", 2000), ("medium", 1800), ("small", 900)]


def setup_env():
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    want = [p for p in CUDA_LIBS if p not in cur]
    if want:
        os.environ["LD_LIBRARY_PATH"] = ":".join(want + ([cur] if cur else ""))
        # ctranslate2 charge les libs à l'import : il faut ré-exécuter si le chemin vient d'être ajouté
        return True
    return False


def free_vram_mb() -> int | None:
    for exe in ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi"):
        try:
            out = subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                return int(out.stdout.strip().splitlines()[0])
        except Exception:
            continue
    return None


def extract_audio(src: str, dst: str, start: float = 0.0, end: float | None = None) -> str:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}"]
    if end is not None:
        cmd += ["-t", f"{end - start:.2f}"]
    cmd += ["-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst]
    subprocess.run(cmd, check=True)
    return dst


def pick_model(prefer: str | None, log=print) -> tuple[str, str, str]:
    """-> (modèle, device, compute_type)."""
    free = free_vram_mb()
    log(f"VRAM libre : {free} Mo")
    if prefer == "cpu":
        return ("medium", "cpu", "int8")
    cands = [m for m in MODELS if prefer in (None, m[0])] or MODELS
    for name, need in cands:
        if free is not None and free >= need + 300:
            return (name, "cuda", "int8")
    # pas assez de VRAM (GPU partagé) : CPU int8 avec le meilleur modèle raisonnable
    return ("large-v3-turbo" if prefer in (None, "large-v3-turbo", "large-v3") else prefer, "cpu", "int8")


def transcribe(wav: str, out_json: str, prefer: str | None = None, log=print, language: str | None = None) -> dict:
    from faster_whisper import WhisperModel
    name, device, ctype = pick_model(prefer, log)
    log(f"Whisper : {name} sur {device} ({ctype})")
    t0 = time.time()
    try:
        model = WhisperModel(name, device=device, compute_type=ctype, cpu_threads=8 if device == "cpu" else 4)
    except Exception as e:  # noqa
        log(f"Échec chargement {name}/{device} : {e} -> repli CPU int8")
        name, device, ctype = ("large-v3-turbo" if name.startswith("large") else name), "cpu", "int8"
        model = WhisperModel(name, device=device, compute_type=ctype, cpu_threads=8)
    import wave
    import numpy as np
    with wave.open(wav, "rb") as w:  # on décode nous-mêmes le WAV 16 kHz (évite PyAV, incompatible ici)
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segs_iter, info = model.transcribe(
        audio, language=language, multilingual=language is None, vad_filter=True,
        beam_size=5, condition_on_previous_text=False, temperature=0.0,
    )
    segs = []
    for s in segs_iter:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(), "avg_logprob": round(s.avg_logprob, 3)})
        if len(segs) % 20 == 0:
            log(f"  … {len(segs)} segments, {s.end:.0f}/{info.duration:.0f}s")
    res = {"model": name, "device": device, "compute_type": ctype, "language": info.language,
           "language_probability": round(info.language_probability, 3), "duration": info.duration,
           "elapsed_s": round(time.time() - t0, 1), "segments": segs}
    Path(out_json).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(f"Transcription : {len(segs)} segments en {res['elapsed_s']} s")
    return res
