"""Language model: local HTTP endpoint (OpenAI and Anthropic formats), silent fallback when absent."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sada.pipeline import llm

REPLY_OPENAI = {"choices": [{"message": {"role": "assistant",
                                         "content": '```json\n{"theme": "la patience", "points": ["a"]}\n```'}}]}
REPLY_ANTHROPIC = {"content": [{"type": "text", "text": '{"theme": "la patience", "points": ["b"]}'}]}


class _Handler(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self):  # noqa: N802 — name imposed by http.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen = {"path": self.path, "body": body,
                         "headers": {k.lower(): v for k, v in self.headers.items()}}
        payload = REPLY_ANTHROPIC if self.path.endswith("/messages") else REPLY_OPENAI
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):  # silence: this is not a production server
        pass


@pytest.fixture()
def serveur():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_format_openai(serveur):
    d = llm.ask_json("résume", 5, lambda m: None, url=serveur, model="petit-modele")
    assert d == {"theme": "la patience", "points": ["a"]}
    seen = _Handler.seen
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == "petit-modele" and seen["body"]["stream"] is False
    assert seen["body"]["messages"] == [{"role": "user", "content": "résume"}]


def test_format_anthropic(serveur):
    # the format is chosen explicitly: deducing it here would be circular in the test
    d = llm.ask_json("résume", 5, lambda m: None, url=f"{serveur}/messages", model="claude-local", key="secret",
                     protocol="anthropic")
    assert d == {"theme": "la patience", "points": ["b"]}
    seen = _Handler.seen
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == "secret"
    assert seen["headers"]["anthropic-version"] == llm.ANTHROPIC_VERSION
    assert seen["body"]["max_tokens"] > 0 and "stream" not in seen["body"]


def test_protocole_deduit_de_l_url():
    assert llm.settings(url="http://127.0.0.1:11434/v1").protocol == "openai"
    assert llm.settings(url="https://api.anthropic.com/v1").protocol == "anthropic"
    assert llm.settings(url="https://api.anthropic.com/v1/").protocol == "anthropic"   # trailing slash tolerated
    assert llm.settings(url="http://127.0.0.1:8000/v1/messages").protocol == "anthropic"
    assert llm.settings(url="http://127.0.0.1:8000/v1", protocol="anthropic").protocol == "anthropic"
    assert llm.settings(url="http://127.0.0.1:11434/v1", protocol="claude-cli").label() == "claude -p"


def test_format_enregistre_ne_s_applique_qu_a_son_adresse(tmp_path, monkeypatch):
    """The format kept in memory (back-office) must not leak into another address."""
    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({"enabled": True, "url": "http://127.0.0.1:11434/v1", "model": "m", "protocol": "openai"}))
    monkeypatch.setenv("SADA_LLM_CONFIG", str(cfg))
    monkeypatch.delenv("SADA_LLM_PROTOCOL", raising=False)
    assert llm.settings().protocol == "openai"                                   # same address: saved setting honoured
    assert llm.settings(url="https://api.anthropic.com/v1").protocol == "anthropic"  # other address: deduced


def test_environnement_prime_sur_le_defaut(monkeypatch):
    monkeypatch.setenv("SADA_LLM_URL", "http://127.0.0.1:8080/v1/")
    monkeypatch.setenv("SADA_LLM_MODEL", "mistral-small")
    s = llm.settings()
    assert s.url == "http://127.0.0.1:8080/v1"          # trailing slash removed
    assert s.label() == "openai · mistral-small"


def test_interrupteur_coupe_tout_appel(serveur, monkeypatch):
    monkeypatch.setenv("SADA_LLM_ENABLED", "0")
    _Handler.seen = {}
    logs = []
    assert llm.ask_json("résume", 5, logs.append, url=serveur) is None
    assert _Handler.seen == {}                      # no request left
    assert "désactivé" in logs[0]


def test_interrupteur_valeurs(monkeypatch):
    for off in ("0", "off", "false", "non", " NON "):
        monkeypatch.setenv("SADA_LLM_ENABLED", off)
        assert llm.enabled() is False
    for on in ("1", "on", "true", "oui", ""):
        monkeypatch.setenv("SADA_LLM_ENABLED", on)
        assert llm.enabled() is True


def test_options_du_cli_reglent_l_interrupteur(monkeypatch):
    from types import SimpleNamespace

    from sada.pipeline import run
    monkeypatch.setenv("SADA_LLM_ENABLED", "1")
    a = SimpleNamespace(llm=False, llm_url="http://127.0.0.1:9/v1", llm_model="m", llm_protocol="openai")
    run.apply_llm_options(a)
    assert llm.enabled() is False
    a.llm = True
    run.apply_llm_options(a)
    assert llm.enabled() is True
    assert llm.settings().model == "m"


def test_fichier_du_backoffice_et_priorite(serveur, tmp_path, monkeypatch):
    """The file written by Settings serves as the default; the environment still takes priority."""
    cfg = tmp_path / "llm.json"
    monkeypatch.setenv("SADA_LLM_CONFIG", str(cfg))
    for var in ("SADA_LLM_ENABLED", "SADA_LLM_URL", "SADA_LLM_MODEL", "SADA_LLM_PROTOCOL"):
        monkeypatch.delenv(var, raising=False)
    llm.save_config(enabled=True, url=serveur, model="du-fichier", protocol="openai")
    assert llm.config()["model"] == "du-fichier" and llm.enabled() is True
    assert llm.label() == "openai · du-fichier"

    monkeypatch.setenv("SADA_LLM_MODEL", "de-l-env")           # the command line wins
    assert llm.settings().model == "de-l-env"
    monkeypatch.delenv("SADA_LLM_MODEL")

    assert llm.ask_json("résume", 5, lambda m: None) == {"theme": "la patience", "points": ["a"]}

    llm.save_config(enabled=False)
    assert llm.enabled() is False
    monkeypatch.setenv("SADA_LLM_ENABLED", "1")                # SADA_LLM_ENABLED takes precedence over the file
    assert llm.enabled() is True


def test_la_cle_locale_survit_a_un_enregistrement(tmp_path, monkeypatch):
    """The key set by hand in the file is neither erased nor rewritten by the form; the file is private."""
    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({"enabled": True, "url": "https://api.exemple/v1", "model": "m", "key": "sk-test"}))
    monkeypatch.setenv("SADA_LLM_CONFIG", str(cfg))
    monkeypatch.delenv("SADA_LLM_API_KEY", raising=False)
    llm.save_config(enabled=True, url="https://api.exemple/v1", model="autre", protocol="openai")
    d = json.loads(cfg.read_text())
    assert d["key"] == "sk-test" and d["model"] == "autre"
    assert oct(cfg.stat().st_mode)[-3:] == "600"
    assert llm.settings().key == "sk-test"


def test_fichier_illisible_ignore(tmp_path, monkeypatch):
    cfg = tmp_path / "llm.json"
    cfg.write_text("{ pas du json")
    monkeypatch.setenv("SADA_LLM_CONFIG", str(cfg))
    monkeypatch.delenv("SADA_LLM_URL", raising=False)
    assert llm.config() == {} and llm.enabled() is True
    assert llm.settings().url == llm.DEFAULT_URL


def test_modele_absent_ne_casse_pas_le_pipeline():
    logs = []
    assert llm.ask_json("résume", 2, logs.append, url="http://127.0.0.1:9/v1") is None
    assert logs and "LLM indisponible" in logs[0]
