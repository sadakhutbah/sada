"""Glossaire : hotwords pour Whisper et glose française des termes à leur première occurrence."""
import json

from minbar.pipeline import clips as C
from minbar.pipeline import glossary as G


def test_normalisation_insensible_casse_accents_apostrophes():
    assert G.normalize("Tawba,") == "tawba"
    assert G.normalize("TAQWA") == "taqwa"
    assert G.normalize("aïd") == "aid"
    assert G.normalize("du’a") == "du'a"
    assert G.normalize("al-hamdu  lillah") == "al hamdu lillah"


def test_variantes_et_glose_unique_dans_le_clip():
    words = [{"w": "La", "s": 0.0, "e": 0.2}, {"w": "taouba", "s": 0.2, "e": 0.6},
             {"w": "est", "s": 0.6, "e": 0.8}, {"w": "un", "s": 0.8, "e": 0.9},
             {"w": "retour.", "s": 0.9, "e": 1.3}, {"w": "Cette", "s": 1.3, "e": 1.5},
             {"w": "tawbah", "s": 1.5, "e": 1.9}, {"w": "libre.", "s": 1.9, "e": 2.3}]
    out = G.gloss_words(words)
    assert G.display(out[1]) == "taouba (le repentir)"
    assert out[1]["w"] == "taouba" and out[6].get("gloss") is None   # une seule glose par clip
    assert "gloss" not in words[1]                      # l'entrée n'est pas modifiée
    assert out[1]["s"] == 0.2 and out[1]["e"] == 0.6    # minutage inchangé


def test_terme_multi_mots_et_ponctuation_conservee():
    words = [{"w": "Nous", "s": 0, "e": 0.3}, {"w": "disons", "s": 0.3, "e": 0.6},
             {"w": "in", "s": 0.6, "e": 0.7}, {"w": "sha", "s": 0.7, "e": 0.8},
             {"w": "Allah,", "s": 0.8, "e": 1.2}, {"w": "demain.", "s": 1.2, "e": 1.6}]
    out = G.gloss_words(words)
    assert G.display(out[4]) == "Allah (si Allah le veut),"
    assert G.display(out[5]) == "demain."


def test_pas_de_faux_positif_sur_un_mot_francais():
    words = [{"w": "Le", "s": 0, "e": 0.2}, {"w": "sabre", "s": 0.2, "e": 0.6},
             {"w": "et", "s": 0.6, "e": 0.8}, {"w": "le", "s": 0.8, "e": 1.0},
             {"w": "harem", "s": 1.0, "e": 1.4}]
    assert [G.display(w) for w in G.gloss_words(words)] == ["Le", "sabre", "et", "le", "harem"]


