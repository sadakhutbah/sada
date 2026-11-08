"""Texte du Coran (api.alquran.cloud : quran-simple-clean + traduction fr.hamidullah), mis en cache."""
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


def load_verses(with_translation: bool = True) -> list[dict]:
    """[{surah, ayah, surah_name, text, norm, fr}] ; la basmala préfixée au verset 1 est retirée."""
    ar = _get("quran-simple-clean")
    fr = _get("fr.hamidullah") if with_translation else None
    out = []
    for si, s in enumerate(ar):
        for ai, a in enumerate(s["ayahs"]):
            txt = a["text"]
            if a["numberInSurah"] == 1 and s["number"] not in (1, 9) and txt.startswith(BASMALA):
                txt = txt[len(BASMALA):].strip()
            out.append({
                "surah": s["number"], "ayah": a["numberInSurah"], "surah_name": s["englishName"],
                "surah_ar": s["name"], "text": txt, "norm": normalize(txt),
                "fr": fr[si]["ayahs"][ai]["text"] if fr else "",
            })
    return out
