"""Post-live pipeline: transcription -> verses -> notes -> clips.

    python -m sada.pipeline.run                                        # latest orchestrator run
    python -m sada.pipeline.run data/state/<run>/sessions.json         # all finished sessions
    python -m sada.pipeline.run --input video.mkv --start 0 --end 1000 --label test
    python -m sada.pipeline.run --llm-url http://127.0.0.1:8000/v1 --llm-model mistral-small
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from ..i18n import _
from .. import paths, runs
from ..text import duration as fmt_t
from . import clips as clipmod
from . import notes as notemod
from . import select as selmod
from . import transcribe as tr
from . import llm, translate as trmod
from . import settings as reglages
from .output import SessionOutput, clip_entry
from .quran import load_verses
from .verses import VerseIndex, detect, to_dicts


def log(m):
    print(f"[pipeline] {m}", flush=True)


def transcript_key(src: str, start: float, end: float | None) -> dict:
    """What a cached transcript was made from: reused only for the same recording and the same range."""
    return {"recording": str(Path(src).resolve()), "start": round(float(start or 0), 2),
            "end": None if end is None else round(float(end), 2)}


def transcript_matches(res: dict, key: dict, wav: str) -> bool:
    """A transcript from another range would put every subtitle at the wrong moment of the clips."""
    if "source" in res:
        return res["source"] == key
    # Older transcripts carry no source: the audio length is the only witness left.
    try:
        import wave
        with wave.open(wav) as w:
            seconds = w.getnframes() / w.getframerate()
        return abs(seconds - float(res.get("duration") or -1)) < 1.0
    except Exception:
        return False


def process(src: str, start: float, end: float | None, label: str, outdir: Path, *, model: str | None = None,
            retranscribe: bool = False, use_llm: bool = True, nclips: int = 3, clip_video: str | None = None,
            align: bool = False, crop: dict | None = None, header: dict | None = None,
            arabic_retry: bool | None = None, logo: str | None = None, brand: dict | None = None):
    """src = recording for the audio/transcription; clip_video = clean video (without overlay) for the clips.

    clip_video is either aligned with src (same ffmpeg process: same positions), or another video of the same
    khutba (align=True): the offset is then recovered by audio cross-correlation.
    """
    o = SessionOutput(outdir)
    o.dir.mkdir(parents=True, exist_ok=True)
    wav = str(o.audio)
    log(_("[{label}] audio extraction {start:.1f}→{end}", label=label, start=start, end=end))
    tr.extract_audio(src, wav, start, end)
    res = o.read(o.transcript, None) if not retranscribe else None
    key = transcript_key(src, start, end)
    if res and not transcript_matches(res, key, wav):
        log(_("[{label}] cached transcript was made on another excerpt: transcribing again", label=label))
        res = None
    if not res:
        res = tr.transcribe(wav, str(o.transcript), model, log, arabic_retry=arabic_retry)
        res["source"] = key
        o.write(o.transcript, res)
    segs = res["segments"]
    o.transcript_md.write_text("# Transcription — " + label + "\n\n" + "\n".join(f"- **[{fmt_t(s['start'])}]** {s['text']}" for s in segs), encoding="utf-8")
    log(_("loading the Quran + index"))
    idx = VerseIndex(load_verses())
    ms = to_dicts(detect(segs, idx))
    o.write(o.verses, ms)
    log(_("{count} verses detected: {refs}", count=len(ms),
          refs=", ".join(f"{m['ref']}@{fmt_t(m['start'])}({m['score']:.0f})" for m in ms)))
    md, extra = notemod.build_notes(label, segs, ms, res, use_llm, log)
    o.notes.write_text(md, encoding="utf-8")
    log(_("notes -> {path} (summary: {method})", path=o.notes, method=extra["summary"].get("method")))
    manifest = []
    o.clips.mkdir(exist_ok=True)
    # Extraits d'abord, traduction ensuite : une khutba de 20 minutes devient 1-3 clips d'environ
    # une minute, traduire toute la séance brûlerait le modèle pour du texte que personne ne lira.
    # La sélection est décidée par sada.pipeline.select : le code génère les candidats (fenêtres
    # 30-75 s propres), un petit modèle les note par lots de 5, le code retient les N meilleurs
    # espacés — repli heuristique sans modèle. Le résumé ne porte plus les bornes des clips.
    wins = []
    if nclips:
        wins = selmod.select_clips(segs, res["duration"], nclips, use_llm=use_llm, log=log)
    if nclips and not wins:
        log(_("no publishable clip: nothing is edited"))
    # No clips to subtitle: translating the session would burn the model for text nobody reads.
    trs = (trmod.translate_session(segs, ms, o.translations, use_llm, log=log,
                                   only=[(w["start"], w["end"]) for w in wins])
           if nclips else {"seg": {}, "verse_fr": {}})
    if nclips:
        vsrc, voff = src, start
        if clip_video:
            vsrc = clip_video
            if align:
                voff, sharp = clipmod.align_offset(wav, clip_video)
                log(_("clips: {name} aligned on the audio, offset {offset:+.2f} s (sharpness {sharp:.0f})",
                      name=Path(clip_video).name, offset=voff, sharp=sharp))
                if sharp < 8:
                    log(_("clips: doubtful alignment -> back to the recording"))
                    vsrc, voff = src, start
        log(_("clips: video {video}", video=vsrc))
        for i, w in enumerate(wins, 1):
            out = clipmod.make_clip(segs, ms, trs, w, i, 1, vsrc, voff, o.clips, header, crop, log,
                                    logo=logo, brand=brand)
            manifest.append(clip_entry(w, i, out, vsrc, voff, crop))
        o.write_manifest(manifest)
    return {"segments": len(segs), "verses": len(ms), "clips": manifest}


def _clip_video(clean: str | None) -> str | None:
    """Camera recording without overlay (same ffmpeg process -> same positions), if it exists."""
    if clean and Path(clean).exists() and Path(clean).stat().st_size > 0:
        return clean
    log(_("no clean recording (live_*_clean.mkv): clips cut from the recording WITH branding"))
    return None


def nclips_option(a) -> int:
    """Extraits par khutba: --no-clips, then --clips, then the setting saved in the back office."""
    return 0 if a.no_clips else reglages.clip_count(a.clips)


def apply_llm_options(a) -> None:
    """The notes/translate modules read the environment: the CLI options fill it once and for all."""
    for env_name, val in (("SADA_LLM_URL", a.llm_url), ("SADA_LLM_MODEL", a.llm_model),
                          ("SADA_LLM_PROTOCOL", a.llm_protocol)):
        if val:
            os.environ[env_name] = val
    if a.llm is not None:
        os.environ["SADA_LLM_ENABLED"] = "1" if a.llm else "0"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="?",
                    help=_("sessions.json written by the orchestrator (default: the latest run)"))
    ap.add_argument("--input")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--label", default="session")
    ap.add_argument("--out", default=str(paths.OUTPUT))
    ap.add_argument("--model", help=_("large-v3 | large-v3-turbo | medium | small | cpu | force (transcribe again)"))
    ap.add_argument("--llm", action=argparse.BooleanOptionalAction, default=None,
                    help=_("enable or cut the model call (default: SADA_LLM_ENABLED, otherwise enabled)"))
    ap.add_argument("--llm-url", default=os.environ.get("SADA_LLM_URL"),
                    help=_("model endpoint (default: http://127.0.0.1:11434/v1, Ollama)"))
    ap.add_argument("--llm-model", default=os.environ.get("SADA_LLM_MODEL"),
                    help=_("model name on the server side (e.g. qwen2.5:7b-instruct)"))
    ap.add_argument("--llm-protocol", default=os.environ.get("SADA_LLM_PROTOCOL"), choices=["openai", "anthropic", "claude-cli"],
                    help=_("API format (deduced from the URL when absent)"))
    ap.add_argument("--no-clips", action="store_true")
    ap.add_argument("--clips", type=int, default=None,
                    help=_("clips per khutba (default: the back-office setting, otherwise {default}); 0 = none",
                           default=reglages.DEFAULT_CLIPS))
    ap.add_argument("--only-session", type=int)
    ap.add_argument("--clip-source", help=_("video for the clips (default: the clean recording live_*_clean.mkv if it "
                                            "exists); the @auto suffix = another video of the same khutba, aligned by audio"))
    ap.add_argument("--crop", help=_("9:16 crop: x=0.52,top=0,bottom=0.15 (fractions; bottom = burnt-in banner to exclude)"))
    ap.add_argument("--title", default=clipmod.DEFAULT_TITLE)
    ap.add_argument("--subtitle", default="", help=_("line under the clip title (e.g. mosque · date)"))
    ap.add_argument("--arabic-retry", action=argparse.BooleanOptionalAction, default=None,
                    help=_("transcribe again in Arabic the quotations rendered as incoherent French "
                           "(default: SADA_ARABIC_RETRY, otherwise enabled)"))
    ap.add_argument("--logo", help=_("mosque logo (PNG/JPG) in the clip header"))
    ap.add_argument("--brand-green", help=_("mosque green (#RRGGBB); default: current colours"))
    ap.add_argument("--brand-gold", help=_("mosque gold (#RRGGBB); default: current colours"))
    ap.add_argument("--glossary", help=_("JSON glossary of Islamic terms "
                                         "(default: SADA_GLOSSARY, otherwise data/glossary.json, otherwise the code glossary)"))
    a = ap.parse_args(argv)
    if a.glossary:
        os.environ["SADA_GLOSSARY"] = a.glossary
    apply_llm_options(a)
    model_label = llm.label() if llm.enabled() else _("disabled (fallback without AI)")
    log(_("model: {model}", model=model_label))
    nclips = nclips_option(a)
    log(_("clips per khutba: {count}", count=nclips) if nclips else _("clips per khutba: none"))
    if tr.setup_env() and not os.environ.get("SADA_REEXEC"):
        os.environ["SADA_REEXEC"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "sada.pipeline.run"] + (argv if argv is not None else sys.argv[1:]))
    out = Path(a.out)
    opts = {
        "model": None if a.model == "force" else a.model, "retranscribe": a.model == "force",
        "use_llm": llm.enabled(),
        "nclips": nclips,
        "arabic_retry": a.arabic_retry,
        "logo": a.logo,
        "brand": {k: v for k, v in (("green", a.brand_green), ("gold", a.brand_gold)) if v} or None,
        "crop": {k: float(v) for k, v in (kv.split("=") for kv in a.crop.split(","))} if a.crop else None,
        "header": {"title": a.title, "subtitle": a.subtitle} if a.title else None,
    }
    forced, _sep, mode = (a.clip_source or "").partition("@")
    if forced:
        opts["align"] = mode == "auto"
    if a.input:
        r = process(a.input, a.start, a.end, a.label, out / a.label, clip_video=forced or None, **opts)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    sj = Path(a.sessions) if a.sessions else runs.latest()
    if sj is None:
        sys.exit(_("no run in {path}", path=paths.STATE))
    data = runs.load(sj)
    for s in data["sessions"]:
        if a.only_session is not None and s["index"] != a.only_session:
            continue
        if not runs.is_done(s):
            log(_("session {index} ({planned}) skipped: status={status}",
                  index=s["index"], planned=s["planned"], status=s["status"]))
            continue
        live, clean = runs.sources(data, s)
        if not live:
            log(_("session {index} ({planned}) skipped: no recording", index=s["index"], planned=s["planned"]))
            continue
        sopts = dict(opts, header=session_header(s, data, a)) if opts["header"] else opts
        r = process(live, s["start_pos"], s["end_pos"], f"{runs.session_dir(s['index'])}_{s['planned'].replace(':', 'h')}",
                    out / data["run_id"] / runs.session_dir(s["index"]),
                    clip_video=forced or _clip_video(clean), **sopts)
        log(_("session {index}: {segments} segments, {verses} verses, {clips} clips",
              index=s["index"], segments=r["segments"], verses=r["verses"], clips=len(r["clips"])))


def session_header(s: dict, data: dict, a) -> dict:
    """Clip header of a session: its own kind and title (a dars imported is not a « Khutba du vendredi »),
    and the mosque as subtitle so the clip carries the mosque's identity. --title/--subtitle still win."""
    from ..events import kind_label
    kind = s.get("kind") or "khutba"
    title = a.title
    if title == clipmod.DEFAULT_TITLE:
        title = (s.get("title") or "").strip() or (clipmod.DEFAULT_TITLE if kind == "khutba" else kind_label(kind))
    return {"title": title, "subtitle": a.subtitle or (data.get("mosque") or "")}


if __name__ == "__main__":
    main()
