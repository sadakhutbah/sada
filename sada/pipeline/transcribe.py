"""faster-whisper transcription (int8 GPU when VRAM allows, int8 CPU otherwise) with per-segment timestamps."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from ..i18n import _
from ..text import arabic_ratio, is_translit_word
from . import glossary, settings

CUDA_LIBS = [
    str(Path.home() / ".local/share/raphael/cuda12/nvidia" / p / "lib") for p in ("cublas", "cudnn", "cuda_nvrtc")
] + ["/usr/lib/wsl/lib"]
# Order of preference. large-v3 (~3 GB in int8) is only used if free VRAM allows it AND it is cached/downloadable.
MODELS = [("large-v3", 3400), ("large-v3-turbo", 2000), ("medium", 1800), ("small", 900)]

# --- passages arabes dans un discours français ---------------------------------
SUSPECT_LOGPROB = -0.75  # en dessous : Whisper doute, souvent une citation arabe rendue en français
DETECT_MIN_PROB = 0.55  # probabilité minimale pour croire detect_language quand il dit « ar »
RETRY_MARGIN = 0.05  # l'arabe doit être plus probable que le français de ce petit écart
TRANSLIT_SUSPECT = 0.35  # part des mots en arabe translittéré : passage arabe à retranscrire
TRANSLIT_SURE = 0.5  # au-delà, le passage est arabe sans autre vérification
RETRY_MAX = 150  # plafond de segments retranscrits par session (coût CPU borné)


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


def dominant_language(segs: list[dict], detected: str) -> str:
    """Langue du discours sur toute la durée. `info.language` ne regarde que les 30 premières
    secondes : un cours en français qui s'ouvre sur une récitation passe pour de l'arabe."""
    arabic = re.compile(r"[\u0600-\u06FF]")
    ar = sum(max(0.0, s["end"] - s["start"]) for s in segs if arabic.search(s.get("text") or ""))
    total = sum(max(0.0, s["end"] - s["start"]) for s in segs)
    if not total:
        return detected
    return "ar" if ar / total >= 0.5 else ("fr" if detected == "ar" else detected)


def _text_of(pieces) -> str:
    return " ".join((p.text or "").strip() for p in pieces if (p.text or "").strip())


def _logprob_of(pieces) -> float:
    """Moyenne pondérée par la durée : une longue phrase arabe pèse plus qu'un mot isolé."""
    total = sum(max(0.01, p.end - p.start) for p in pieces)
    if not total:
        return -9.0
    return sum(p.avg_logprob * max(0.01, p.end - p.start) for p in pieces) / total


def translit_ratio(text: str) -> float:
    """Share of the words that look like Arabic transliterated in Latin letters."""
    words = [w for w in text.split() if w.strip('.,;:!?«»"()')]
    if not words:
        return 0.0
    return sum(1 for w in words if is_translit_word(w)) / len(words)


def is_loop(text: str) -> bool:
    """Boucle de répétition de Whisper (« al-kufr, al-kufr, al-kufr… ») : un mot recopié sur tout le segment."""
    words = [w.strip('.,;:!?«»"()').lower() for w in text.split()]
    words = [w for w in words if w]
    return len(words) >= 5 and len(set(words)) / len(words) <= 0.3


def redo_loops(model, audio, segs: list[dict], *, sr: int = 16000, log=print) -> int:
    """Retranscrit sans langue imposée les segments réduits à une boucle de répétition.

    Ces boucles recouvrent souvent de la parole française : les forcer en arabe fait traduire le
    français en arabe. Le segment est remplacé seulement si la nouvelle version n'est plus une boucle.
    """
    done = 0
    for seg in segs:
        if not is_loop(seg.get("text") or ""):
            continue
        chunk = audio[int(seg["start"] * sr) : int(seg["end"] * sr)]
        if len(chunk) < sr * 0.5:
            continue
        try:
            it, _info = model.transcribe(
                chunk,
                beam_size=5,
                condition_on_previous_text=False,
                temperature=(0.2, 0.4, 0.6),
                compression_ratio_threshold=2.0,
                word_timestamps=True,
                vad_filter=False,
            )
            pieces = list(it)
        except Exception as e:  # noqa: BLE001
            log(_("  retry impossible at {seconds:.1f}s: {error}", seconds=seg["start"], error=e))
            continue
        text = _text_of(pieces)
        if not text or is_loop(text):
            continue
        words = [
            {"w": w.word.strip(), "s": round(seg["start"] + w.start, 2), "e": round(seg["start"] + w.end, 2)}
            for p in pieces
            for w in (p.words or [])
            if w.word.strip()
        ]
        seg.update(text=text, avg_logprob=round(_logprob_of(pieces), 3), recovered="loop")
        if words:
            seg["words"] = words
        done += 1
        log(_("  loop recovered at {seconds:.1f}s: {heard}", seconds=seg["start"], heard=text[:60]))
    return done


