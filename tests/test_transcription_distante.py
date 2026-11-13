"""Transcription distante : découpage, recollage des horodatages, réessais, réglage, écran.

Un faux serveur compatible OpenAI tient le rôle du fournisseur : aucun appel réseau réel, aucune clé.
"""

import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sada.pipeline import remote as R

pytest.importorskip("fastapi")


class _Handler(BaseHTTPRequestHandler):
    """Réponses servies dans l'ordre de `plan` (la dernière se répète), corps conservé pour contrôle."""

    plan: list = []
    seen: list = []

    def do_POST(self):  # noqa: N802 — nom imposé par http.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        _Handler.seen.append(
            {
                "path": self.path,
                "body": body,
                "auth": self.headers.get("Authorization"),
                "type": self.headers.get("Content-Type"),
            }
        )
        status, payload = _Handler.plan[min(len(_Handler.seen) - 1, len(_Handler.plan) - 1)]
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):  # silence: ce n'est pas un serveur de production
        pass


@pytest.fixture()
def serveur():
    _Handler.plan = [(200, {"text": ""})]
    _Handler.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def _wav(path, seconds: float, rate: int = 16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def _reglage(monkeypatch, url: str, *, mode: str = "distant", model: str = "whisper-test", key: str = "sk-secret"):
    monkeypatch.setenv("SADA_TRANSCRIPTION_MODE", mode)
    monkeypatch.setenv("SADA_TRANSCRIPTION_PROVIDER", "custom")
    monkeypatch.setenv("SADA_TRANSCRIPTION_URL", url)
    monkeypatch.setenv("SADA_TRANSCRIPTION_MODEL", model)
    monkeypatch.setenv("SADA_TRANSCRIPTION_KEY", key)
    monkeypatch.setenv("SADA_LANG", "fr")  # messages du journal en français, comme chez la mosquée


def _mot(word, start, end):
    return {"word": word, "start": start, "end": end}


# ------------------------------------------------------------------ découpage


def test_le_decoupage_reste_sous_la_limite_et_se_chevauche():
    plan = R.chunk_plan(100.0, max_bytes=R.BYTES_PER_SECOND * 20)
    assert plan[0] == (0.0, 20.0)
    assert all(length <= 20.0 for _, length in plan)
    for (debut, _longueur), (suivant, _) in zip(plan, plan[1:], strict=False):
        assert suivant == pytest.approx(debut + 20.0 - R.OVERLAP_S)  # léger chevauchement, aucun trou
    dernier_debut, derniere_longueur = plan[-1]
    assert dernier_debut + derniere_longueur == pytest.approx(100.0)


def test_la_limite_de_taille_borne_la_duree_d_un_morceau():
    # 6 Mo à 6 ko/s : 16 min de mp3, chevauchement de 2 s compris
    plan = R.chunk_plan(3600.0, max_bytes=6_000_000)
    assert max(length for _, length in plan) == pytest.approx(1000.0)
    assert len(plan) == 4  # 3600 s / 998 s de pas


# ------------------------------------------------------------------ recollage


def test_recollage_des_horodatages_et_dedoublonnage_du_chevauchement(monkeypatch, serveur, tmp_path):
    monkeypatch.setattr(R, "MAX_CHUNK_S", 10.0)  # 15 s -> deux morceaux, décalés de 8 s
    _Handler.plan = [
        (
            200,
            {
                "language": "fr",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 2.0,
                        "text": " Première phrase.",
                        "avg_logprob": -0.2,
                        "words": [_mot("Première", 0.0, 1.0), _mot("phrase.", 1.0, 2.0)],
                    },
                    {
                        "start": 2.0,
                        "end": 6.0,
                        "text": " Deuxième phrase.",
                        "avg_logprob": -0.25,
                        "words": [_mot("Deuxième", 2.0, 4.0), _mot("phrase.", 4.0, 6.0)],
                    },
                    # dernier segment du morceau, mots à la racine de la réponse (comme Groq)
                    {"start": 6.0, "end": 9.9, "text": " À cheval.", "avg_logprob": -0.3},
                ],
                "words": [_mot("À", 6.0, 7.0), _mot("cheval.", 7.0, 9.9)],
            },
        ),
        (
            200,
            {
                "language": "fr",
                "segments": [
                    # redite intégrale du chevauchement : écartée
                    {
                        "start": 0.0,
                        "end": 1.9,
                        "text": " À cheval.",
                        "avg_logprob": -0.4,
                        "words": [_mot("À", 0.0, 0.9), _mot("cheval.", 0.9, 1.9)],
                    },
                    # segment à cheval sur la coupure : on ne garde que les mots non encore transcrits
                    {
                        "start": 1.5,
                        "end": 5.0,
                        "text": " À cheval. Suite du propos.",
                        "avg_logprob": -0.35,
                        "words": [
                            _mot("À", 1.5, 1.9),
                            _mot("cheval.", 1.9, 2.0),
                            _mot("Suite", 2.0, 2.5),
                            _mot("du", 2.5, 2.8),
                            _mot("propos.", 2.8, 5.0),
                        ],
                    },
                ],
            },
        ),
    ]
    _reglage(monkeypatch, serveur)
    wav = _wav(tmp_path / "audio.wav", 15.0)
    logs = []
    res = R.transcribe(str(wav), str(tmp_path / "t.json"), log=logs.append, arabic_retry=False)

    assert len(_Handler.seen) == 2
    segs = res["segments"]
    assert [s["text"] for s in segs] == ["Première phrase.", "Deuxième phrase.", "À cheval.", "Suite du propos."]
    assert [s["start"] for s in segs] == [0.0, 2.0, 6.0, 10.0]
    assert [s["end"] for s in segs] == [2.0, 6.0, 9.9, 13.0]
    # aucun chevauchement dans le résultat : les sous-titres ne se superposent jamais
    for avant, apres in zip(segs, segs[1:], strict=False):
        assert apres["start"] >= avant["end"] - 1e-9
    assert segs[3]["words"] == [
        {"w": "Suite", "s": 10.0, "e": 10.5},
        {"w": "du", "s": 10.5, "e": 10.8},
        {"w": "propos.", "s": 10.8, "e": 13.0},
    ]
    # mots du dernier segment du premier morceau : repris de la racine de la réponse, décalés
    assert [w["s"] for w in segs[2]["words"]] == [6.0, 7.0]
    # fichier écrit, relisible, et journal lisible
    ecrit = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert ecrit["segments"] == segs
    assert any("morceau 2/2" in ligne for ligne in logs)
    assert res["language"] == "fr" and res["duration"] == pytest.approx(15.0)

    # ce qui part réellement au fournisseur
    body = _Handler.seen[0]["body"]
    assert _Handler.seen[0]["path"] == "/v1/audio/transcriptions"
    assert _Handler.seen[0]["auth"] == "Bearer sk-secret"
    assert _Handler.seen[0]["type"].startswith("multipart/form-data")
    assert b'name="model"' in body and b"whisper-test" in body
    assert b"verbose_json" in body
    assert body.count(b'name="timestamp_granularities[]"') == 2
    assert b"segment" in body and b"word" in body
    assert b'name="language"' not in body  # langue inconnue : rien n'est imposé
    assert b"audio/mpeg" in body


