"""Dossier de sortie d'une session : data/output/<run_id>/<runs.session_dir(index)>/.

Écrit par le pipeline (run.py), lu par le back-office. Tout nom de fichier et tout champ du manifeste des clips
passent par ici.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


class SessionOutput:
    def __init__(self, path: str | Path):
        self.dir = Path(path)
        self.audio = self.dir / "audio.wav"
        self.transcript = self.dir / "transcript.json"
        self.transcript_md = self.dir / "transcript.md"
        self.verses = self.dir / "verses.json"
        self.notes = self.dir / "notes.md"
        self.translations = self.dir / "translations.json"
        self.video = self.dir / "khutba.mp4"           # extrait du live, écrit par le back-office
        self.clips = self.dir / "clips"
        self.manifest = self.clips / "manifest.json"

    def is_transcribed(self) -> bool:
        return self.transcript.exists()

    def read(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write(self, path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def write_manifest(self, entries: list[dict]) -> None:
        self.write(self.manifest, entries)

    def manifest_entries(self) -> list[dict]:
        """Clips du manifeste, au format courant (manifestes antérieurs convertis)."""
        return [_current(c, n) for n, c in enumerate(self.read(self.manifest, []), 1)]


def clip_entry(w: dict, idx: int, file: Path, video: str, offset: float, crop: dict | None) -> dict:
    """Entrée du manifeste pour le clip n° idx de la fenêtre w ; offset = début de la session dans `video`."""
    return {"idx": idx, "kind": w.get("kind", ""), "why": w.get("why", ""), "start": w["start"], "end": w["end"],
            "file": file.name, "video_source": str(video), "source_offset": round(offset, 2), "crop": crop or None}


def _current(c: dict, n: int) -> dict:
    c = dict(c)
    if "idx" not in c:                                  # manifestes antérieurs au champ idx
        m = re.match(r"clip(\d+)_", c["file"])
        c["idx"] = int(m.group(1)) if m else n
    if "source_offset" not in c:                        # manifestes antérieurs : position absolue du début du clip
        old = c.get("source_offset_s")
        c["source_offset"] = old - c["start"] if old is not None else None
    c.setdefault("kind", "")
    c.setdefault("why", "")
    c.setdefault("video_source", None)
    c.setdefault("crop", None)
    return c
