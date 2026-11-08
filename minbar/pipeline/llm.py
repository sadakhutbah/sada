"""Appel au LLM local (`claude -p`) : renvoie l'objet JSON de la réponse, ou None si indisponible.

Le pipeline a toujours un repli sans LLM (résumé extractif, traductions fixes) : un échec n'est pas une erreur."""
from __future__ import annotations
import json
import re
import shutil
import subprocess


def ask_json(prompt: str, timeout: int, log=print) -> dict | None:
    exe = shutil.which("claude")
    if not exe:
        log("LLM : claude introuvable")
        return None
    cmd = [exe, "-p", "--output-format", "text"]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            log(f"LLM : claude -p a échoué ({r.returncode}) {r.stderr[:300]}")
            return None
        m = re.search(r"\{.*\}", r.stdout, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:  # noqa: BLE001
        log(f"LLM indisponible : {e}")
        return None