# ------------------------------------------------------------------ forme identique au local


class _Piece:
    def __init__(self, text, start, end, avg_logprob, words=()):
        self.text, self.start, self.end, self.avg_logprob = text, start, end, avg_logprob
        self.words = list(words)


class _Mot:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class _Info:
    language, language_probability, duration = "fr", 0.97, 15.0


class _Model:
    def __init__(self, name, **kw):
        pass

    def transcribe(self, audio, **kw):
        return iter([_Piece(" Bonjour tout le monde.", 0.0, 2.0, -0.21, [_Mot("Bonjour", 0.0, 1.0)])]), _Info()


def test_meme_forme_que_la_transcription_locale(monkeypatch, serveur, tmp_path):
    import faster_whisper

    from sada.pipeline import transcribe as T

    wav = _wav(tmp_path / "audio.wav", 15.0)
    monkeypatch.setattr(faster_whisper, "WhisperModel", _Model)
    monkeypatch.setattr(T, "pick_model", lambda prefer, log: ("tiny", "cpu", "int8"))
    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.delenv("SADA_TRANSCRIPTION_MODE", raising=False)
    local = T.transcribe(str(wav), str(tmp_path / "local.json"), "cpu", log=lambda *_: None, arabic_retry=False)
    assert local["segments"][0]["text"] == "Bonjour tout le monde."

    _Handler.plan = [
        (
            200,
            {
                "language": "fr",
                "duration": 15.0,
                "segments": [
                    {
                        "start": 0.0,
                        "end": 2.0,
                        "text": " Bonjour tout le monde.",
                        "avg_logprob": -0.21,
                        "words": [
                            _mot("Bonjour", 0.0, 1.0),
                            _mot("tout", 1.0, 1.5),
                            _mot("le", 1.5, 1.7),
                            _mot("monde.", 1.7, 2.0),
                        ],
                    },
                ],
            },
        )
    ]
    _reglage(monkeypatch, serveur)
    distant = T.transcribe(str(wav), str(tmp_path / "distant.json"), "cpu", log=lambda *_: None, arabic_retry=False)

    assert set(distant) == set(local)  # mêmes clés de premier niveau
    assert set(distant["segments"][0]) == set(local["segments"][0]) | {"words"}
    assert set(distant["segments"][0]["words"][0]) == {"w", "s", "e"}
    assert distant["duration"] == pytest.approx(local["duration"])
    assert distant["language"] == local["language"]
    assert distant["hotwords"] is True and local["hotwords"] is True
    assert all(isinstance(s["start"], float) and isinstance(s["end"], float) for s in distant["segments"])


