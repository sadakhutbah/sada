"""Transcription distante : API compatible OpenAI `/audio/transcriptions`.

Pour les mosquées sans carte graphique : l'audio part chez un fournisseur (Groq, OpenAI, ou toute
adresse compatible), qui rend des segments et des mots horodatés. Le résultat a **la même forme**
que celui de Whisper local (`transcribe.transcribe`) : `model`, `device`, `compute_type`,
`language`, `language_probability`, `duration`, `elapsed_s`, `hotwords`, `segments` (avec `words`),
et `arabic_recovered` quand une reprise a eu lieu (`loops_redone` reste le fait du moteur local).
Tout l'aval le lit sans savoir d'où il vient.

Les enregistrements longs sont découpés (ffmpeg -> mp3 mono 16 kHz) en morceaux sous la limite de
taille de l'API, avec un léger chevauchement ; segments et mots sont recollés avec des horodatages
décalés, et le chevauchement est dédoublonné.

Réglage : `data/transcription.json` (ou `SADA_TRANSCRIPTION_CONFIG`), écrit par le back-office,
sur le modèle de `data/llm.json` : `{"mode", "provider", "url", "model", "key"}`. La priorité reste
celle du reste du projet : environnement, puis fichier, puis défaut.

    SADA_TRANSCRIPTION_MODE    local | distant            (défaut : local)
    SADA_TRANSCRIPTION_PROVIDER groq | openai | custom     (défaut : groq)
    SADA_TRANSCRIPTION_URL     racine de l'API compatible  (défaut : celle du fournisseur)
    SADA_TRANSCRIPTION_MODEL   nom du modèle               (défaut : celui du fournisseur)
    SADA_TRANSCRIPTION_KEY     clé d'API                   (défaut : vide)
    SADA_TRANSCRIPTION_TIMEOUT délai d'un envoi, en secondes (défaut : 600)

Aucun repli silencieux vers le local : une erreur du fournisseur remonte telle quelle dans le
journal du traitement, et la session repart en échec.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx

from .. import paths
from ..i18n import _
from ..text import arabic_ratio
from . import glossary
from .settings import arabic_retry_enabled

CONFIG_KEYS = ("mode", "provider", "url", "model", "key")
MODES = ("local", "distant")
CUSTOM = "custom"

# Envoi : mp3 mono 16 kHz à 48 kbit/s, morceaux nettement sous les 25 Mo des API (Groq, OpenAI).
BYTES_PER_SECOND = 6000
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
MAX_CHUNK_S = 1500.0  # 25 min : un morceau reste court à téléverser et à transcrire
OVERLAP_S = 2.0  # léger chevauchement : aucun mot perdu à la coupure
PROMPT_MAX = 900  # le glossaire part en `prompt` : les API le tronquent au-delà

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
BACKOFF_S = 2.0
MAX_BACKOFF_S = 30.0
EPS = 0.05

DEFAULT_TIMEOUT_S = 600.0


class TranscriptionError(RuntimeError):
    """Échec de la transcription distante : le message est montré tel quel dans le journal."""


@dataclass(frozen=True)
class Provider:
    label: str
    url: str
    model: str
    price_per_hour: float | None


# Tarifs publics indicatifs, par heure d'audio (au moment de l'écriture).
PROVIDERS = {
    "groq": Provider("Groq", "https://api.groq.com/openai/v1", "whisper-large-v3", 0.111),
    "openai": Provider("OpenAI", "https://api.openai.com/v1", "whisper-1", 0.36),
}
DEFAULT_PROVIDER = "groq"


# --------------------------------------------------------------------------- réglage


def config_path() -> Path:
    return Path(os.environ.get("SADA_TRANSCRIPTION_CONFIG") or paths.DATA / "transcription.json")


def config() -> dict:
    """Réglage enregistré depuis le back-office. Fichier absent ou illisible : {}."""
    try:
        d = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: d[k] for k in CONFIG_KEYS if k in d} if isinstance(d, dict) else {}


@dataclass(frozen=True)
class Settings:
    mode: str
    provider: str
    url: str
    model: str
    key: str

    @property
    def distant(self) -> bool:
        return self.mode == "distant"

    def label(self) -> str:
        """Nom court de la méthode (journal du pipeline, écran de réglages)."""
        return f"{PROVIDERS[self.provider].label} · {self.model}" if self.provider in PROVIDERS else self.model


def _text(value, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def settings() -> Settings:
    """Réglage effectif : environnement, puis fichier, puis le défaut du fournisseur."""
    cfg = config()
    provider = (
        _text(os.environ.get("SADA_TRANSCRIPTION_PROVIDER")) or _text(cfg.get("provider")) or DEFAULT_PROVIDER
    ).lower()
    if provider not in PROVIDERS and provider != CUSTOM:
        provider = DEFAULT_PROVIDER
    preset = PROVIDERS.get(provider)
    mode = (_text(os.environ.get("SADA_TRANSCRIPTION_MODE")) or _text(cfg.get("mode")) or "local").lower()
    url = _text(os.environ.get("SADA_TRANSCRIPTION_URL")) or _text(cfg.get("url")) or (preset.url if preset else "")
    model = (
        _text(os.environ.get("SADA_TRANSCRIPTION_MODEL")) or _text(cfg.get("model")) or (preset.model if preset else "")
    )
    key = _text(os.environ.get("SADA_TRANSCRIPTION_KEY")) or _text(cfg.get("key"))
    return Settings(
        mode=mode if mode in MODES else "local", provider=provider, url=url.rstrip("/"), model=model, key=key
    )


def enabled() -> bool:
    """Le pipeline transcrit-il à distance ? (`transcribe.transcribe` aiguille sur ce booléen.)"""
    return settings().distant


def save_config(*, mode: str, provider: str, url: str = "", model: str = "", key: str | None = None) -> dict:
    """Écrit le réglage et rend la vue pour l'écran. Les autres clés du fichier sont conservées.

    `key=None` (ou vide) garde la clé déjà enregistrée : le back-office ne la reçoit jamais en
    clair, il renvoie le masque tel quel. Une clé fournie remplace l'ancienne.
    """
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        **config(),
        "mode": mode if mode in MODES else "local",
        "provider": provider,
        "url": url.strip(),
        "model": model.strip(),
    }
    nouvelle = (key or "").strip()
    if nouvelle:
        data["key"] = nouvelle
    elif "key" not in data:
        data["key"] = ""
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: data[k] for k in CONFIG_KEYS if k in data}, indent=1), encoding="utf-8")
    tmp.chmod(0o600)  # la clé y est : lisible par le service seul
    os.replace(tmp, p)
    return view()


def view() -> dict:
    """Ce que l'écran Réglages montre : jamais la clé, seulement « définie / non définie »."""
    s = settings()
    preset = PROVIDERS.get(s.provider)
    return {
        "mode": s.mode,
        "provider": s.provider,
        "url": s.url if (s.provider == CUSTOM or s.url != (preset.url if preset else "")) else "",
        "model": s.model,
        "has_key": bool(s.key),
        "label": s.label(),
        "price_per_hour": preset.price_per_hour if preset else None,
        "providers": [
            {"key": k, "label": p.label, "url": p.url, "model": p.model, "price_per_hour": p.price_per_hour}
            for k, p in PROVIDERS.items()
        ],
    }


