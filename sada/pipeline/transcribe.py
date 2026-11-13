"""faster-whisper transcription (int8 GPU when VRAM allows, int8 CPU otherwise) with per-segment timestamps."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from ..i18n import _
from . import glossary, repair, settings
from .language import measure as measure_language

CUDA_LIBS = [
    str(Path.home() / ".local/share/raphael/cuda12/nvidia" / p / "lib") for p in ("cublas", "cudnn", "cuda_nvrtc")
] + ["/usr/lib/wsl/lib"]
# Order of preference. large-v3 (~3 GB in int8) is only used if free VRAM allows it AND it is cached/downloadable.
MODELS = [("large-v3", 3400), ("large-v3-turbo", 2000), ("medium", 1800), ("small", 900)]

# Les passes de reprise (boucles, arabe translittéré) vivent dans `repair`, partagées avec le
# distant : la politique est la même, seul le moteur qui retranscrit une plage change.
SUSPECT_LOGPROB = repair.SUSPECT_LOGPROB
DETECT_MIN_PROB = repair.DETECT_MIN_PROB
RETRY_MARGIN = repair.RETRY_MARGIN
TRANSLIT_SUSPECT = repair.TRANSLIT_SUSPECT
TRANSLIT_SURE = repair.TRANSLIT_SURE
RETRY_MAX = repair.RETRY_MAX


def setup_env():
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    want = [p for p in CUDA_LIBS if p not in cur]
    if want:
        os.environ["LD_LIBRARY_PATH"] = ":".join(want + ([cur] if cur else []))
        # ctranslate2 loads the libs at import time: we must re-exec if the path was just added
        return True
    return False


def free_vram_mb() -> int | None:
    for exe in ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi"):
        try:
            out = subprocess.run(
                [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            )
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
    """-> (model, device, compute_type)."""
    free = free_vram_mb()
    log(_("Free VRAM: {mb} MB", mb=free))
    if prefer == "cpu":
        return ("medium", "cpu", "int8")
    cands = [m for m in MODELS if prefer in (None, m[0])] or MODELS
    for name, need in cands:
        if name == "small" and prefer != "small":
            break  # small rate l'arabe et boucle : mieux vaut large-v3-turbo sur CPU, plus lent
        if free is not None and free >= need + 300:
            return (name, "cuda", "int8")
    # not enough VRAM (shared GPU): int8 CPU with the best reasonable model
    return ("large-v3-turbo" if prefer in (None, "large-v3-turbo", "large-v3") else prefer, "cpu", "int8")


class LocalEngine:
    """Moteur local des passes de reprise : faster-whisper sur le tableau numpy du WAV 16 kHz."""

    def __init__(self, model, audio, *, sr: int = 16000):
        self.model, self.audio, self.sr = model, audio, sr

    def chunk(self, start: float, end: float):
        piece = self.audio[int(start * self.sr) : int(end * self.sr)]
        return piece if len(piece) >= self.sr * 0.5 else None

    def transcribe(
        self,
        chunk,
        *,
        language: str | None = None,
        temperatures: tuple[float, ...] = (0.0,),
        compression_ratio_threshold: float | None = None,
    ) -> list[dict]:
        options: dict = {
            "beam_size": 5,
            "condition_on_previous_text": False,
            "temperature": temperatures,
            "word_timestamps": True,
            "vad_filter": False,
        }
        if compression_ratio_threshold is not None:
            options["compression_ratio_threshold"] = compression_ratio_threshold
        if language:
            options["language"] = language
        pieces, _info = self.model.transcribe(chunk, **options)
        return [
            {
                "text": piece.text or "",
                "avg_logprob": piece.avg_logprob,
                "start": piece.start,
                "end": piece.end,
                "words": [
                    {"w": word.word.strip(), "s": word.start, "e": word.end}
                    for word in (piece.words or [])
                    if (word.word or "").strip()
                ],
            }
            for piece in pieces
        ]

    def detect_language(self, chunk):
        """("ar", 0.9) ou None : une détection qui échoue ne doit pas perdre la transcription."""
        try:
            code, prob = self.model.detect_language(chunk)[:2]
        except Exception:  # noqa: BLE001
            return None
        return (code, prob)

    def silent(self, start: float, end: float) -> bool | None:
        """Inconnu : la transcription locale passe déjà par un VAD, le silence n'y fait pas de segment.

        Mesurer ici ne dirait rien de plus, et une plage jugée muette à tort priverait d'une reprise
        une citation dite à voix basse.
        """
        return None


def redo_loops(model, audio, segs: list[dict], *, sr: int = 16000, log=print, max_segments=None) -> int:
    """Boucles de répétition reprises sans langue imposée (voir `repair.redo_loops`)."""
    return repair.redo_loops(LocalEngine(model, audio, sr=sr), segs, log=log, max_segments=max_segments)


def recover_arabic(
    model,
    audio,
    segs: list[dict],
    *,
    sr: int = 16000,
    log=print,
    max_segments: int = repair.RETRY_MAX,
    min_prob: float = repair.DETECT_MIN_PROB,
    margin: float = repair.RETRY_MARGIN,
    language: str = "",
) -> int:
    """Passages arabes repris en arabe forcé (voir `repair.recover_arabic`).

    `max_segments` borne le nombre de plages envoyées au moteur (une plage peut couvrir plusieurs
    segments voisins).
    """
    return repair.recover_arabic(
        LocalEngine(model, audio, sr=sr),
        segs,
        log=log,
        max_ranges=max_segments,
        min_prob=min_prob,
        margin=margin,
        language=language,
    )


def transcribe(
    wav: str,
    out_json: str,
    prefer: str | None = None,
    log=print,
    arabic_retry: bool | None = None,
    hotwords: str | None = None,
    source: dict | None = None,
) -> dict:
    # Réglage « Local / Distant » (Réglages → Transcription) : le distant rend exactement la même
    # forme de résultat, l'aval ne sait pas d'où il vient. Aucun repli silencieux vers le local.
    from . import remote

    if remote.enabled():
        return remote.transcribe(wav, out_json, log=log, arabic_retry=arabic_retry, hotwords=hotwords, source=source)

    from faster_whisper import WhisperModel

    name, device, ctype = pick_model(prefer, log)
    log(_("Whisper: {model} on {device} ({compute_type})", model=name, device=device, compute_type=ctype))
    t0 = time.time()
    try:
        model = WhisperModel(name, device=device, compute_type=ctype, cpu_threads=8 if device == "cpu" else 4)
    except Exception as e:  # noqa
        log(_("Loading {model}/{device} failed: {error} -> fallback to CPU int8", model=name, device=device, error=e))
        name, device, ctype = ("large-v3-turbo" if name.startswith("large") else name), "cpu", "int8"
        model = WhisperModel(name, device=device, compute_type=ctype, cpu_threads=8)
    import wave

    import numpy as np

    with wave.open(wav, "rb") as w:  # we decode the 16 kHz WAV ourselves (avoids PyAV, which is incompatible here)
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    # Glossaire des termes islamiques : donné en `hotwords`, il stabilise l'orthographe des
    # translittérations (tawba plutôt que « taouba » ou un mot français approchant).
    hw = glossary.hotwords() if hotwords is None else hotwords
    segs_iter, info = model.transcribe(
        audio,
        multilingual=True,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
        # repli en température : une boucle de répétition (« إذا كنت مرحباً » x30) dépasse le seuil de
        # compression et le segment est retenté au lieu d'être gardé tel quel
        temperature=(0.0, 0.2, 0.4),
        compression_ratio_threshold=2.4,
        word_timestamps=True,  # timestamped words: the active word is highlighted in the clip subtitles
        hotwords=hw or None,
    )
    segs = []
    for s in segs_iter:
        seg = {
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "avg_logprob": round(s.avg_logprob, 3),
        }
        if s.words:
            seg["words"] = [
                {"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words if w.word.strip()
            ]
        segs.append(seg)
        if len(segs) % 20 == 0:
            log(f"  … {len(segs)} segments, {s.end:.0f}/{info.duration:.0f}s")
    # Même filtre qu'à distance : un générique de sous-titres recopié sur de la musique n'est pas
    # la khutba (le VAD écarte déjà le silence, d'où l'absence de mesure de niveau ici).
    segs = repair.without_hallucinations(segs, log=log)
    # La langue est mesurée sur tous les segments, comme en distant : `info.language` ne regarde
    # que les 30 premières secondes (une khutba ouverte par la Fatiha passerait pour de l'arabe).
    lang, prob = measure_language(segs, info.language)
    res = {
        "model": name,
        "device": device,
        "compute_type": ctype,
        "language": lang,
        "language_probability": prob,
        "duration": info.duration,
        "elapsed_s": round(time.time() - t0, 1),
        "hotwords": bool(hw),
        "segments": segs,
    }
    if source:
        res["source"] = source
    # Deux passes de reprise, les mêmes qu'à distance : boucles de répétition, puis passages arabes
    # que Whisper a rendus en français incohérent ou en translittération.
    retry = settings.arabic_retry_enabled() if arabic_retry is None else bool(arabic_retry)
    if retry:
        engine = LocalEngine(model, audio)
        res["loops_redone"] = repair.redo_loops(engine, segs, log=log, max_segments=repair.LOOP_RETRY_MAX)
        res["arabic_recovered"] = repair.recover_arabic(
            engine, segs, log=log, max_ranges=RETRY_MAX, min_prob=DETECT_MIN_PROB, margin=RETRY_MARGIN, language=lang
        )

    Path(out_json).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(_("Transcription: {count} segments in {seconds} s", count=len(segs), seconds=res["elapsed_s"]))
    return res
