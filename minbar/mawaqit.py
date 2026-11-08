"""Horaires de Jumu'a depuis Mawaqit, avec cache local (repli si le site est injoignable)."""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) minbar-live/0.1"


def _extract_conf(html: str) -> dict:
    m = re.search(r"confData\s*=\s*(\{.*?\});\s*\n", html, re.S)
    if not m:
        raise ValueError("confData introuvable dans la page Mawaqit")
    return json.loads(m.group(1))


def fetch_live(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    conf = _extract_conf(r.text)
    return {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": url,
        "name": conf.get("name"),
        "jumua": conf.get("jumua"),
        "jumua2": conf.get("jumua2"),
        "jumua3": conf.get("jumua3"),
        "times": conf.get("times"),
        "timezone": conf.get("timezone"),
    }


def get_jumua(url: str, cache_path: str, max_age_h: float = 20.0, timeout: float = 15.0) -> dict:
    """Données Mawaqit + jumua_list (horaires de jumua) et origin (live / cache). Rafraîchi si le cache a plus de max_age_h."""
    cp = Path(cache_path)
    cache = None
    if cp.exists():
        try:
            cache = json.loads(cp.read_text())
        except Exception:
            cache = None
    if cache and time.time() - cp.stat().st_mtime < max_age_h * 3600:
        cache["origin"] = "cache (frais)"
        return _finish(cache)
    try:
        data = fetch_live(url, timeout)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        data["origin"] = "live"
        return _finish(data)
    except Exception as e:  # panne réseau / format changé : on retombe sur le cache
        if cache:
            cache["origin"] = f"cache (périmé, échec live : {e})"
            return _finish(cache)
        raise


def _finish(d: dict) -> dict:
    d["jumua_list"] = [t for t in (d.get("jumua"), d.get("jumua2"), d.get("jumua3")) if t]
    return d