# --------------------------------------------------------------------------- découpage / recollage


def chunk_plan(duration: float, max_bytes: int = MAX_UPLOAD_BYTES) -> list[tuple[float, float]]:
    """(début, longueur) des morceaux : sous la limite de taille, avec chevauchement, sans trou.

    La durée d'un morceau est bornée par la limite d'envoi (débit fixe du mp3) et par MAX_CHUNK_S.
    """
    if duration <= 0:
        return [(0.0, 0.0)]
    piece = min(MAX_CHUNK_S, float(max_bytes) / BYTES_PER_SECOND)
    if piece <= 2 * OVERLAP_S:  # limite ridiculement basse : un seul morceau plutôt qu'aucun
        return [(0.0, duration)]
    step = piece - OVERLAP_S
    plan = []
    start = 0.0
    while start < duration:
        plan.append((round(start, 2), round(min(piece, duration - start), 2)))
        if start + piece >= duration:
            break
        start += step
    return plan


def wav_duration(path: str) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, wave.Error, EOFError):
        return 0.0


def cut(src: str, dst: Path, start: float, length: float) -> Path:
    """Extrait [start, start+length] du WAV en mp3 mono 16 kHz (petit et accepté partout)."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, start):.2f}",
        "-t",
        f"{max(0.05, length):.2f}",
        "-i",
        src,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "48k",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise TranscriptionError(_("Cannot cut the audio: {error}", error=detail.strip()[-200:])) from e
    return dst


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _word(item) -> dict | None:
    """Mot de l'API, ramené à la forme locale `{"w", "s", "e"}`."""
    text = _text(item.get("word") if "word" in item else item.get("w"))
    if not text:
        return None
    return {"w": text, "s": _num(item.get("start", item.get("s"))), "e": _num(item.get("end", item.get("e")))}