# ------------------------------------------------------------------ réessais et erreurs


def test_reessai_sur_429_puis_succes(monkeypatch, serveur, tmp_path):
    monkeypatch.setattr(R, "BACKOFF_S", 0.0)
    _Handler.plan = [
        (429, {"error": {"message": "rate limit"}}),
        (429, {"error": {"message": "rate limit"}}),
        (200, {"language": "fr", "segments": [{"start": 0.0, "end": 2.0, "text": " Ça marche.", "avg_logprob": -0.3}]}),
    ]
    _reglage(monkeypatch, serveur)
    wav = _wav(tmp_path / "audio.wav", 4.0)
    logs = []
    res = R.transcribe(str(wav), str(tmp_path / "t.json"), log=logs.append, arabic_retry=False)
    assert len(_Handler.seen) == 3
    assert res["segments"][0]["text"] == "Ça marche."
    assert any("429" in ligne and "nouvel essai" in ligne for ligne in logs)


def test_erreur_claire_apres_trop_d_essais_et_aucun_repli_local(monkeypatch, serveur, tmp_path):
    import faster_whisper

    from sada.pipeline import transcribe as T

    monkeypatch.setattr(R, "BACKOFF_S", 0.0)
    _Handler.plan = [(503, {"error": {"message": "surcharge"}})]
    _reglage(monkeypatch, serveur)

    def _interdit(*a, **k):
        raise AssertionError("le mode distant ne doit jamais retomber sur Whisper local")

    monkeypatch.setattr(faster_whisper, "WhisperModel", _interdit)
    wav = _wav(tmp_path / "audio.wav", 4.0)
    logs = []
    with pytest.raises(R.TranscriptionError) as exc:
        T.transcribe(str(wav), str(tmp_path / "t.json"), "cpu", log=logs.append, arabic_retry=False)
    assert len(_Handler.seen) == R.MAX_ATTEMPTS
    message = str(exc.value)
    assert "503" in message and "surcharge" in message
    assert any("503" in ligne for ligne in logs)
    assert not (tmp_path / "t.json").exists()


def test_cle_absente_de_la_requete_et_erreur_http_relayee(monkeypatch, serveur, tmp_path):
    _Handler.plan = [(401, {"error": {"message": "Invalid API Key"}})]
    _reglage(monkeypatch, serveur, key="")
    wav = _wav(tmp_path / "audio.wav", 4.0)
    with pytest.raises(R.TranscriptionError) as exc:
        R.transcribe(str(wav), str(tmp_path / "t.json"), log=lambda *_: None, arabic_retry=False)
    assert "401" in str(exc.value) and "Invalid API Key" in str(exc.value)
    assert _Handler.seen[0]["auth"] is None  # pas d'en-tête vide, pas de clé inventée


def test_mode_local_par_defaut_ne_part_pas_sur_le_reseau(monkeypatch, serveur, tmp_path):
    import faster_whisper

    from sada.pipeline import transcribe as T

    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "absent.json"))
    for nom in ("SADA_TRANSCRIPTION_MODE", "SADA_TRANSCRIPTION_URL", "SADA_TRANSCRIPTION_MODEL"):
        monkeypatch.delenv(nom, raising=False)
    monkeypatch.setattr(faster_whisper, "WhisperModel", _Model)
    monkeypatch.setattr(_Info, "duration", 15.0)
    monkeypatch.setattr(T, "pick_model", lambda prefer, log: ("tiny", "cpu", "int8"))
    wav = _wav(tmp_path / "audio.wav", 15.0)
    res = T.transcribe(str(wav), str(tmp_path / "t.json"), "cpu", log=lambda *_: None, arabic_retry=False)
    assert res["device"] == "cpu" and res["compute_type"] == "int8"
    assert _Handler.seen == []


# ------------------------------------------------------------------ réglage et écran


