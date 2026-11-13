"""Hallucinations de Whisper : formules de générique, silence, et reprise qui les réintroduit.

Textes et scores repris de l'essai réel Groq whisper-large-v3 du 13/11 sur la khutba de 549 s :
l'enregistrement commence par 68,6 s de silence numérique (`silencedetect` : -91 dB), Whisper y a
écrit « Al Fatiha », puis, forcé en arabe sur ces fenêtres muettes, « ترجمة نانسي قنقر » et
« اشتركوا في القناة » — acceptés comme des reprises normales parce que leur vraisemblance est
excellente (-0.126, -0.055). Aucun appel réseau : un faux serveur compatible OpenAI tient le rôle
du fournisseur, un moteur de rejeu tient celui du moteur sur les plages reprises.
"""

import array
import json
import math
import re
import wave

import pytest
from test_transcription_distante import _Handler, _reglage, serveur  # noqa: F401 — fixtures partagées

from sada.pipeline import remote as R
from sada.pipeline import repair

pytest.importorskip("fastapi")

# Fenêtres de 30 s rendues par Groq sur les 68,6 s muettes, puis l'arabe translittéré à partir de
# 116 s. Bornes et scores de la réponse réelle.
GROQ = [
    {"start": 0.0, "end": 29.98, "text": "Al Fatiha", "avg_logprob": -0.579},
    {"start": 30.0, "end": 59.98, "text": "Al Fatiha", "avg_logprob": -0.579},
    {"start": 60.0, "end": 89.98, "text": "Al-Fatiha", "avg_logprob": -0.409},
    {
        "start": 116.08,
        "end": 121.28,
        "text": "awaluhaa nafadu al-mashia'a ilaahi al-mutlaqa",
        "avg_logprob": -0.363,
    },
    {"start": 125.28, "end": 130.2, "text": "wala yumkinu la ayi shay'in an yajiza amra", "avg_logprob": -0.363},
    {
        "start": 131.2,
        "end": 142.64,
        "text": "faindama yuqadiru Allah subhanahu wa ta amran fa innahu yatahqaqu bi asbabin",
        "avg_logprob": -0.038,
    },
]

# Ce que l'arabe forcé a rendu, plage par plage (temps relatifs à la plage).
ARABE_MUET = [
    {"text": "ترجمة نانسي قنقر", "avg_logprob": -0.126, "start": 0.0, "end": 29.98},
    {"text": "ترجمة نانسي قنقر", "avg_logprob": -0.126, "start": 30.0, "end": 59.98},
    {"text": "اشتركوا في القناة", "avg_logprob": -0.055, "start": 60.0, "end": 89.98},
]
ARABE_TRANSLIT = [
    {"text": "أولها نفاد المشيئة الإلهية المطلقة", "avg_logprob": -0.106, "start": 0.0, "end": 5.2},
    {"text": "ولا يمكن لأي شيء أن يعجز أمره", "avg_logprob": -0.106, "start": 9.2, "end": 14.92},
    {
        "text": "فعندما يقدر الله سبحانه وتعالى أمرا فإنه يتحقق بأسباب وطرق لا نتوقعها",
        "avg_logprob": -0.106,
        "start": 15.12,
        "end": 26.56,
    },
]


class Rejeu:
    """Moteur qui rejoue les réponses réelles ; `muettes` dit ce que la mesure de niveau a trouvé."""

    def __init__(self, answers: dict, muettes: dict | None = None):
        self.answers, self.muettes, self.sent = answers, muettes or {}, []

    def chunk(self, start: float, end: float):
        self.sent.append((round(start, 2), round(end, 2)))
        return (round(start, 2), round(end, 2))

    def transcribe(self, chunk, *, language=None, temperatures=(0.0,), compression_ratio_threshold=None):
        return [dict(piece) for piece in self.answers[chunk[0]]]

    def detect_language(self, chunk):
        return None  # Groq n'a pas de détection de langue

    def silent(self, start: float, end: float) -> bool | None:
        return self.muettes.get(round(start, 2))


def _segs() -> list[dict]:
    return [dict(seg) for seg in GROQ]


def _reponses() -> dict:
    return {60.0: ARABE_MUET, 116.08: ARABE_TRANSLIT}


# ------------------------------------------------------------------ formules de générique