def _segment_words(raw: dict, top_words: list, offset: float) -> list[dict]:
    """Mots du segment, décalés au temps de la session.

    Les fournisseurs placent les mots soit dans le segment, soit à la racine de la réponse : on
    prend l'un ou l'autre, sans jamais inventer d'horodatage.
    """
    inside = raw.get("words") or []
    if not inside and top_words:
        a, b = _num(raw.get("start")), _num(raw.get("end"))
        inside = [w for w in top_words if a - EPS <= _num(w.get("start")) < b + EPS]
    out = []
    for item in inside:
        w = _word(item)
        if w is not None:
            w["s"] = round(w["s"] + offset, 2)
            w["e"] = round(w["e"] + offset, 2)
            out.append(w)
    return out


def merge(payloads: list[tuple[float, dict]]) -> list[dict]:
    """Recolle les réponses des morceaux : horodatages décalés, chevauchement dédoublonné.

    Un segment déjà couvert par le morceau précédent est écarté ; un segment à cheval sur la
    coupure est ramené à ses mots non encore transcrits (texte reconstruit sur ces mots), pour
    qu'aucun mot ne soit dit deux fois dans les sous-titres.
    """
    out: list[dict] = []
    prev_end = 0.0
    for offset, payload in payloads:
        top_words = payload.get("words") or []
        for raw in payload.get("segments") or []:
            start = offset + _num(raw.get("start"))
            end = offset + _num(raw.get("end"))
            text = _text(raw.get("text"))
            words = _segment_words(raw, top_words, offset)
            if end <= prev_end + EPS:  # entièrement dans le chevauchement déjà transcrit
                continue
            if start < prev_end - EPS:
                kept = [w for w in words if w["e"] > prev_end + EPS]
                if kept and out:
                    # Un mot que le morceau précédent vient de dire n'est pas redit : les deux
                    # transcriptions n'accordent pas leurs horodatages à la coupure.
                    kept = _sans_redite(kept, out[-1].get("words") or [], prev_end + 0.6)
                if words and not kept:
                    continue  # tout ce segment était déjà transcrit par le morceau précédent
                if kept:
                    words = kept
                    text = " ".join(w["w"] for w in words)
                    start = words[0]["s"]
                else:
                    start = prev_end
                    if out and text and _same(text, out[-1]["text"]):
                        continue  # même phrase redite à la coupure, sans mots pour la trancher
            if not text and not words:
                continue
            if end - start <= 0:
                continue
            seg = {
                "start": round(start, 2),
                "end": round(end, 2),
                "text": text,
                "avg_logprob": _logprob_of(raw),
            }
            if words:
                seg["words"] = words
            out.append(seg)
            prev_end = max(prev_end, end)
    return out


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def _sans_redite(words: list[dict], previous: list[dict], limit: float) -> list[dict]:
    """Ôte la tête de `words` qui répète la queue de `previous` (mots déjà dits avant la coupure)."""
    n = 0
    while (
        n < len(words) and n < len(previous) and words[n]["s"] <= limit and _same(words[n]["w"], previous[-1 - n]["w"])
    ):
        n += 1
    return words[n:]


def _logprob_of(raw: dict) -> float | None:
    lp = raw.get("avg_logprob")
    return round(_num(lp), 3) if lp is not None else None


def _payload_text(payload: dict) -> str:
    segments = payload.get("segments") or []
    if segments:
        return " ".join(_text(s.get("text")) for s in segments if _text(s.get("text")))
    return _text(payload.get("text"))


def _payload_logprob(payload: dict) -> float:
    """Moyenne pondérée par la durée, comme la transcription locale."""
    total = 0.0
    weight = 0.0
    for raw in payload.get("segments") or []:
        d = max(0.01, _num(raw.get("end")) - _num(raw.get("start")))
        lp = raw.get("avg_logprob")
        if lp is None:
            continue
        total += _num(lp) * d
        weight += d
    return total / weight if weight else -9.0


# --------------------------------------------------------------------------- appel HTTP


