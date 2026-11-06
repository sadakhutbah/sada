from minbar.pipeline.arabic import normalize
from minbar.pipeline.verses import VerseIndex, detect

VERSES = [
    {"surah": 112, "ayah": 1, "surah_name": "Al-Ikhlas", "text": "قل هو الله أحد", "fr": "Dis : Il est Allah, Unique."},
    {"surah": 103, "ayah": 1, "surah_name": "Al-Asr", "text": "والعصر", "fr": ""},
    {"surah": 2, "ayah": 153, "surah_name": "Al-Baqara", "text": "يا أيها الذين آمنوا استعينوا بالصبر والصلاة إن الله مع الصابرين", "fr": "Ô vous qui croyez !"},
    {"surah": 65, "ayah": 2, "surah_name": "At-Talaq", "text": "ومن يتق الله يجعل له مخرجا", "fr": ""},
]
for v in VERSES:
    v["norm"] = normalize(v["text"])


def test_normalize():
    assert normalize("بِسْمِ ٱللَّهِ الرَّحْمَٰنِ") == "بسم الله الرحمن"
    assert normalize("إِنَّ أُمَّةً مُؤْمِنَةً") == "ان امه مومنه"
    assert normalize("Bonjour الله 123") == "الله"


def seg(t0, t1, text):
    return {"start": t0, "end": t1, "text": text}


def test_detects_with_whisper_style_errors_and_diacritics():
    idx = VerseIndex(VERSES)
    segs = [
        seg(0, 5, "Mes frères et sœurs, rappelez-vous"),
        seg(5, 12, "يَا أَيُّهَا الَّذِينَ آمَنُوا اسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ إِنَّ اللَّهَ مَعَ الصَّابِرِينَ"),
        seg(60, 64, "يا ايها الذين امنو استعينو بالصبر و الصلاه ان الله مع الصابرين"),  # sans diacritiques, fautes
        seg(100, 104, "وَمَنْ يَتَّقِ اللَّهَ يَجْعَلْ لَهُ مَخْرَجًا"),
    ]
    ms = detect(segs, idx)
    refs = [(m.ref, round(m.start)) for m in ms]
    assert ("2:153", 5) in refs and ("2:153", 60) in refs and ("65:2", 100) in refs
    assert all(m.score >= 82 for m in ms)


def test_no_false_positive_on_french_or_unrelated_arabic():
    idx = VerseIndex(VERSES)
    segs = [seg(0, 5, "La patience est une vertu"), seg(5, 9, "ذهب الولد الى المدرسة صباحا مع اخيه الصغير")]
    assert detect(segs, idx) == []


def test_short_verse():
    idx = VerseIndex(VERSES)
    ms = detect([seg(0, 3, "قل هو الله احد")], idx)
    assert [m.ref for m in ms] == ["112:1"]
