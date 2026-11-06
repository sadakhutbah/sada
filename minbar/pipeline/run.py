"""Pipeline post-live : transcription -> versets -> notes -> clips.

    python -m minbar.pipeline.run data/state/<run>/sessions.json         # toutes les sessions « done »
    python -m minbar.pipeline.run --input video.mkv --start 0 --end 1000 --label test
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from ..config import ROOT
from . import clips as clipmod
from . import notes as notemod
from . import transcribe as tr
from . import translate as trmod
from .quran import load_verses
from .verses import VerseIndex, detect, to_dicts


def log(m):
    print(f"[pipeline] {m}", flush=True)


def process(src: str, start: float, end: float | None, label: str, outdir: Path, model: str | None, use_llm: bool, nclips: int,
            skip_clips: bool = False, clip_src: str | None = None, crop: dict | None = None, header: dict | None = None):
    """src = enregistrement pour l'audio/la transcription ; clip_src = vidéo propre (sans habillage) pour les clips.

    clip_src est soit aligné sur src (même processus ffmpeg : mêmes positions), soit une autre vidéo de la même
    khutba : dans ce cas `clip_src` = « chemin@auto » et le décalage est retrouvé par intercorrélation audio.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    wav = str(outdir / "audio.wav")
    log(f"[{label}] extraction audio {start:.1f}→{end}")
    tr.extract_audio(src, wav, start, end)
    tjson = outdir / "transcript.json"
    res = json.loads(tjson.read_text()) if tjson.exists() and model != "force" else tr.transcribe(wav, str(tjson), None if model in (None, "force") else model, log)
    segs = res["segments"]
    (outdir / "transcript.md").write_text("# Transcription — " + label + "\n\n" + "\n".join(f"- **[{notemod.fmt_t(s['start'])}]** {s['text']}" for s in segs), encoding="utf-8")
    log("chargement du Coran + index")
    verses = load_verses()
    idx = VerseIndex(verses)
    ms = to_dicts(detect(segs, idx))
    (outdir / "verses.json").write_text(json.dumps(ms, ensure_ascii=False, indent=1))
    log(f"{len(ms)} versets détectés : " + ", ".join(f"{m['ref']}@{notemod.fmt_t(m['start'])}({m['score']:.0f})" for m in ms))
    md, extra = notemod.build_notes(label, segs, ms, res, use_llm, log)
    (outdir / "notes.md").write_text(md, encoding="utf-8")
    log(f"notes -> {outdir/'notes.md'} (résumé : {extra['summary'].get('method')})")
    manifest = []
    if not skip_clips:
        cdir = outdir / "clips"
        cdir.mkdir(exist_ok=True)
        trs = trmod.translate_session(segs, ms, outdir / "translations.json", use_llm, log=log)
        vsrc, voff = src, start
        if clip_src:
            path, _, mode = clip_src.partition("@")
            vsrc = path
            if mode == "auto":
                voff, sharp = clipmod.align_offset(wav, path)
                log(f"clips : {Path(path).name} alignée par l'audio, décalage {voff:+.2f} s (netteté {sharp:.0f})")
                if sharp < 8:
                    log("clips : alignement douteux -> retour à l'enregistrement")
                    vsrc, voff = src, start
        log(f"clips : vidéo {vsrc}")
        wins = clipmod.select_windows(segs, ms, res["duration"], extra["quotes"], nclips)
        for i, w in enumerate(wins, 1):
            ass = cdir / f"clip{i}.ass"
            n = clipmod.build_ass(segs, ms, w["start"], w["end"], str(ass), tr=trs, header=header)
            out = cdir / f"clip{i}_{w['kind']}_A-VALIDER.mp4"
            clipmod.render_clip(vsrc, voff, w, ass.name, str(out), log, crop=crop)
            manifest.append({**w, "file": out.name, "subtitle_events": n, "status": "À VALIDER", "music": False,
                             "video_source": str(vsrc), "source_offset_s": round(voff + w["start"], 2),
                             "crop": crop or None, "subtitles": "français seul"})
        (cdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return {"segments": len(segs), "verses": len(ms), "clips": manifest}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="?", help="sessions.json écrit par l'orchestrateur")
    ap.add_argument("--input")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--label", default="session")
    ap.add_argument("--out", default=str(ROOT / "data" / "output"))
    ap.add_argument("--model", help="large-v3 | large-v3-turbo | medium | small | cpu | force (retranscrire)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-clips", action="store_true")
    ap.add_argument("--clips", type=int, default=3)
    ap.add_argument("--only-session", type=int)
    ap.add_argument("--clip-source", help="vidéo pour les clips (défaut : enregistrement propre live_*_clean.mkv s'il existe) ; "
                    "suffixe @auto = autre vidéo de la même khutba, alignée par l'audio")
    ap.add_argument("--crop", help="recadrage 9:16 : x=0.52,top=0,bottom=0.15 (fractions ; bottom = bandeau incrusté à exclure)")
    ap.add_argument("--title", default="Khutba du vendredi")
    ap.add_argument("--subtitle", default="", help="ligne sous le titre des clips (ex. mosquée · date)")
    a = ap.parse_args(argv)
    if tr.setup_env() and not __import__("os").environ.get("MINBAR_REEXEC"):
        import os
        os.environ["MINBAR_REEXEC"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "minbar.pipeline.run"] + (argv if argv is not None else sys.argv[1:]))
    out = Path(a.out)
    crop = {k: float(v) for k, v in (kv.split("=") for kv in a.crop.split(","))} if a.crop else None
    header = {"title": a.title, "subtitle": a.subtitle} if a.title else None
    if a.input:
        r = process(a.input, a.start, a.end, a.label, out / a.label, a.model, not a.no_llm, a.clips, a.no_clips,
                    a.clip_source, crop, header)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    data = json.loads(Path(a.sessions).read_text())
    rec = data["recordings"][-1] if data.get("recordings") else None
    if not rec:
        sys.exit("aucun enregistrement dans sessions.json")
    clip_src = a.clip_source
    if not clip_src:  # enregistrement caméra sans habillage, même processus ffmpeg -> mêmes positions
        cleans = data.get("recordings_clean") or []
        clean = cleans[len(data["recordings"]) - 1] if len(cleans) == len(data["recordings"]) else None
        if clean and Path(clean).exists() and Path(clean).stat().st_size > 0:
            clip_src = clean
        else:
            log("pas d'enregistrement propre (live_*_clean.mkv) : clips taillés dans l'enregistrement AVEC habillage")
    for s in data["sessions"]:
        if a.only_session is not None and s["index"] != a.only_session:
            continue
        if s.get("exclude_from_pipeline") or s["status"] != "done":
            log(f"session {s['index']} ({s['planned']}) ignorée : statut={s['status']} exclue={s.get('exclude_from_pipeline')}")
            continue
        r = process(rec, s["start_pos"], s["end_pos"], f"session{s['index'] + 1}_{s['planned'].replace(':', 'h')}",
                    out / data["run_id"] / f"session{s['index'] + 1}", a.model, not a.no_llm, a.clips, a.no_clips,
                    clip_src, crop, header)
        log(f"session {s['index']} : {r['segments']} segments, {r['verses']} versets, {len(r['clips'])} clips")


if __name__ == "__main__":
    main()