def test_le_reglage_ne_renvoie_jamais_la_cle(env, monkeypatch, tmp_path):
    from test_backoffice import client

    from sada.backoffice import db

    cfg = tmp_path / "transcription.json"
    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(cfg))
    for nom in (
        "SADA_TRANSCRIPTION_MODE",
        "SADA_TRANSCRIPTION_URL",
        "SADA_TRANSCRIPTION_MODEL",
        "SADA_TRANSCRIPTION_KEY",
    ):
        monkeypatch.delenv(nom, raising=False)
    c = client(env, "admin")
    assert c.get("/api/v1/settings").json()["transcription"]["mode"] == "local"  # défaut : local

    r = c.ecrire(
        "/settings/transcription",
        {"mode": "distant", "provider": "groq", "model": "whisper-large-v3", "key": "sk-tres-secret"},
        methode="PUT",
    )
    assert r.status_code == 200, r.text
    vue = r.json()["transcription"]
    assert vue["mode"] == "distant" and vue["has_key"] is True and "key" not in vue
    assert "sk-tres-secret" not in r.text
    assert json.loads(cfg.read_text(encoding="utf-8"))["key"] == "sk-tres-secret"  # gardée côté serveur
    assert oct(cfg.stat().st_mode)[-3:] == "600"

    ecran = c.get("/api/v1/settings").json()["transcription"]
    assert ecran["has_key"] is True and "key" not in ecran and "sk-tres-secret" not in json.dumps(ecran)
    assert ecran["providers"] and {p["key"] for p in ecran["providers"]} == {"groq", "openai"}
    assert ecran["url"] == ""  # aucune adresse propre : c'est celle du fournisseur qui sert
    assert ecran["price_per_hour"] == pytest.approx(0.111)
    assert db.one(env["conn"], "SELECT COUNT(*) n FROM audit WHERE action = 'transcription.configure'")["n"] == 1
    # le masque renvoyé tel quel garde la clé enregistrée
    r = c.ecrire(
        "/settings/transcription",
        {"mode": "distant", "provider": "openai", "model": "whisper-1", "key": db.MASK},
        methode="PUT",
    )
    assert r.status_code == 200 and r.json()["transcription"]["has_key"] is True
    assert json.loads(cfg.read_text(encoding="utf-8"))["key"] == "sk-tres-secret"

    # adresse personnalisée : exige une adresse et un modèle
    r = c.ecrire(
        "/settings/transcription", {"mode": "distant", "provider": "custom", "url": "", "model": "x"}, methode="PUT"
    )
    assert r.status_code == 422 and "http" in r.json()["message"]
    r = c.ecrire(
        "/settings/transcription",
        {"mode": "distant", "provider": "custom", "url": "https://exemple/v1", "model": ""},
        methode="PUT",
    )
    assert r.status_code == 422 and "model" in r.json()["message"].lower()
    r = c.ecrire("/settings/transcription", {"mode": "magique", "provider": "groq"}, methode="PUT")
    assert r.status_code == 422


def test_endpoint_de_test_envoie_un_extrait_genere(env, monkeypatch, serveur, tmp_path):
    from test_backoffice import client

    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "transcription.json"))
    _reglage(monkeypatch, serveur, key="sk-test")
    _Handler.plan = [
        (200, {"language": "en", "text": " Bonjour", "segments": [{"start": 0, "end": 1, "text": " Bonjour"}]})
    ]
    r = client(env, "admin").ecrire("/settings/transcription/test", {}, methode="POST")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and "Bonjour" in r.json()["message"]
    assert len(_Handler.seen) == 1
    assert _Handler.seen[0]["auth"] == "Bearer sk-test"
    assert b"audio/mpeg" in _Handler.seen[0]["body"]  # l'extrait a bien été joint
    assert "sk-test" not in r.text


def test_endpoint_de_test_signale_un_refus(env, monkeypatch, serveur, tmp_path):
    from test_backoffice import client

    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "transcription.json"))
    _reglage(monkeypatch, serveur)
    _Handler.plan = [(401, {"error": {"message": "Invalid API Key"}})]
    r = client(env, "admin").ecrire("/settings/transcription/test", {}, methode="POST")
    assert r.status_code == 422
    assert r.json()["ok"] is False and "Invalid API Key" in r.json()["message"]


def test_le_reglage_est_reserve_a_l_admin(env, monkeypatch, tmp_path):
    from test_backoffice import client

    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "transcription.json"))
    for role in ("imam", "responsable"):
        c = client(env, role)
        assert (
            c.ecrire("/settings/transcription", {"mode": "local", "provider": "groq"}, methode="PUT").status_code == 403
        )
        assert c.ecrire("/settings/transcription/test", {}, methode="POST").status_code == 403
