"""Language-model call: OpenAI or Anthropic endpoint, local by default.

Nothing to install on the side: MINBAR_LLM_URL points to a compatible server (Ollama, llama.cpp, LM Studio,
vLLM, or an Anthropic API). Without the variable, we target a local Ollama; if nothing answers, the call returns None
and the pipeline goes on with its fallbacks (extractive summary, fixed translations).

    MINBAR_LLM_URL        http://127.0.0.1:11434/v1        (API root)
    MINBAR_LLM_MODEL      qwen2.5:7b-instruct
    MINBAR_LLM_API_KEY    (empty locally)
    MINBAR_LLM_PROTOCOL   openai | anthropic | claude-cli  (deduced from the URL by default)
    MINBAR_LLM_TIMEOUT    timeout in seconds (default: 420 for the summary, 600 for the translation)

`claude-cli` stays available for the old behaviour (`claude -p` binary run as a subprocess):
it is no longer the default, it must be requested explicitly.
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

# process-local setting (back-office, tests): takes precedence over the environment
_OVERRIDE: dict = {}


def config_path() -> Path:
    return Path(os.environ.get("MINBAR_LLM_CONFIG") or paths.DATA / "llm.json")


def config() -> dict:
    """Settings saved from the back-office (Settings → Mosque). File missing or unreadable: {}."""
    try:
        d = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: d[k] for k in CONFIG_KEYS if k in d} if isinstance(d, dict) else {}


def save_config(enabled: bool, url: str = "", model: str = "", protocol: str = "") -> None:
    """Write the model setting (local file, readable by the pipeline started alongside).

    The API key is never written here: absent from the web form, it is only kept if it was
    set by hand in the file (permissions 600).
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
    """Settings forced on the current process (take precedence over the environment and the file)."""
    _OVERRIDE.clear()
    _OVERRIDE.update({k: v for k, v in kw.items() if k in CONFIG_KEYS and v is not None})


def enabled() -> bool:
    """Switch: process setting, then MINBAR_LLM_ENABLED, then the file, otherwise enabled."""
    if "enabled" in _OVERRIDE:
        return bool(_OVERRIDE["enabled"])
    env = os.environ.get("MINBAR_LLM_ENABLED", "").strip().lower()
    if env:
        return env not in ("0", "off", "false", "no", "non")
    return bool(config().get("enabled", True))


def label() -> str:
    """Short name of the effective method (pipeline startup log)."""
    return settings().label()


@dataclass(frozen=True)
class Settings:
    url: str
    model: str
    key: str
    protocol: str

    def label(self) -> str:
        """Short name of the method, written into the notes and the translation cache."""
        if self.protocol == "claude-cli":
            return "claude -p"
        return f"{self.protocol} · {self.model}"


def settings(url: str | None = None, model: str | None = None, key: str | None = None,
             protocol: str | None = None) -> Settings:
    """Effective settings: argument, process setting, environment, back-office file, default."""
    cfg = config()
    url = (url or _OVERRIDE.get("url") or os.environ.get("MINBAR_LLM_URL") or cfg.get("url")
           or DEFAULT_URL).rstrip("/")
    model = model or _OVERRIDE.get("model") or os.environ.get("MINBAR_LLM_MODEL") or cfg.get("model") or DEFAULT_MODEL
    # The saved protocol only holds for the saved address: if the address changes (back-office
    # setting, environment variable, argument), the protocol is deduced again from the address.
    cfg_proto = (cfg.get("protocol") or "").strip().lower() if (cfg.get("url") or "").rstrip("/") == url else ""
    proto = (protocol or _OVERRIDE.get("protocol") or os.environ.get("MINBAR_LLM_PROTOCOL")
             or cfg_proto or "").strip().lower() or _guess_protocol(url)
    return Settings(url=url, model=model,
                    key=key if key is not None else (os.environ.get("MINBAR_LLM_API_KEY") or cfg.get("key", "")),
                    protocol=proto)


def _guess_protocol(url: str) -> str:
    """Deduce the API format from the address: « /messages » or an anthropic host speaks the Anthropic format."""
    low = (url or "").lower().rstrip("/")
    return "anthropic" if "anthropic" in low or low.endswith("/messages") else "openai"


def ask_json(prompt: str, timeout: int, log=print, *, url=None, model=None, key=None, protocol=None) -> dict | None:
    """JSON response from the model, or None if it is unavailable (the pipeline always has a fallback)."""
    if not enabled():
        log("LLM désactivé (MINBAR_LLM_ENABLED=0) : repli sans modèle")
        return None
    s = settings(url, model, key, protocol)
    try:
        text = _ask_cli(prompt, timeout, log) if s.protocol == "claude-cli" else _ask_http(s, prompt, timeout, log)
    except Exception as e:  # noqa: BLE001 — a missing or slow model must not break the pipeline
        log(f"LLM indisponible ({s.label()}) : {e}")
        return None
    if not text:
        return None
    d = _json_object(text)
    if d is None:
        log(f"LLM : réponse sans objet JSON ({text[:200]!r})")
    return d


def _json_object(text: str) -> dict | None:
    """JSON object of the response: the whole response if possible, otherwise the first {...} block."""
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
    """One HTTP round trip, in the format of the chosen protocol."""
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
    """Does the model answer? Independent of the switch: this is the « Test » button of the back-office."""
    s = settings()
    try:
        _ask_http(s, 'Réponds uniquement {"ok": true}.', timeout, lambda m: None)
    except Exception as e:  # noqa: BLE001 — the message is shown as is to the administrator
        return False, f"{s.label()} injoignable : {e}"
    return True, f"{s.label()} répond."


def _ask_cli(prompt: str, timeout: int, log) -> str:
    """Old behaviour: `claude` binary as a subprocess (disabled by default)."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("binaire « claude » introuvable")
    r = subprocess.run([exe, "-p", "--output-format", "text"], input=prompt, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p a échoué ({r.returncode}) {r.stderr[:300]}")
    return r.stdout
