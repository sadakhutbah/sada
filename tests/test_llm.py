"""Modèle de langage : endpoint HTTP local (formats OpenAI et Anthropic), repli silencieux s'il est absent."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from minbar.pipeline import llm

REPLY_OPENAI = {"choices": [{"message": {"role": "assistant",
                                         "content": '```json\n{"theme": "la patience", "points": ["a"]}\n```'}}]}
REPLY_ANTHROPIC = {"content": [{"type": "text", "text": '{"theme": "la patience", "points": ["b"]}'}]}


class _Handler(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self):  # noqa: N802 — nom imposé par http.server
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

    def log_message(self, format, *args):  # silence : ce n'est pas un serveur de production
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
    d = llm.ask_json("résume", 5, lambda m: None, url=f"{serveur}/messages", model="claude-local", key="secret")
    assert d == {"theme": "la patience", "points": ["b"]}
    seen = _Handler.seen
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == "secret"
    assert seen["headers"]["anthropic-version"] == llm.ANTHROPIC_VERSION
    assert seen["body"]["max_tokens"] > 0 and "stream" not in seen["body"]


def test_protocole_deduit_de_l_url():
    assert llm.settings(url="http://127.0.0.1:11434/v1").protocol == "openai"
    assert llm.settings(url="https://api.anthropic.com/v1").protocol == "anthropic"
    assert llm.settings(url="http://127.0.0.1:8000/v1", protocol="anthropic").protocol == "anthropic"
    assert llm.settings(url="http://127.0.0.1:11434/v1", protocol="claude-cli").label() == "claude -p"


def test_environnement_prime_sur_le_defaut(monkeypatch):
    monkeypatch.setenv("MINBAR_LLM_URL", "http://127.0.0.1:8080/v1/")
    monkeypatch.setenv("MINBAR_LLM_MODEL", "mistral-small")
    s = llm.settings()
    assert s.url == "http://127.0.0.1:8080/v1"          # barre finale retirée
    assert s.label() == "openai · mistral-small"


def test_modele_absent_ne_casse_pas_le_pipeline():
    logs = []
    assert llm.ask_json("résume", 2, logs.append, url="http://127.0.0.1:9/v1") is None
    assert logs and "LLM indisponible" in logs[0]
