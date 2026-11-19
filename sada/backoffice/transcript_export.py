"""Export of a session transcript (original text + translation, timestamped) as .txt or .srt."""

from __future__ import annotations

import re

from ..pipeline import subtitles

FORMATS = {
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "txt": ("text/plain; charset=utf-8", "txt"),
}


def entries(rows: list[dict]) -> list[dict]:
    """Rows of `content.transcript_rows` reduced to (start, end, original, translation), in time order.

    The translation is the French subtitle, or the verse translation for a recitation; it is dropped
    when empty or identical to the original text."""
    out = []
    for r in rows:
        original = " ".join((r.get("text") or "").split())
        if not original:
            continue
        tr = r.get("fr") or (r.get("verse") or {}).get("fr") or ""
        tr = " ".join(tr.split())
        out.append(
            {"start": r["start"], "end": r["end"], "text": original, "translation": "" if tr == original else tr}
        )
    out.sort(key=lambda e: (e["start"], e["end"]))
    return out


def to_srt(items: list[dict]) -> str:
    """Numbered cues, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, original line then translation line, blank line between."""
    cues = [
        {
            "start": e["start"],
            "end": max(e["end"], e["start"]),
            "lines": [e["text"], *([e["translation"]] if e["translation"] else [])],
        }
        for e in items
    ]
    return subtitles.srt(cues)


def to_txt(items: list[dict]) -> str:
    """One block per segment: `[HH:MM:SS]` then the original, the translation indented below."""
    blocks = []
    for e in items:
        stamp = subtitles.timestamp(e["start"])[:8]
        block = f"[{stamp}] {e['text']}"
        if e["translation"]:
            block += f"\n           {e['translation']}"
        blocks.append(block)
    return "\n".join(blocks) + ("\n" if blocks else "")


def render(rows: list[dict], fmt: str) -> bytes:
    items = entries(rows)
    return (to_srt(items) if fmt == "srt" else to_txt(items)).encode("utf-8")


def filename(label: str, kid: int, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label or "").strip("-").lower()
    return f"{slug or 'khutba'}-{kid}-transcript.{ext}"