def test_les_formules_de_generique_sont_reconnues():
    for texte in (
        "ترجمة نانسي قنقر",
        "تَرْجَمَة نانسى قنقر",  # diacritiques et ى : même formule
        "اشتركوا في القناة",
        "اشتركوا في القناه",  # ة/ه : même formule
        "Sous-titres réalisés par la communauté d'Amara.org",
        "SOUS-TITRES RÉALISÉS PAR AMARA.ORG",
        "Merci d'avoir regardé !",
        "Amara.org",
        "Thanks for watching",
        "Please subscribe to the channel",
    ):
        assert repair.is_boilerplate(texte), texte


def test_un_discours_n_est_pas_un_generique():
    for texte in (
        "بسم الله الرحمن الرحيم",
        "اشتركوا في الخير جميعا يا إخوة",
        "Face aux épreuves, le croyant garde espoir.",
        "Al-Fatiha",
        "Merci d'être venus si nombreux",
        "",
    ):
        assert not repair.is_boilerplate(texte), texte


# ------------------------------------------------------------------ sélection des passages


def test_les_fenetres_muettes_ne_partent_pas_en_reprise():
    """0 s et 30 s sont dans les 68,6 s de silence : les renvoyer en arabe forcé ne rend que du générique."""
    segs = _segs()
    moteur = Rejeu(_reponses(), muettes={0.0: True, 30.0: True, 60.0: False})
    logs: list[str] = []
    faits = repair.recover_arabic(moteur, segs, log=logs.append, language="ar")

    # seules les plages qui portent de la parole sont parties : 60 s (une fenêtre) et 116 s
    assert moteur.sent == [(60.0, 89.98), (116.08, 142.64)]
    assert any("silence" in ligne and "0.0s" in ligne for ligne in logs)
    # la reprise muette de 60 s est refusée : « اشتركوا في القناة » n'entre pas, l'original reste
    assert segs[2]["text"] == "Al-Fatiha" and "recovered" not in segs[2]
    assert segs[0]["text"] == "Al Fatiha" and segs[1]["text"] == "Al Fatiha"
    # les passages réellement arabes, eux, sont repris
    assert faits == 3
    assert [seg["text"][:6] for seg in segs[3:]] == ["أولها ", "ولا يم", "فعندما"]
    assert [seg["recovered"] for seg in segs[3:]] == ["ar", "ar", "ar"]


def test_une_reprise_hallucinee_n_est_jamais_acceptee():
    """Même quand la plage part (fournisseur qui ne mesure pas le silence), le générique est refusé."""
    segs = _segs()
    moteur = Rejeu(_reponses())  # silent() -> None : aucune mesure de niveau
    faits = repair.recover_arabic(moteur, segs, log=lambda *_: None, language="ar")

    assert moteur.sent == [(0.0, 89.98), (116.08, 142.64)]  # les trois fenêtres muettes réunies
    assert faits == 3  # 3 hallucinations refusées, 3 vrais passages acceptés
    textes = [seg["text"] for seg in segs]
    assert "ترجمة نانسي قنقر" not in textes and "اشتركوا في القناة" not in textes
    assert not any("نانسي" in texte or "اشتركوا" in texte for texte in textes)
    assert [seg["text"] for seg in segs[:3]] == ["Al Fatiha", "Al Fatiha", "Al-Fatiha"]
    assert all("recovered" not in seg for seg in segs[:3])


def test_le_compteur_du_journal_compte_la_meme_chose_que_les_remplacements():
    """Le journal disait « 7 (out of 2 passages examined) » : deux unités pour un seul compte."""
    segs = _segs()
    moteur = Rejeu(_reponses(), muettes={0.0: True, 30.0: True, 60.0: False})
    logs: list[str] = []
    faits = repair.recover_arabic(moteur, segs, log=logs.append, language="ar")

    ligne = next(ligne for ligne in logs if ligne.startswith("Arabic passages retranscribed"))
    nombres = [int(n) for n in re.findall(r"\d+", ligne)]
    assert nombres == [faits, 4, 2]  # 3 remplacés sur 4 segments envoyés, en 2 plages
    assert nombres[0] <= nombres[1]  # jamais plus de remplacements que de segments examinés


# ------------------------------------------------------------------ transcription initiale