def _timeout() -> float:
    try:
        return max(10.0, float(os.environ.get("SADA_TRANSCRIPTION_TIMEOUT") or DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S


def _detail(response: httpx.Response) -> str:
    try:
        d = response.json()
    except ValueError:
        return (response.text or "")[:200]
    if isinstance(d, dict):
        err = d.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:200]
        if isinstance(err, str):
            return err[:200]
        if d.get("message"):
            return str(d["message"])[:200]
    return json.dumps(d)[:200]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return min(MAX_BACKOFF_S, max(0.0, float(response.headers.get("retry-after", ""))))
    except (TypeError, ValueError):
        return None


def _post(s: Settings, audio: Path, fields: dict, log) -> dict:
    """Un envoi, avec réessais sur 429/5xx et sur incident réseau (jamais de repli local)."""
    url = f"{s.url}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {s.key}"} if s.key else {}
    wait = BACKOFF_S
    last = ""
    status = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with audio.open("rb") as fh:
                response = httpx.post(
                    url,
                    data=fields,
                    files={"file": (audio.name, fh, "audio/mpeg")},
                    headers=headers,
                    timeout=_timeout(),
                )
        except httpx.HTTPError as e:
            last = str(e)
            status = 0
            pause = wait
        else:
            if response.status_code < 400:
                try:
                    payload = response.json()
                except ValueError as e:
                    raise TranscriptionError(
                        _("Distant transcription: unreadable answer from {provider}", provider=s.label())
                    ) from e
                return payload if isinstance(payload, dict) else {"text": str(payload)}
            last = _detail(response)
            if response.status_code not in RETRY_STATUS:
                raise TranscriptionError(
                    _(
                        "Distant transcription refused by {provider} (HTTP {status}): {detail}",
                        provider=s.label(),
                        status=response.status_code,
                        detail=last,
                    )
                )
            status = response.status_code
            pause = _retry_after(response) or wait
        if attempt == MAX_ATTEMPTS:
            break
        log(
            _(
                "  distant transcription: HTTP {status}, attempt {attempt}/{attempts} failed — retrying in {seconds:.0f} s",
                status=status or "—",
                attempt=attempt,
                attempts=MAX_ATTEMPTS,
                seconds=pause,
            )
        )
        time.sleep(pause)
        wait = min(MAX_BACKOFF_S, wait * 2)
    raise TranscriptionError(
        _(
            "Distant transcription failed after {attempts} attempts ({provider}, HTTP {status}): {detail}",
            attempts=MAX_ATTEMPTS,
            provider=s.label(),
            status=status or "—",
            detail=last,
        )
    )


def _transcribe_file(s: Settings, audio: Path, prompt: str, language: str | None, log) -> dict:
    """Un morceau : `verbose_json`, segments ET mots horodatés, langue si elle est connue."""
    fields: dict = {
        "model": s.model,
        "response_format": "verbose_json",
        # liste : httpx répète le champ, comme l'exige `timestamp_granularities` (tableau)
        "timestamp_granularities[]": ["segment", "word"],
        "temperature": "0",
    }
    if language:
        fields["language"] = language
    if prompt:
        fields["prompt"] = prompt
    return _post(s, audio, fields, log)


# --------------------------------------------------------------------------- reprise des citations arabes


def _recover_arabic(s: Settings, wav: str, segs: list[dict], prompt: str, log, max_segments: int = 40) -> int:
    """Retranscrit en arabe forcé les passages que le fournisseur a rendus en français incohérent.

    Même logique que la reprise locale (`transcribe.recover_arabic`), sans `detect_language` : ces
    API n'en ont pas, le segment suspect part directement en arabe. Le coût reste borné
    (`max_segments`) et un segment n'est remplacé que si l'arabe est plus probable.
    """
    from . import transcribe as local

    replaced, tried = 0, 0
    with tempfile.TemporaryDirectory(prefix="sada-remote-ar-") as work:
        for seg in segs:
            if tried >= max_segments:
                break
            if not local.looks_suspect(seg):
                continue
            length = seg["end"] - seg["start"]
            if length < 0.5:
                continue
            tried += 1
            piece = cut(wav, Path(work) / "seg.mp3", seg["start"], length)
            try:
                payload = _transcribe_file(s, piece, prompt, "ar", log)
            except TranscriptionError as e:
                log(_("  Arabic retranscription impossible at {seconds:.1f}s: {error}", seconds=seg["start"], error=e))
                continue
            text = _payload_text(payload)
            if not text or arabic_ratio(text) < 0.5:
                continue
            lp = _payload_logprob(payload)
            sure = local.translit_ratio(seg.get("text") or "") >= local.TRANSLIT_SURE
            before = seg.get("avg_logprob")
            if not sure and lp <= (float(before) if before is not None else -9.0) + local.RETRY_MARGIN:
                continue
            words = []
            for raw in payload.get("segments") or []:
                words += _segment_words(raw, payload.get("words") or [], seg["start"])
            seg.update(text=text, avg_logprob=round(lp, 3), recovered="ar")
            if words:
                seg["words"] = words
            replaced += 1
            log(_("  Arabic passage recovered at {seconds:.1f}s: {heard}", seconds=seg["start"], heard=text[:60]))
    return replaced