def looks_suspect(seg: dict) -> bool:
    """Segment French on paper but probably a mis-transcribed Arabic passage.

    Multilingual Whisper decides the language of each 30 s window: a Quran quotation inside a
    French khutba is written as incoherent French (« ce qu'on appelle aran »). Three cheap signals:
    already-Arabic text (nothing to do), a low average log-probability, or a long stretch with very
    few words. Only the segments that pass this filter pay for a language detection.
    """
    text = (seg.get("text") or "").strip()
    if not text or arabic_ratio(text) > 0.3 or is_loop(text):
        return False
    if (seg.get("end", 0.0) - seg.get("start", 0.0)) < 0.6:
        return False
    if len(text.split()) >= 3 and translit_ratio(text) >= TRANSLIT_SUSPECT:
        return True
    lp = seg.get("avg_logprob")
    if lp is None or lp < SUSPECT_LOGPROB:
        return True
    words = text.split()
    return (seg["end"] - seg["start"]) >= 3.0 and len(words) <= 4


def recover_arabic(
    model,
    audio,
    segs: list[dict],
    *,
    sr: int = 16000,
    log=print,
    max_segments: int = 40,
    min_prob: float = 0.55,
    margin: float = 0.05,
) -> int:
    """Retranscrit en arabe les segments que Whisper a rendus en français incohérent.

    Le coût est borné : seuls les segments suspects (`looks_suspect`, plafonnés à `max_segments`)
    passent par `detect_language`, et seuls ceux que le modèle reconnaît comme arabes sont
    retranscrits. Un segment n'est remplacé que si l'arabe est plus probable que le français
    (`avg_logprob` supérieur de `margin`) et si le texte obtenu est bien arabe. Les bornes du
    segment sont conservées (le montage ne bouge pas) ; les mots horodatés sont ramenés au temps
    de la session. Renvoie le nombre de segments remplacés.
    """
    replaced, tried = 0, 0
    for seg in segs:
        if tried >= max_segments:
            break
        if not looks_suspect(seg):
            continue
        a, b = int(seg["start"] * sr), int(seg["end"] * sr)
        chunk = audio[a:b]
        if len(chunk) < sr * 0.5:
            continue
        tried += 1
        # Arabe déjà écrit en lettres latines : la langue est connue, detect_language (qui se trompe
        # sur ces passages mêlés) et l'écart de probabilité ne sont plus des garde-fous utiles.
        sure = translit_ratio(seg.get("text") or "") >= TRANSLIT_SURE
        if not sure:
            try:
                lang, prob = model.detect_language(chunk)[:2]
            except Exception as e:  # noqa: BLE001 — une détection qui échoue ne doit pas perdre la transcription
                log(_("  language detection impossible at {seconds:.1f}s: {error}", seconds=seg["start"], error=e))
                continue
            if lang != "ar" or prob < min_prob:
                continue
        try:
            it, _info = model.transcribe(
                chunk,
                language="ar",
                beam_size=5,
                condition_on_previous_text=False,
                temperature=0.0,
                word_timestamps=True,
                vad_filter=False,
            )
            pieces = list(it)
        except Exception as e:  # noqa: BLE001
            log(_("  Arabic retranscription impossible at {seconds:.1f}s: {error}", seconds=seg["start"], error=e))
            continue
        text = _text_of(pieces)
        if arabic_ratio(text) < 0.5:
            continue
        lp = _logprob_of(pieces)
        if not sure and lp <= float(seg.get("avg_logprob") or -9.0) + margin:
            continue
        words = [
            {"w": w.word.strip(), "s": round(seg["start"] + w.start, 2), "e": round(seg["start"] + w.end, 2)}
            for p in pieces
            for w in (p.words or [])
            if w.word.strip()
        ]
        seg.update(text=text, avg_logprob=round(lp, 3), recovered="ar")
        if words:
            seg["words"] = words
        replaced += 1
        log(_("  Arabic passage recovered at {seconds:.1f}s: {heard}", seconds=seg["start"], heard=text[:60]))
    if replaced:
        log(
            _(
                "Arabic passages retranscribed: {replaced} (out of {tried} segments examined)",
                replaced=replaced,
                tried=tried,
            )
        )
    return replaced


def transcribe(
    wav: str,
    out_json: str,
    prefer: str | None = None,
    log=print,
    arabic_retry: bool | None = None,
    hotwords: str | None = None,
) -> dict:
    # Réglage « Local / Distant » (Réglages → Transcription) : le distant rend exactement la même
    # forme de résultat, l'aval ne sait pas d'où il vient. Aucun repli silencieux vers le local.
    from . import remote

    if remote.enabled():
        return remote.transcribe(wav, out_json, log=log, arabic_retry=arabic_retry, hotwords=hotwords)
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
    res = {
        "model": name,
        "device": device,
        "compute_type": ctype,
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": info.duration,
        "elapsed_s": round(time.time() - t0, 1),
        "hotwords": bool(hw),
        "segments": segs,
    }
    # Une khutba en français peut citer le Coran : dans ce cas seulement, les passages que Whisper
    # a rendus en français incohérent sont retentés en arabe forcé.
    retry = settings.arabic_retry_enabled() if arabic_retry is None else bool(arabic_retry)
    if retry:
        res["loops_redone"] = redo_loops(model, audio, segs, log=log)
    if retry and dominant_language(segs, info.language) != "ar":
        res["arabic_recovered"] = recover_arabic(
            model, audio, segs, log=log, max_segments=RETRY_MAX, min_prob=DETECT_MIN_PROB, margin=RETRY_MARGIN
        )
    Path(out_json).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(_("Transcription: {count} segments in {seconds} s", count=len(segs), seconds=res["elapsed_s"]))
    return res