def _wav_silence_puis_son(path, muettes_s: float, son_s: float, rate: int = 16000):
    """68,6 s de zéros puis de la parole (un son) : la forme réelle de cet enregistrement."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * muettes_s))
        frames = array.array(
            "h", (int(4000 * math.sin(2 * math.pi * 220 * t / rate)) for t in range(int(rate * son_s)))
        )
        w.writeframes(frames.tobytes())
    return path


def test_les_generiques_ne_sont_pas_dans_la_transcription_initiale(monkeypatch, serveur, tmp_path):  # noqa: F811
    _Handler.seen = []
    _Handler.plan = [
        (
            200,
            {
                "language": "English",
                "duration": 90.0,
                "segments": [
                    {"start": 0.0, "end": 29.98, "text": " Al Fatiha", "avg_logprob": -0.579},
                    {"start": 30.0, "end": 59.98, "text": " Al Fatiha", "avg_logprob": -0.579},
                    {
                        "start": 60.0,
                        "end": 89.98,
                        "text": " al-talaq, haytu yaqul Allah",
                        "avg_logprob": -0.146,
                        "no_speech_prob": 0.02,
                    },
                ],
            },
        ),
        (
            200,
            {
                "language": "ar",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 29.98,
                        "text": " الطلاق حيث يقول الله عز وجل",
                        "avg_logprob": -0.05,
                        "no_speech_prob": 0.02,
                    }
                ],
            },
        ),
    ]
    _reglage(monkeypatch, serveur)
    monkeypatch.setenv("SADA_LANG", "en")  # messages assertés tels quels
    wav = _wav_silence_puis_son(tmp_path / "audio.wav", 60.0, 30.0)
    logs: list[str] = []
    res = R.transcribe(str(wav), str(tmp_path / "t.json"), log=logs.append)

    # les deux fenêtres mesurées muettes ne sont ni écrites ni proposées à une reprise
    assert [seg["start"] for seg in res["segments"]] == [60.0]
    assert any("hallucination discarded at 0.0s" in ligne for ligne in logs)
    assert any("hallucination discarded at 30.0s" in ligne for ligne in logs)
    # et le score du `verbose_json` suit le segment jusqu'à la décision
    assert res["segments"][0]["no_speech_prob"] == 0.02
    assert res["segments"][0]["recovered"] == "ar"
    assert res["arabic_recovered"] == 1
    assert b'name="language"' in _Handler.seen[1]["body"]  # la reprise est bien partie en arabe forcé


def test_scores_du_verbose_json_ecartent_une_hallucination(monkeypatch, serveur, tmp_path):  # noqa: F811
    """`no_speech_prob` et `compression_ratio` : ce que Whisper dit quand il n'entend rien ou radote."""
    muet = {"start": 0.0, "end": 20.0, "text": "بسم الله الرحمن الرحيم", "avg_logprob": -0.2, "no_speech_prob": 0.93}
    radote = {
        "start": 20.0,
        "end": 40.0,
        "text": "وإذا سألك عبادي عني فإني قريب وإذا سألك عبادي عني فإني قريب وإذا سألك عبادي عني",
        "avg_logprob": -0.3,
        "compression_ratio": 3.1,
    }
    parole = {
        "start": 40.0,
        "end": 50.0,
        "text": "فاستجيبوا لي وليؤمنوا بي لعلهم يرشدون",
        "avg_logprob": -0.2,
        "no_speech_prob": 0.1,
        "compression_ratio": 1.3,
    }
    gardes = repair.without_hallucinations([muet, radote, parole], log=lambda *_: None)
    assert gardes == [parole]
    # un fournisseur qui n'envoie pas ces scores ne fait pencher aucune décision
    assert repair.without_hallucinations([dict(GROQ[3])], log=lambda *_: None) == [GROQ[3]]


def test_source_absent_quand_l_appelant_n_en_donne_pas(monkeypatch, serveur, tmp_path):  # noqa: F811
    """Comme en local : la clé n'est écrite que si l'appelant sait d'où vient l'audio (`run.py`)."""
    _Handler.plan = [
        (200, {"language": "fr", "segments": [{"start": 0.0, "end": 2.0, "text": " Ça marche.", "avg_logprob": -0.2}]})
    ]
    _reglage(monkeypatch, serveur)
    wav = _wav_silence_puis_son(tmp_path / "audio.wav", 0.0, 4.0)
    sans = R.transcribe(str(wav), str(tmp_path / "sans.json"), log=lambda *_: None, arabic_retry=False)
    assert "source" not in sans
    avec = R.transcribe(
        str(wav),
        str(tmp_path / "avec.json"),
        log=lambda *_: None,
        arabic_retry=False,
        source={"recording": "x", "start": 0, "end": 4},
    )
    assert avec["source"] == {"recording": "x", "start": 0, "end": 4}
    # le cache relu sait encore à quoi il correspond (même clé, même forme de JSON)
    assert json.loads((tmp_path / "avec.json").read_text(encoding="utf-8"))["source"] == avec["source"]
