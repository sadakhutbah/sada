"""Sous-titres des clips : français seul, pages courtes, carte verset propre ; encodeur : sortie propre."""
import re

from minbar.config import Config
from minbar.encoder import Encoder
from minbar.pipeline import clips as C
from minbar.pipeline import translate
from minbar.pipeline.verses import is_formula, recited_arabic, strip_quran_marks

VERSE = {"ref": "65:3", "surah_name": "At-Talaaq", "start": 10.0, "end": 14.0, "score": 95.0, "coverage": 0.6,
         "confidence": "sûr", "heard": "إن الله بالغ أمره قد جعل الله لكل شيء قدرا",
         "verse_text": "وَيَرْزُقْهُ مِنْ حَيْثُ لَا يَحْتَسِبُ ۚ وَمَن يَتَوَكَّلْ عَلَى ٱللَّهِ فَهُوَ حَسْبُهُۥٓ ۚ إِنَّ ٱللَّهَ بَٰلِغُ أَمْرِهِۦ ۚ قَدْ جَعَلَ ٱللَّهُ لِكُلِّ شَىْءٍ قَدْرًا",
         "verse_fr": "et lui accordera Ses dons par des moyens sur lesquels il ne comptait pas. Allah atteint ce qu'Il Se propose, et Allah a assigné une mesure à chaque chose."}
SEGS = [
    {"start": 0.0, "end": 4.0, "text": "Ô vous les croyants, la patience est une lumière."},
    {"start": 4.5, "end": 9.0, "text": "أيها المسلمون في مواجهة ابتلاءات الحياة"},
    {"start": 10.0, "end": 14.0, "text": "إن الله بالغ أمره قد جعل الله لكل شيء قدرا"},
    {"start": 14.5, "end": 17.0, "text": "صلى الله عليه وسلم ومعه قرآن غير مترجم"},
]
TR = translate.with_correction(translate.empty(), SEGS[1], "Ô musulmans, face aux épreuves de la vie,")
TR["verse_fr"]["65:3@10.0"] = "Allah atteint ce qu'Il Se propose, et Allah a assigné une mesure à chaque chose."


def _events(path):
    return [l for l in open(path, encoding="utf-8").read().splitlines() if l.startswith("Dialogue:")]


def test_french_only_and_verse_card(tmp_path):
    p = tmp_path / "c.ass"
    n = C.build_ass(SEGS, [VERSE], 0.0, 18.0, str(p), tr=TR)
    ev = _events(p)
    subs = [e for e in ev if ",Sub," in e]
    assert n > 0 and subs
    for e in subs:  # aucune lettre arabe dans les sous-titres de parole
        assert not re.search(r"[\u0600-\u06FF]", e)
    assert any("épreuves" in e for e in subs)          # segment arabe traduit
    assert not any("مترجم" in e for e in ev)            # segment arabe sans traduction : rien à l'écran
    card_ar = [e for e in ev if ",CardAr," in e]
    assert len(card_ar) == 1 and not re.search(r"[\u06D6-\u06ED]", card_ar[0])  # pas de signes de pause
    assert any("SOURATE AT-TALAQ" in e and "65:3" in e for e in ev)
    assert any(",CardFr," in e and "Allah atteint" in e for e in ev)
    # pendant la carte : pas de sous-titre de parole
    def t(s):
        h, m, x = s.split(":"); return int(h) * 3600 + int(m) * 60 + float(x)
    for e in subs:
        a, b = t(e.split(",")[1]), t(e.split(",")[2])
        assert not (10.5 < (a + b) / 2 < 14.0)


def test_elision_merged_when_clip_starts_late(tmp_path):
    # mots Whisper « d » + « 'Allah » : recollés même si le clip ne commence pas à 0
    seg = {"start": 100.0, "end": 102.0, "text": "La miséricorde d'Allah",
           "words": [{"w": "La", "s": 100.0, "e": 100.3}, {"w": "miséricorde", "s": 100.3, "e": 101.0},
                     {"w": "d", "s": 101.0, "e": 101.1}, {"w": "'Allah", "s": 101.1, "e": 101.6}]}
    p = tmp_path / "e.ass"
    C.build_ass([seg], [], 99.0, 103.0, str(p))
    subs = [e for e in _events(p) if ",Sub," in e]
    assert subs and any("d’Allah" in re.sub(r"\{[^}]*\}", "", e) for e in subs)
    assert not any("d ’Allah" in re.sub(r"\{[^}]*\}", "", e) for e in subs)


def test_pages_short_and_not_ending_on_weak_word():
    words = C.interpolate_words("Arrêtons-nous aujourd'hui sur ce verset immense de la sourate At-Talaq, où Allah dit", 0, 6)
    chunks = C.chunk_words(words)
    for ch in chunks:
        txt = " ".join(w["w"] for w in ch)
        assert len(txt) <= C.SUB_CHUNK_CHARS + 12
        assert ch[-1]["w"].lower() not in C.WEAK_END or ch is chunks[-1]
        lines = C.split_lines([w["w"] for w in ch])
        assert 1 <= len(lines) <= 2


def test_quran_marks_and_formulas():
    assert "ۚ" not in strip_quran_marks(VERSE["verse_text"])
    assert recited_arabic(VERSE).startswith("إن الله") or recited_arabic(VERSE).startswith("إِنَّ")
    assert is_formula("أعوذ بالله من الشيطان الرجيم")
    assert not is_formula("ولنتوقف اليوم عند هذه الآية العظيمة من سورة الطلاق")


def test_encoder_clean_output(tmp_path):
    cfg = Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path))
    enc = Encoder(cfg, tmp_path, log=lambda m: None)
    rec, clean = enc._new_recording()
    assert clean == rec[:-4] + "_clean.mkv"
    cmd = " ".join(enc._cmd(rec, clean))
    assert "asplit=2[a][aclean]" in cmd and "-map 0:v:0 -c:v:0 copy" in cmd
    enc2 = Encoder(Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path), clean_copy=False), tmp_path, log=lambda m: None)
    cmd2 = " ".join(enc2._cmd(*enc2._new_recording()))
    assert "split=2[cam][camclean]" in cmd2 and "-crf 18" in cmd2
    e3 = Encoder(Config.load(None, record_dir=str(tmp_path), state_dir=str(tmp_path), record_clean=False), tmp_path, log=lambda m: None)
    rec3, clean3 = e3._new_recording()
    cmd3 = " ".join(e3._cmd(rec3, clean3))
    assert clean3 is None and "_clean.mkv" not in cmd3 and "asplit" not in cmd3
