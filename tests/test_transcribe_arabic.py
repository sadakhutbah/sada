"""Passages arabes rendus en français incohérent : repérés et retranscrits en arabe forcé."""
import numpy as np

from minbar.pipeline import settings as reglages
from minbar.pipeline import transcribe as T


class Word:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class Piece:
    def __init__(self, text, start, end, avg_logprob, words=()):
        self.text, self.start, self.end, self.avg_logprob = text, start, end, avg_logprob
        self.words = list(words)


class FakeModel:
    """Même surface que WhisperModel pour ce que recover_arabic utilise."""

    def __init__(self, lang="ar", prob=0.92, pieces=None, error=None):
        self.lang, self.prob, self.pieces, self.error = lang, prob, pieces or [], error
        self.detected, self.calls = [], []

    def detect_language(self, audio, **_kw):
        self.detected.append(len(audio))
        return self.lang, self.prob, [(self.lang, self.prob)]

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return iter(self.pieces), None


VERSE_AR = "كلا بل ران على قلوبهم ما كانوا يكسبون"
GARBLED = {"start": 1090.0, "end": 1098.0, "avg_logprob": -1.1,
           "text": "ça s'accumule jusqu'à ce que devenir un voie, ce qu'on appelle aran"}


def _audio(seconds=1200.0):
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def _arabic_piece():
    return [Piece(" " + VERSE_AR, 0.2, 2.6, -0.35,
                  words=[Word("كلا", 0.2, 0.6), Word("بل", 0.6, 0.9), Word("ران", 0.9, 1.4),
                         Word("على", 1.4, 1.8), Word("قلوبهم", 1.8, 2.4), Word("يكسبون", 2.4, 3.0)])]


def test_un_passage_arabe_mal_transcrit_est_remplace():
    seg = dict(GARBLED)
    model = FakeModel(pieces=_arabic_piece())
    replaced = T.recover_arabic(model, _audio(), [seg], log=lambda *_: None)

    assert replaced == 1
    assert seg["text"] == VERSE_AR
    assert seg["recovered"] == "ar"
    assert seg["avg_logprob"] == -0.35
    # bornes du segment inchangées : le montage ne bouge pas
    assert (seg["start"], seg["end"]) == (1090.0, 1098.0)
    # mots horodatés ramenés au temps de la session
    assert seg["words"][0]["w"] == "كلا" and seg["words"][0]["s"] == 1090.2
    assert seg["words"][-1]["e"] == 1093.0
    # bien appelé en arabe forcé sur l'audio du segment (8 s)
    assert model.calls[0]["language"] == "ar"
    assert model.detected[0] == 8 * 16000


def test_arabe_moins_probable_que_le_francais_laisse_le_segment():
    seg = dict(GARBLED)
    model = FakeModel(pieces=[Piece(" " + VERSE_AR, 0, 3, -1.5)])  # pire que -1.1
    assert T.recover_arabic(model, _audio(), [seg], log=lambda *_: None) == 0
    assert seg["text"] == GARBLED["text"] and "recovered" not in seg


def test_sortie_non_arabe_refusee_meme_si_probable():
    seg = dict(GARBLED)
    model = FakeModel(pieces=[Piece("something in english", 0, 3, -0.1)])
    assert T.recover_arabic(model, _audio(), [seg], log=lambda *_: None) == 0
    assert seg["text"] == GARBLED["text"]


def test_detection_en_francais_ne_declenche_aucune_retranscription():
    model = FakeModel(lang="fr", prob=0.99, pieces=_arabic_piece())
    assert T.recover_arabic(model, _audio(), [dict(GARBLED)], log=lambda *_: None) == 0
    assert model.calls == []


def test_segments_non_suspects_ne_coutent_rien():
    deja_ar = {"start": 0, "end": 4, "avg_logprob": -0.4, "text": "بسم الله الرحمن الرحيم"}
    sur = {"start": 5, "end": 8, "avg_logprob": -0.25, "text": "la patience est une lumière pour les croyants"}
    court = {"start": 9, "end": 9.4, "avg_logprob": -1.2, "text": "voie"}
    assert not T.looks_suspect(deja_ar)
    assert not T.looks_suspect(sur)
    assert not T.looks_suspect(court)
    model = FakeModel(pieces=_arabic_piece())
    assert T.recover_arabic(model, _audio(), [deja_ar, sur, court], log=lambda *_: None) == 0
    assert model.detected == [] and model.calls == []


def test_plafond_de_segments_examines():
    segs = [{"start": i * 2.0, "end": i * 2.0 + 1.5, "avg_logprob": -1.2, "text": "voie aran"} for i in range(10)]
    model = FakeModel(lang="fr", prob=0.9)
    T.recover_arabic(model, _audio(60), segs, log=lambda *_: None, max_segments=3)
    assert len(model.detected) == 3


def test_reglage_arabic_retry_prioritaire(monkeypatch, tmp_path):
    monkeypatch.setenv("MINBAR_PIPELINE_CONFIG", str(tmp_path / "pipeline.json"))
    monkeypatch.delenv("MINBAR_ARABIC_RETRY", raising=False)
    assert reglages.arabic_retry_enabled() is True
    monkeypatch.setenv("MINBAR_ARABIC_RETRY", "0")
    assert reglages.arabic_retry_enabled() is False
    assert reglages.arabic_retry_enabled(True) is True          # l'option de commande gagne
    (tmp_path / "pipeline.json").write_text('{"arabic_retry": false}', encoding="utf-8")
    monkeypatch.delenv("MINBAR_ARABIC_RETRY")
    assert reglages.arabic_retry_enabled() is False
