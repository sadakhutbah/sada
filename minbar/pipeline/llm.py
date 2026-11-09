"""Appel au modèle de langage : endpoint OpenAI ou Anthropic, local par défaut.

Rien à installer à côté : MINBAR_LLM_URL pointe vers un serveur compatible (Ollama, llama.cpp, LM Studio,
vLLM, ou une API Anthropic). Sans variable, on vise Ollama en local ; si rien ne répond, l'appel renvoie None
et le pipeline continue avec ses replis (résumé extractif, traductions fixes).

    MINBAR_LLM_URL        http://127.0.0.1:11434/v1        (racine de l'API)
    MINBAR_LLM_MODEL      qwen2.5:7b-instruct
    MINBAR_LLM_API_KEY    (vide en local)
    MINBAR_LLM_PROTOCOL   openai | anthropic | claude-cli  (déduit de l'URL par défaut)
    MINBAR_LLM_TIMEOUT    délai en secondes (défaut : 420 pour le résumé, 600 pour la traduction)

`claude-cli` reste disponible pour l'ancien fonctionnement (binaire `claude -p` lancé en sous-processus) :
ce n'est plus le défaut, il faut le demander explicitement.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
ANTHROPIC_VERSION = "2023-06-01"


def enabled() -> bool:
    """Interrupteur global (MINBAR_LLM_ENABLED) : à 0, aucun appel au modèle, quelle que soit l'URL."""
    return os.environ.get("MINBAR_LLM_ENABLED", "").strip().lower() not in ("0", "off", "false", "no", "non")


def label() -> str:
    """Nom court de la méthode effective (journal de démarrage du pipeline)."""
    return settings().label()


@dataclass(frozen=True)
class Settings:
    url: str
    model: str
    key: str
    protocol: str

    def label(self) -> str:
        """Nom court de la méthode, écrit dans les notes et le cache de traduction."""
        if self.protocol == "claude-cli":
            return "claude -p"
        return f"{self.protocol} · {self.model}"


def settings(url: str | None = None, model: str | None = None, key: str | None = None,
             protocol: str | None = None) -> Settings:
    """Réglages effectifs : argument, puis variable d'environnement, puis défaut local."""
    url = (url or os.environ.get("MINBAR_LLM_URL") or DEFAULT_URL).rstrip("/")
    proto = (protocol or os.environ.get("MINBAR_LLM_PROTOCOL") or "").strip().lower() or _guess_protocol(url)
    return Settings(url=url, model=model or os.environ.get("MINBAR_LLM_MODEL") or DEFAULT_MODEL,
                    key=key if key is not None else os.environ.get("MINBAR_LLM_API_KEY", ""), protocol=proto)


def _guess_protocol(url: str) -> str:
    low = url.lower()
    return "anthropic" if "anthropic" in low or low.endswith("/messages") else "openai"


def ask_json(prompt: str, timeout: int, log=print, *, url=None, model=None, key=None, protocol=None) -> dict | None:
    """Réponse JSON du modèle, ou None s'il est indisponible (le pipeline a toujours un repli)."""
    if not enabled():
        log("LLM désactivé (MINBAR_LLM_ENABLED=0) : repli sans modèle")
        return None
    s = settings(url, model, key, protocol)
    try:
        text = _ask_cli(prompt, timeout, log) if s.protocol == "claude-cli" else _ask_http(s, prompt, timeout, log)
    except Exception as e:  # noqa: BLE001 — un modèle absent ou lent ne doit pas casser le pipeline
        log(f"LLM indisponible ({s.label()}) : {e}")
        return None
    if not text:
        return None
    d = _json_object(text)
    if d is None:
        log(f"LLM : réponse sans objet JSON ({text[:200]!r})")
    return d


def _json_object(text: str) -> dict | None:
    """Objet JSON de la réponse : la réponse entière si possible, sinon le premier bloc {...}."""
    try:
        d = json.loads(text)
        return d if isinstance(d, dict) else None
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _post(url: str, body: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ask_http(s: Settings, prompt: str, timeout: int, log) -> str:
    """Un aller-retour HTTP, au format du protocole choisi."""
    if s.protocol == "anthropic":
        url = s.url if s.url.endswith("/messages") else f"{s.url}/messages"
        headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        if s.key:
            headers["x-api-key"] = s.key
        body = {"model": s.model, "max_tokens": 8192, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        d = _post(url, body, headers, timeout)
        return "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
    url = s.url if s.url.endswith("/chat/completions") else f"{s.url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if s.key:
        headers["Authorization"] = f"Bearer {s.key}"
    body = {"model": s.model, "temperature": 0, "stream": False,
            "messages": [{"role": "user", "content": prompt}]}
    d = _post(url, body, headers, timeout)
    choices = d.get("choices") or []
    if not choices:
        raise RuntimeError(f"réponse sans « choices » : {json.dumps(d)[:200]}")
    return (choices[0].get("message") or {}).get("content") or ""


def _ask_cli(prompt: str, timeout: int, log) -> str:
    """Ancien fonctionnement : binaire `claude` en sous-processus (désactivé par défaut)."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("binaire « claude » introuvable")
    r = subprocess.run([exe, "-p", "--output-format", "text"], input=prompt, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p a échoué ({r.returncode}) {r.stderr[:300]}")
    return r.stdout
