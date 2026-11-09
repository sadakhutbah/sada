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
from pathlib import Path

from .. import paths

DEFAULT_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
ANTHROPIC_VERSION = "2023-06-01"
CONFIG_KEYS = ("enabled", "url", "model", "protocol", "key")

# réglage propre au processus (back-office, tests) : prioritaire sur l'environnement
_OVERRIDE: dict = {}


def config_path() -> Path:
    return Path(os.environ.get("MINBAR_LLM_CONFIG") or paths.DATA / "llm.json")


def config() -> dict:
    """Réglages enregistrés depuis le back-office (Réglages → Mosquée). Fichier absent ou illisible : {}."""
    try:
        d = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: d[k] for k in CONFIG_KEYS if k in d} if isinstance(d, dict) else {}


def save_config(enabled: bool, url: str = "", model: str = "", protocol: str = "") -> None:
    """Écrit le réglage du modèle (fichier local, lisible par le pipeline lancé à côté).

    La clé d'API n'est jamais écrite ici : absente du formulaire web, elle n'est que conservée si elle a été
    posée à la main dans le fichier (droits 600).
    """
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"enabled": bool(enabled), "url": url.strip(), "model": model.strip(), "protocol": protocol.strip()}
    if (old_key := config().get("key")):
        data["key"] = old_key
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, p)


def use(**kw) -> None:
    """Réglages imposés au processus courant (prioritaires sur l'environnement et le fichier)."""
    _OVERRIDE.clear()
    _OVERRIDE.update({k: v for k, v in kw.items() if k in CONFIG_KEYS and v is not None})


def enabled() -> bool:
    """Interrupteur : réglage du processus, puis MINBAR_LLM_ENABLED, puis le fichier, sinon activé."""
    if "enabled" in _OVERRIDE:
        return bool(_OVERRIDE["enabled"])
    env = os.environ.get("MINBAR_LLM_ENABLED", "").strip().lower()
    if env:
        return env not in ("0", "off", "false", "no", "non")
    return bool(config().get("enabled", True))


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
    """Réglages effectifs : argument, réglage du processus, environnement, fichier du back-office, défaut."""
    cfg = config()
    url = (url or _OVERRIDE.get("url") or os.environ.get("MINBAR_LLM_URL") or cfg.get("url")
           or DEFAULT_URL).rstrip("/")
    model = model or _OVERRIDE.get("model") or os.environ.get("MINBAR_LLM_MODEL") or cfg.get("model") or DEFAULT_MODEL
    # Le format enregistré ne vaut que pour l'adresse enregistrée : si l'adresse change (réglage du
    # back-office, variable d'environnement, argument), le format est de nouveau déduit de l'adresse.
    cfg_proto = (cfg.get("protocol") or "").strip().lower() if (cfg.get("url") or "").rstrip("/") == url else ""
    proto = (protocol or _OVERRIDE.get("protocol") or os.environ.get("MINBAR_LLM_PROTOCOL")
             or cfg_proto or "").strip().lower() or _guess_protocol(url)
    return Settings(url=url, model=model,
                    key=key if key is not None else (os.environ.get("MINBAR_LLM_API_KEY") or cfg.get("key", "")),
                    protocol=proto)


def _guess_protocol(url: str) -> str:
    """Déduit le format d'API de l'adresse : « /messages » ou un hôte anthropic parle le format Anthropic."""
    low = (url or "").lower().rstrip("/")
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


def probe(timeout: int = 8) -> tuple[bool, str]:
    """Le modèle répond-il ? Indépendant de l'interrupteur : c'est le bouton « Tester » du back-office."""
    s = settings()
    try:
        _ask_http(s, 'Réponds uniquement {"ok": true}.', timeout, lambda m: None)
    except Exception as e:  # noqa: BLE001 — le message est montré tel quel à l'administrateur
        return False, f"{s.label()} injoignable : {e}"
    return True, f"{s.label()} répond."


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
