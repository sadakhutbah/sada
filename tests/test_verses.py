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


def test_formula_in_several_verses_is_ambiguous_not_firm():
    vs = [dict(v) for v in VERSES] + [
        {"surah": 5, "ayah": 120, "surah_name": "Al-Ma'ida", "text": "لله ملك السماوات والأرض وما فيهن وهو على كل شيء قدير", "fr": ""},
        {"surah": 11, "ayah": 4, "surah_name": "Hud", "text": "الى الله مرجعكم وهو على كل شيء قدير", "fr": ""},
    ]
    for v in vs:
        v["norm"] = normalize(v["text"])
    idx = VerseIndex(vs)
    ms = detect([seg(0, 3, "وهو على كل شيء قدير")], idx)
    assert ms and all(m.confidence in ("ambigu", "court") for m in ms)
    assert not any(m.confidence == "sûr" for m in ms)


# ------------------------------------------------------------------ détection par la traduction française

VERSES_FR = VERSES + [
    {"surah": 65, "ayah": 3, "surah_name": "At-Talaq",
     "text": "وَمَن يَتَوَكَّلْ عَلَى اللَّهِ فَهُوَ حَسْبُهُ",
     "fr": "Et quiconque place sa confiance en Allah, Il lui suffit. Allah atteint ce qu'Il se propose, et Allah a assigné à chaque chose sa mesure."},
    {"surah": 2, "ayah": 286, "surah_name": "Al-Baqara",
     "text": "لَا يُكَلِّفُ اللَّهُ نَفْسًا إِلَّا وُسْعَهَا",
     "fr": "Allah n'impose à aucune âme une charge supérieure à sa capacité."},
]
for v in VERSES_FR:
    v["norm"] = normalize(v["text"])


def test_texte_francais_transcrit_est_detecte():
    """Le cas signalé : la récitation transcrite en français (Whisper) ne donnait aucun verset."""
    from minbar.pipeline.verses_fr import FrenchIndex

    idx = VerseIndex(VERSES_FR)
    assert len(idx.fr) > 0 and len(idx.fr) <= len(FrenchIndex(VERSES_FR))
    segs = [
        seg(0, 6, "arrêtons-nous sur ce verset grandiose de sourate At-Talaq"),
        seg(6, 14, "où Allah dit : quiconque place sa confiance en Allah, Il lui suffit"),
        seg(14, 20, "Allah atteint ce qu'Il se propose, et Allah a assigné à chaque chose sa mesure"),
    ]
    ms = detect(segs, idx)
    assert [m.ref for m in ms] == ["65:3"], ms
    m = ms[0]
    assert m.via == "francais" and m.confidence == "trad"
    assert m.start == 0 and m.end == 20
    assert "confiance" in m.heard


def test_traduction_fidele_en_francais_litteral():
    idx = VerseIndex(VERSES_FR)
    ms = detect([seg(30, 38, "Allah n'impose à aucune âme une charge supérieure à sa capacité")], idx)
    assert [m.ref for m in ms] == ["2:286"], ms


def test_pas_de_faux_positif_sur_une_khutba_francaise_ordinaire():
    """Le piège vu sur le vrai enregistrement : texte religieux voisin mais pas un verset."""
    idx = VerseIndex(VERSES_FR)
    segs = [
        seg(0, 8, "Ne craignez pas l'avenir et ne désespérez jamais de la délivrance, même si toutes les portes vous semblent fermées"),
        seg(20, 28, "la volonté absolue d'Allah : son ordre s'accomplit inévitablement et rien ne peut l'entraver"),
        seg(40, 48, "les épreuves sont limitées, l'affliction a une fin déjà programmée"),
    ]
    assert detect(segs, idx) == []


def test_citation_francaise_puis_recitation_arabe_sont_deux_occurrences():
    """Si l'imam traduit puis récite, ce sont deux prises de parole : chacune a son horodatage."""
    idx = VerseIndex(VERSES_FR)
    v = [x for x in VERSES_FR if x["surah"] == 65 and x["ayah"] == 3][0]
    segs = [seg(0, 8, "quiconque place sa confiance en Allah, Il lui suffit, et Allah a assigné à chaque chose sa mesure"),
            seg(80, 90, v["text"])]
    ms = detect(segs, idx)
    assert [(m.ref, m.via) for m in ms] == [("65:3", "francais"), ("65:3", "arabe")], ms


def test_segment_mixte_ne_double_pas_la_detection():
    idx = VerseIndex(VERSES_FR)
    segs = [seg(0, 10, "Il dit « وَمَنْ يَتَوَكَّلْ عَلَى اللَّهِ فَهُوَ حَسْبُهُ » puis commente : la confiance porte ses fruits")]
    assert len(detect(segs, idx)) <= 1


def test_normalisation_francaise():
    from minbar.pipeline.verses_fr import content_words, norm_fr

    assert norm_fr("À QUICONQUE place sa confiance… l'Allah !") == "quiconque place confiance allah"
    assert content_words("Allah a assigné à chaque chose sa mesure") == ["assigne", "chaque", "mesure"]
