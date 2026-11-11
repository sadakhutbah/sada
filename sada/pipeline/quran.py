"""Quran text (api.alquran.cloud: quran-simple-clean + fr.hamidullah translation), cached."""
from __future__ import annotations
import json
from pathlib import Path

import requests

from ..paths import CACHE
from .arabic import normalize

BASMALA = "بسم الله الرحمن الرحيم"


def _get(edition: str) -> dict:
    p = CACHE / f"{edition}.json"
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        r = requests.get(f"https://api.alquran.cloud/v1/quran/{edition}", timeout=60)
        r.raise_for_status()
        p.write_bytes(r.content)
    return json.loads(p.read_text())["data"]["surahs"]


def load_verses() -> list[dict]:
    """[{surah, ayah, surah_name, text, norm, fr}]; the basmala prefixed to verse 1 is stripped."""
    ar = _get("quran-simple-clean")
    fr = _get("fr.hamidullah")
    out = []
    for si, s in enumerate(ar):
        for ai, a in enumerate(s["ayahs"]):
            txt = a["text"]
            if a["numberInSurah"] == 1 and s["number"] not in (1, 9) and txt.startswith(BASMALA):
                txt = txt[len(BASMALA):].strip()
            out.append({
                "surah": s["number"], "ayah": a["numberInSurah"], "surah_name": s["englishName"],
                "text": txt, "norm": normalize(txt),
                "fr": fr[si]["ayahs"][ai]["text"],
            })
    return out
