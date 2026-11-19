"""Export of a session transcript (original text + translation, timestamped) as .txt or .srt."""

from __future__ import annotations

import re

from ..pipeline import subtitles

FORMATS = {
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "txt": ("text/plain; charset=utf-8", "txt"),
}


TIMINGS = ("video", "raw")


def entries(rows: list[dict], shift: float = 0.0) -> list[dict]:
    """Rows of `content.transcript_rows` reduced to (start, end, original, translation), in time order.

    The translation is the French subtitle, or the verse translation for a recitation; it is dropped
    when empty or identical to the original text.

    `shift` (seconds) moves session time to the video's time (see `habillage.alignements`). A segment
    that ends before the video starts is dropped; one that starts before it is trimmed to 0."""
    out = []
    for r in rows:
        original = " ".join((r.get("text") or "").split())
        if not original:
            continue
        tr = r.get("fr") or (r.get("verse") or {}).get("fr") or ""
        tr = " ".join(tr.split())
        start, end = r["start"] + shift, r["end"] + shift
        if end <= 0:
            continue
        out.append(
            {"start": max(0.0, start), "end": end, "text": original, "translation": "" if tr == original else tr}
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


def render(rows: list[dict], fmt: str, shift: float = 0.0) -> bytes:
    items = entries(rows, shift)
    return (to_srt(items) if fmt == "srt" else to_txt(items)).encode("utf-8")


def filename(label: str, kid: int, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label or "").strip("-").lower()
    return f"{slug or 'khutba'}-{kid}-transcript.{ext}"
