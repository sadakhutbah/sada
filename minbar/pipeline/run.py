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
from .quran import load_verses
from .verses import VerseIndex, detect, to_dicts


def log(m):
    print(f"[pipeline] {m}", flush=True)


def process(src: str, start: float, end: float | None, label: str, outdir: Path, model: str | None, use_llm: bool, nclips: int, skip_clips: bool = False):
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
        wins = clipmod.select_windows(segs, ms, res["duration"], extra["quotes"], nclips)
        for i, w in enumerate(wins, 1):
            ass = cdir / f"clip{i}.ass"
            n = clipmod.build_ass(segs, ms, w["start"], w["end"], str(ass))
            out = cdir / f"clip{i}_{w['kind']}_A-VALIDER.mp4"
            clipmod.render_clip(src, start, w, ass.name, str(out), log)
            manifest.append({**w, "file": out.name, "subtitle_events": n, "status": "À VALIDER", "music": False,
                             "source_offset_s": round(start + w["start"], 2)})
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
    a = ap.parse_args(argv)
    if tr.setup_env() and not __import__("os").environ.get("MINBAR_REEXEC"):
        import os
        os.environ["MINBAR_REEXEC"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "minbar.pipeline.run"] + (argv if argv is not None else sys.argv[1:]))
    out = Path(a.out)
    if a.input:
        r = process(a.input, a.start, a.end, a.label, out / a.label, a.model, not a.no_llm, a.clips, a.no_clips)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    data = json.loads(Path(a.sessions).read_text())
    rec = data["recordings"][-1] if data.get("recordings") else None
    if not rec:
        sys.exit("aucun enregistrement dans sessions.json")
    for s in data["sessions"]:
        if a.only_session is not None and s["index"] != a.only_session:
            continue
        if s.get("exclude_from_pipeline") or s["status"] != "done":
            log(f"session {s['index']} ({s['planned']}) ignorée : statut={s['status']} exclue={s.get('exclude_from_pipeline')}")
            continue
        r = process(rec, s["start_pos"], s["end_pos"], f"session{s['index'] + 1}_{s['planned'].replace(':', 'h')}",
                    out / data["run_id"] / f"session{s['index'] + 1}", a.model, not a.no_llm, a.clips, a.no_clips)
        log(f"session {s['index']} : {r['segments']} segments, {r['verses']} versets, {len(r['clips'])} clips")


if __name__ == "__main__":
    main()