def test_fichier_de_glossaire_complete_et_remplace(monkeypatch, tmp_path):
    f = tmp_path / "glossaire.json"
    f.write_text(json.dumps({"terms": [
        {"term": "tawba", "variants": ["tawba", "taouba"], "fr": "le retour vers Allah"},
        {"term": "khouchou'", "variants": ["khouchou", "khushu"], "fr": "la concentration"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MINBAR_GLOSSARY", str(f))
    terms = G.load()
    by = {e["term"]: e for e in terms}
    assert by["tawba"]["fr"] == "le retour vers Allah"       # le fichier gagne
    assert by["khouchou'"]["fr"] == "la concentration"        # terme ajouté
    assert len(terms) == len(G.default_terms()) + 1
    words = [{"w": "khouchou", "s": 0, "e": 1}]
    assert G.display(G.gloss_words(words)[0]) == "khouchou (la concentration)"


def test_fichier_absent_ou_illisible_laisse_les_termes_par_defaut(monkeypatch, tmp_path):
    monkeypatch.setenv("MINBAR_GLOSSARY", str(tmp_path / "absent.json"))
    assert len(G.load()) == len(G.default_terms())
    (tmp_path / "casse.json").write_text("{ pas du json", encoding="utf-8")
    monkeypatch.setenv("MINBAR_GLOSSARY", str(tmp_path / "casse.json"))
    assert len(G.load()) == len(G.default_terms())


def test_hotwords_contient_les_termes_courants():
    hw = G.hotwords()
    for term in ("tawba", "istighfar", "taqwa", "khutba", "in sha Allah", "sallallahu alayhi wa sallam"):
        assert term in hw
    assert ", ," not in hw and not hw.startswith(",")


def test_build_ass_glose_une_fois_sans_casser_les_pages(tmp_path):
    segs = [{"start": 0.0, "end": 4.0, "text": "La tawba est un retour vers Allah.",
             "words": [{"w": "La", "s": 0.0, "e": 0.2}, {"w": "tawba", "s": 0.2, "e": 0.6},
                       {"w": "est", "s": 0.6, "e": 0.8}, {"w": "un", "s": 0.8, "e": 0.9},
                       {"w": "retour", "s": 0.9, "e": 1.2}, {"w": "vers", "s": 1.2, "e": 1.4},
                       {"w": "Allah.", "s": 1.4, "e": 2.0}]},
            {"start": 4.5, "end": 7.0, "text": "La tawba efface les fautes.",
             "words": [{"w": "La", "s": 4.5, "e": 4.7}, {"w": "tawba", "s": 4.7, "e": 5.1},
                       {"w": "efface", "s": 5.1, "e": 5.4}, {"w": "les", "s": 5.4, "e": 5.6},
                       {"w": "fautes.", "s": 5.6, "e": 6.0}]}]
    p = tmp_path / "g.ass"
    C.build_ass(segs, [], 0.0, 8.0, str(p))
    lines = p.read_text(encoding="utf-8").splitlines()
    glow = [l for l in lines if ",Glow," in l]
    assert sum("(le repentir)" in l for l in glow) == 1      # une page, une seule glose
    assert any("tawba" in l and "(le repentir)" in l for l in glow)   # sur la page du terme
    assert all(l.count("\\N") <= 1 for l in glow)            # deux lignes au plus
    sub = [l for l in lines if ",Sub," in l]
    assert sub and all(l.count("\\N") <= 1 for l in sub)      # deux lignes au plus
    # le second passage garde le terme sans glose
    assert any("La tawba efface" in l for l in glow)


def test_glossaire_explicite_vide_desactive_la_glose(tmp_path):
    words = [{"w": "tawba", "s": 0, "e": 1}]
    assert G.display(G.gloss_words(words, [])[0]) == "tawba"
    p = tmp_path / "v.ass"
    C.build_ass([{"start": 0, "end": 2, "text": "tawba", "words": words}], [], 0, 3, str(p), glossary=[])
    assert "(le repentir)" not in p.read_text(encoding="utf-8")


class _Piece:
    def __init__(self, text, start, end, avg_logprob=-0.3):
        self.text, self.start, self.end, self.avg_logprob = text, start, end, avg_logprob
        self.words = []


class _Info:
    language, language_probability, duration = "fr", 0.98, 1.0


class _Model:
    def __init__(self, name, **kw):
        self.kw = kw
        _Model.last = self

    def transcribe(self, audio, **kw):
        self.call = kw
        return iter([_Piece(" La tawba est un retour.", 0.0, 1.0)]), _Info()


def test_transcribe_passe_le_glossaire_en_hotwords(monkeypatch, tmp_path):
    import faster_whisper
    import wave

    from minbar.pipeline import transcribe as T

    monkeypatch.setattr(faster_whisper, "WhisperModel", _Model)
    monkeypatch.setattr(T, "pick_model", lambda prefer, log: ("tiny", "cpu", "int8"))
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    res = T.transcribe(str(wav), str(tmp_path / "t.json"), "cpu", log=lambda *_: None, arabic_retry=False)
    hw = _Model.last.call["hotwords"]
    assert hw and "tawba" in hw and "khutba" in hw
    assert res["hotwords"] is True and res["segments"][0]["text"] == "La tawba est un retour."
