"""Post-live pipeline: transcription -> verses -> notes -> clips.

    python -m minbar.pipeline.run                                        # latest orchestrator run
    python -m minbar.pipeline.run data/state/<run>/sessions.json         # all finished sessions
    python -m minbar.pipeline.run --input video.mkv --start 0 --end 1000 --label test
    python -m minbar.pipeline.run --llm-url http://127.0.0.1:8000/v1 --llm-model mistral-small
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from .. import paths, runs
from ..text import duration as fmt_t
from . import clips as clipmod
from . import notes as notemod
from . import transcribe as tr
from . import llm, translate as trmod
from . import settings as reglages
from .output import SessionOutput, clip_entry
from .quran import load_verses
from .verses import VerseIndex, detect, to_dicts


def log(m):
    print(f"[pipeline] {m}", flush=True)


def process(src: str, start: float, end: float | None, label: str, outdir: Path, *, model: str | None = None,
            retranscribe: bool = False, use_llm: bool = True, nclips: int = 3, clip_video: str | None = None,
            align: bool = False, crop: dict | None = None, header: dict | None = None):
    """src = recording for the audio/transcription; clip_video = clean video (without overlay) for the clips.

    clip_video is either aligned with src (same ffmpeg process: same positions), or another video of the same
    khutba (align=True): the offset is then recovered by audio cross-correlation.
    """
    o = SessionOutput(outdir)
    o.dir.mkdir(parents=True, exist_ok=True)
    wav = str(o.audio)
    log(f"[{label}] extraction audio {start:.1f}→{end}")
    tr.extract_audio(src, wav, start, end)
    res = o.read(o.transcript, None) if not retranscribe else None
    res = res or tr.transcribe(wav, str(o.transcript), model, log)
    segs = res["segments"]
    o.transcript_md.write_text("# Transcription — " + label + "\n\n" + "\n".join(f"- **[{fmt_t(s['start'])}]** {s['text']}" for s in segs), encoding="utf-8")
    log("chargement du Coran + index")
    idx = VerseIndex(load_verses())
    ms = to_dicts(detect(segs, idx))
    o.write(o.verses, ms)
    log(f"{len(ms)} versets détectés : " + ", ".join(f"{m['ref']}@{fmt_t(m['start'])}({m['score']:.0f})" for m in ms))
    md, extra = notemod.build_notes(label, segs, ms, res, use_llm, log, n_clips=nclips)
    o.notes.write_text(md, encoding="utf-8")
    log(f"notes -> {o.notes} (résumé : {extra['summary'].get('method')})")
    manifest = []
    o.clips.mkdir(exist_ok=True)
    # Windows first, translation second: a 20-minute khutba becomes 1-3 clips of ~1 minute, so
    # translating the whole session burns the LLM for text nobody will read. Only the windows are translated.
    # The model already read the whole transcript for the notes: its clip boundaries are used as the
    # windows (one call per transcript, not per clip). Each one is checked mechanically by notes.py;
    # an empty or doubtful list falls back on the code's own selection.
    wins = []
    if nclips:
        wins = extra.get("clip_windows") or []
        if wins:
            log("clips : bornes proposées par le modèle, " + ", ".join(f"{w['start']:.0f}→{w['end']:.0f}" for w in wins))
            wins = clipmod.refine_windows(wins, segs, res["duration"])
        else:
            log("clips : pas de bornes exploitables du modèle, découpage par le code")
        # The model rarely proposes as many extracts as asked: rather than settle for one clip per
        # khutba, the code's own candidates fill the remaining slots (no overlap, chronological).
        if len(wins) < nclips:
            extra_wins = clipmod.select_windows(segs, ms, res["duration"], extra["quotes"], nclips)
            wins = clipmod.merge_windows(wins, extra_wins, nclips)
            if wins:
                log(f"clips : {len(wins)} extraits après complément (code : {len(extra_wins)} candidats)")
    if nclips and not wins:
        log("aucun clip publiable (ni rappel de verset ni citation) : rien n'est monté")
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
                log(f"clips : {Path(clip_video).name} alignée par l'audio, décalage {voff:+.2f} s (netteté {sharp:.0f})")
                if sharp < 8:
                    log("clips : alignement douteux -> retour à l'enregistrement")
                    vsrc, voff = src, start
        log(f"clips : vidéo {vsrc}")
        for i, w in enumerate(wins, 1):
            out = clipmod.make_clip(segs, ms, trs, w, i, 1, vsrc, voff, o.clips, header, crop, log)
            manifest.append(clip_entry(w, i, out, vsrc, voff, crop))
        o.write_manifest(manifest)
    return {"segments": len(segs), "verses": len(ms), "clips": manifest}


def _clip_video(clean: str | None) -> str | None:
    """Camera recording without overlay (same ffmpeg process -> same positions), if it exists."""
    if clean and Path(clean).exists() and Path(clean).stat().st_size > 0:
        return clean
    log("pas d'enregistrement propre (live_*_clean.mkv) : clips taillés dans l'enregistrement AVEC habillage")
    return None


def nclips_option(a) -> int:
    """Extraits par khutba: --no-clips, then --clips, then the setting saved in the back office."""
    return 0 if a.no_clips else reglages.clip_count(a.clips)


def apply_llm_options(a) -> None:
    """The notes/translate modules read the environment: the CLI options fill it once and for all."""
    for env_name, val in (("MINBAR_LLM_URL", a.llm_url), ("MINBAR_LLM_MODEL", a.llm_model),
                          ("MINBAR_LLM_PROTOCOL", a.llm_protocol)):
        if val:
            os.environ[env_name] = val
    if a.llm is not None:
        os.environ["MINBAR_LLM_ENABLED"] = "1" if a.llm else "0"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="?", help="sessions.json écrit par l'orchestrateur (défaut : le dernier run)")
    ap.add_argument("--input")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--label", default="session")
    ap.add_argument("--out", default=str(paths.OUTPUT))
    ap.add_argument("--model", help="large-v3 | large-v3-turbo | medium | small | cpu | force (retranscrire)")
    ap.add_argument("--llm", action=argparse.BooleanOptionalAction, default=None,
                    help="activer ou couper l'appel au modèle (défaut : MINBAR_LLM_ENABLED, sinon activé)")
    ap.add_argument("--llm-url", default=os.environ.get("MINBAR_LLM_URL"),
                    help="endpoint du modèle (défaut : http://127.0.0.1:11434/v1, Ollama)")
    ap.add_argument("--llm-model", default=os.environ.get("MINBAR_LLM_MODEL"),
                    help="nom du modèle côté serveur (ex. qwen2.5:7b-instruct)")
    ap.add_argument("--llm-protocol", default=os.environ.get("MINBAR_LLM_PROTOCOL"), choices=["openai", "anthropic", "claude-cli"],
                    help="format d'API (déduit de l'URL si absent)")
    ap.add_argument("--no-clips", action="store_true")
    ap.add_argument("--clips", type=int, default=None,
                    help=f"extraits par khutba (défaut : réglage du back-office, sinon {reglages.DEFAULT_CLIPS}) ; 0 = aucun")
    ap.add_argument("--only-session", type=int)
    ap.add_argument("--clip-source", help="vidéo pour les clips (défaut : enregistrement propre live_*_clean.mkv s'il existe) ; "
                    "suffixe @auto = autre vidéo de la même khutba, alignée par l'audio")
    ap.add_argument("--crop", help="recadrage 9:16 : x=0.52,top=0,bottom=0.15 (fractions ; bottom = bandeau incrusté à exclure)")
    ap.add_argument("--title", default=clipmod.DEFAULT_TITLE)
    ap.add_argument("--subtitle", default="", help="ligne sous le titre des clips (ex. mosquée · date)")
    a = ap.parse_args(argv)
    apply_llm_options(a)
    log(f"modèle : {llm.label() if llm.enabled() else 'désactivé (repli sans IA)'}")
    nclips = nclips_option(a)
    log("extraits par khutba : " + (str(nclips) if nclips else "aucun"))
    if tr.setup_env() and not os.environ.get("MINBAR_REEXEC"):
        os.environ["MINBAR_REEXEC"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "minbar.pipeline.run"] + (argv if argv is not None else sys.argv[1:]))
    out = Path(a.out)
    opts = {
        "model": None if a.model == "force" else a.model, "retranscribe": a.model == "force",
        "use_llm": llm.enabled(),
        "nclips": nclips,
        "crop": {k: float(v) for k, v in (kv.split("=") for kv in a.crop.split(","))} if a.crop else None,
        "header": {"title": a.title, "subtitle": a.subtitle} if a.title else None,
    }
    forced, _, mode = (a.clip_source or "").partition("@")
    if forced:
        opts["align"] = mode == "auto"
    if a.input:
        r = process(a.input, a.start, a.end, a.label, out / a.label, clip_video=forced or None, **opts)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    sj = Path(a.sessions) if a.sessions else runs.latest()
    if sj is None:
        sys.exit("aucun run dans " + str(paths.STATE))
    data = runs.load(sj)
    for s in data["sessions"]:
        if a.only_session is not None and s["index"] != a.only_session:
            continue
        if not runs.is_done(s):
            log(f"session {s['index']} ({s['planned']}) ignorée : statut={s['status']}")
            continue
        live, clean = runs.sources(data, s)
        if not live:
            log(f"session {s['index']} ({s['planned']}) ignorée : aucun enregistrement")
            continue
        r = process(live, s["start_pos"], s["end_pos"], f"{runs.session_dir(s['index'])}_{s['planned'].replace(':', 'h')}",
                    out / data["run_id"] / runs.session_dir(s["index"]),
                    clip_video=forced or _clip_video(clean), **opts)
        log(f"session {s['index']} : {r['segments']} segments, {r['verses']} versets, {len(r['clips'])} clips")


if __name__ == "__main__":
    main()
