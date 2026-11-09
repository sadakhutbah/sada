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
    # le format est explicitement choisi : le déduire ici serait circulaire dans le test
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
    assert llm.settings(url="https://api.anthropic.com/v1/").protocol == "anthropic"   # barre finale tolérée
    assert llm.settings(url="http://127.0.0.1:8000/v1/messages").protocol == "anthropic"
    assert llm.settings(url="http://127.0.0.1:8000/v1", protocol="anthropic").protocol == "anthropic"
    assert llm.settings(url="http://127.0.0.1:11434/v1", protocol="claude-cli").label() == "claude -p"


def test_format_enregistre_ne_s_applique_qu_a_son_adresse(tmp_path, monkeypatch):
    """Le format gardé en mémoire (back-office) ne doit pas contaminer une autre adresse."""
    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({"enabled": True, "url": "http://127.0.0.1:11434/v1", "model": "m", "protocol": "openai"}))
    monkeypatch.setenv("MINBAR_LLM_CONFIG", str(cfg))
    monkeypatch.delenv("MINBAR_LLM_PROTOCOL", raising=False)
    assert llm.settings().protocol == "openai"                                   # même adresse : réglage respecté
    assert llm.settings(url="https://api.anthropic.com/v1").protocol == "anthropic"  # autre adresse : déduit


def test_environnement_prime_sur_le_defaut(monkeypatch):
    monkeypatch.setenv("MINBAR_LLM_URL", "http://127.0.0.1:8080/v1/")
    monkeypatch.setenv("MINBAR_LLM_MODEL", "mistral-small")
    s = llm.settings()
    assert s.url == "http://127.0.0.1:8080/v1"          # barre finale retirée
    assert s.label() == "openai · mistral-small"


def test_interrupteur_coupe_tout_appel(serveur, monkeypatch):
    monkeypatch.setenv("MINBAR_LLM_ENABLED", "0")
    _Handler.seen = {}
    logs = []
    assert llm.ask_json("résume", 5, logs.append, url=serveur) is None
    assert _Handler.seen == {}                      # aucune requête n'est partie
    assert "désactivé" in logs[0]


def test_interrupteur_valeurs(monkeypatch):
    for off in ("0", "off", "false", "non", " NON "):
        monkeypatch.setenv("MINBAR_LLM_ENABLED", off)
        assert llm.enabled() is False
    for on in ("1", "on", "true", "oui", ""):
        monkeypatch.setenv("MINBAR_LLM_ENABLED", on)
        assert llm.enabled() is True


def test_options_du_cli_reglent_l_interrupteur(monkeypatch):
    from types import SimpleNamespace

    from minbar.pipeline import run
    monkeypatch.setenv("MINBAR_LLM_ENABLED", "1")
    a = SimpleNamespace(llm=False, llm_url="http://127.0.0.1:9/v1", llm_model="m", llm_protocol="openai")
    run.apply_llm_options(a)
    assert llm.enabled() is False
    a.llm = True
    run.apply_llm_options(a)
    assert llm.enabled() is True
    assert llm.settings().model == "m"


def test_fichier_du_backoffice_et_priorite(serveur, tmp_path, monkeypatch):
    """Le fichier écrit par les Réglages sert de défaut ; l'environnement reste prioritaire."""
    cfg = tmp_path / "llm.json"
    monkeypatch.setenv("MINBAR_LLM_CONFIG", str(cfg))
    for var in ("MINBAR_LLM_ENABLED", "MINBAR_LLM_URL", "MINBAR_LLM_MODEL", "MINBAR_LLM_PROTOCOL"):
        monkeypatch.delenv(var, raising=False)
    llm.save_config(enabled=True, url=serveur, model="du-fichier", protocol="openai")
    assert llm.config()["model"] == "du-fichier" and llm.enabled() is True
    assert llm.label() == "openai · du-fichier"

    monkeypatch.setenv("MINBAR_LLM_MODEL", "de-l-env")           # la ligne de commande gagne
    assert llm.settings().model == "de-l-env"
    monkeypatch.delenv("MINBAR_LLM_MODEL")

    assert llm.ask_json("résume", 5, lambda m: None) == {"theme": "la patience", "points": ["a"]}

    llm.save_config(enabled=False)
    assert llm.enabled() is False
    monkeypatch.setenv("MINBAR_LLM_ENABLED", "1")                # MINBAR_LLM_ENABLED l'emporte sur le fichier
    assert llm.enabled() is True


def test_la_cle_locale_survit_a_un_enregistrement(tmp_path, monkeypatch):
    """La clé posée à la main dans le fichier n'est ni effacée ni réécrite par le formulaire ; le fichier est privé."""
    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({"enabled": True, "url": "https://api.exemple/v1", "model": "m", "key": "sk-test"}))
    monkeypatch.setenv("MINBAR_LLM_CONFIG", str(cfg))
    monkeypatch.delenv("MINBAR_LLM_API_KEY", raising=False)
    llm.save_config(enabled=True, url="https://api.exemple/v1", model="autre", protocol="openai")
    d = json.loads(cfg.read_text())
    assert d["key"] == "sk-test" and d["model"] == "autre"
    assert oct(cfg.stat().st_mode)[-3:] == "600"
    assert llm.settings().key == "sk-test"


def test_fichier_illisible_ignore(tmp_path, monkeypatch):
    cfg = tmp_path / "llm.json"
    cfg.write_text("{ pas du json")
    monkeypatch.setenv("MINBAR_LLM_CONFIG", str(cfg))
    monkeypatch.delenv("MINBAR_LLM_URL", raising=False)
    assert llm.config() == {} and llm.enabled() is True
    assert llm.settings().url == llm.DEFAULT_URL


def test_modele_absent_ne_casse_pas_le_pipeline():
    logs = []
    assert llm.ask_json("résume", 2, logs.append, url="http://127.0.0.1:9/v1") is None
    assert logs and "LLM indisponible" in logs[0]