# --------------------------------------------------------------------------- entrée du pipeline


def transcribe(
    wav: str,
    out_json: str,
    log=print,
    arabic_retry: bool | None = None,
    hotwords: str | None = None,
    language: str | None = None,
) -> dict:
    """Transcrit `wav` à distance et écrit `out_json`, dans la forme exacte du transcripteur local."""
    from . import transcribe as local

    s = settings()
    if not s.url:
        raise TranscriptionError(_("No distant transcription address: Settings → Transcription."))
    if not s.model:
        raise TranscriptionError(_("No distant transcription model: Settings → Transcription."))
    duration = wav_duration(wav)
    if duration <= 0:
        raise TranscriptionError(_("Unreadable audio: {name}", name=Path(wav).name))
    prompt = (_text(glossary.hotwords() if hotwords is None else hotwords))[:PROMPT_MAX]
    plan = chunk_plan(duration)
    log(_("Distant transcription: {provider}", provider=s.label()))
    log(
        _(
            "Distant transcription: {count} chunk(s), {seconds:.0f} s at most, {overlap:.0f} s overlap",
            count=len(plan),
            seconds=max(length for _, length in plan),
            overlap=OVERLAP_S,
        )
    )
    t0 = time.time()
    payloads: list[tuple[float, dict]] = []
    with tempfile.TemporaryDirectory(prefix="sada-remote-") as work:
        for i, (start, length) in enumerate(plan, 1):
            piece = cut(wav, Path(work) / f"chunk{i:03d}.mp3", start, length)
            if len(plan) > 1:
                log(
                    _(
                        "  chunk {index}/{count}: {start:.0f} s → {end:.0f} s",
                        index=i,
                        count=len(plan),
                        start=start,
                        end=start + length,
                    )
                )
            # La langue n'est transmise que si l'appelant la connaît : imposer la langue détectée du
            # premier morceau rendrait inaudible une citation arabe du second.
            payloads.append((start, _transcribe_file(s, piece, prompt, language, log)))
    segs = merge(payloads)
    detected = _text(next((p.get("language") for _, p in payloads if _text(p.get("language"))), ""))
    lang = language or detected or "und"
    res = {
        "model": s.model,
        "device": "distant",
        "compute_type": s.provider,
        "language": lang,
        "language_probability": 1.0 if (language or detected) else 0.0,
        "duration": round(duration, 2),
        "elapsed_s": round(time.time() - t0, 1),
        "hotwords": bool(prompt),
        "segments": segs,
    }
    retry = arabic_retry_enabled() if arabic_retry is None else bool(arabic_retry)
    if retry and local.dominant_language(segs, lang) != "ar":
        recovered = _recover_arabic(s, wav, segs, prompt, log)
        if recovered:
            res["arabic_recovered"] = recovered
            log(_("Arabic passages retranscribed: {replaced}", replaced=recovered))
    Path(out_json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    log(_("Transcription: {count} segments in {seconds} s", count=len(segs), seconds=res["elapsed_s"]))
    return res


def probe(timeout: float = 90.0) -> tuple[bool, str]:
    """Le fournisseur répond-il ? Envoie un court extrait généré : c'est le bouton « Tester »."""
    s = settings()
    if not s.url:
        return False, _("No distant transcription address: Settings → Transcription.")
    if not s.model:
        return False, _("No distant transcription model: Settings → Transcription.")
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="sada-remote-test-") as work:
            piece = Path(work) / "test.mp3"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=2",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "48k",
                    str(piece),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = _transcribe_file(s, piece, "", None, lambda m: None)
    except TranscriptionError as e:
        return False, str(e)
    except (OSError, subprocess.CalledProcessError) as e:
        return False, _("Cannot build the test audio: {error}", error=str(e)[:200])
    seconds = time.time() - t0
    heard = _payload_text(payload).strip()
    if heard:
        return True, _(
            "Distant transcription answers ({model}, {seconds:.1f} s): “{heard}”",
            model=s.label(),
            seconds=seconds,
            heard=heard[:160],
        )
    return True, _(
        "Distant transcription answers ({model}, {seconds:.1f} s): no speech recognised in the generated excerpt",
        model=s.label(),
        seconds=seconds,
    )
