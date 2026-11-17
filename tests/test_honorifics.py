import pytest

from sada.pipeline import glossary, honorifics
from sada.pipeline.honorifics import apply, apply_words

FR = "Le Prophète a dit à Abu Bakr et à Aïcha que Allah pardonne, comme Moïse l'a rapporté."


@pytest.mark.parametrize(
    ("lang", "mode", "text", "expected"),
    [
        (
            "fr",
            "full",
            FR,
            "Le Prophète (paix et salut sur lui) a dit à Abu Bakr (qu'Allah l'agrée) et à Aïcha (qu'Allah l'agrée)"
            " que Allah (exalté soit-Il) pardonne, comme Moïse (paix sur lui) l'a rapporté.",
        ),
        (
            "fr",
            "short",
            FR,
            "Le Prophète ﷺ a dit à Abu Bakr (RA) et à Aïcha (RA) que Allah (SWT) pardonne, comme Moïse (AS) l'a rapporté.",
        ),
        (
            "en",
            "full",
            "The Prophet told Abu Hurayra and Fatima that Allah forgives, as Moses said.",
            "The Prophet (peace be upon him) told Abu Hurayra (may Allah be pleased with him) and Fatima"
            " (may Allah be pleased with her) that Allah (glorified and exalted be He) forgives,"
            " as Moses (peace be upon him) said.",
        ),
        (
            "en",
            "short",
            "The Messenger of Allah told Ibn Abbas that Allah sent Jesus.",
            "The Messenger of Allah ﷺ told Ibn Abbas (RA) that Allah (SWT) sent Jesus (AS).",
        ),
        (
            "ar",
            "full",
            "قال رسول الله لأبي بكر وعائشة إن الله غفور كما قال موسى",
            "قال رسول الله صلى الله عليه وسلم لأبي بكر رضي الله عنه وعائشة رضي الله عنها"
            " إن الله سبحانه وتعالى غفور كما قال موسى عليه السلام",
        ),
        (
            "ar",
            "short",
            "قال النبي إن الله غفور كما قال موسى",
            "قال النبي ﷺ إن الله سبحانه غفور كما قال موسى عليه السلام",
        ),
    ],
)
def test_languages_and_modes(lang, mode, text, expected):
    assert apply(text, lang, mode) == expected


@pytest.mark.parametrize(
    ("lang", "text"),
    [
        ("fr", "Le Prophète ﷺ a dit"),
        ("fr", "le Prophète sallallahu alayhi wa sallam a dit"),
        ("fr", "le Prophète (paix et salut sur lui) a dit"),
        ("fr", "Muhammad, que la paix soit sur lui, a dit"),
        ("fr", "dans l'histoire de Moussa alayhi salam, il est dit"),
        ("fr", "Omar radiallahu anhu a dit"),
        ("fr", "Allah azza wa jal a dit"),
        ("fr", "Allah ta'ala dit"),
        ("fr", "Allah subhanahu wa ta'ala dit"),
        ("en", "the Prophet (saws) said"),
        ("en", "the Prophet SAW said"),
        ("en", "Allah (SWT) says"),
        ("ar", "قال النبي صلى الله عليه وسلم"),
        ("ar", "قال الله عز وجل"),
        ("ar", "قال موسى عليه السلام"),
    ],
)
def test_never_doubles_a_formula_already_said(lang, text):
    assert apply(text, lang, "full") == text
    assert apply(text, lang, "short") == text


@pytest.mark.parametrize("mode", honorifics.MODES)
@pytest.mark.parametrize(
    ("lang", "text"),
    [
        ("fr", FR),
        ("fr", "le Messager d'Allah, Muhammad, et le Prophète Moïse"),
        ("en", "the Prophet's companions and Abu Bakr; Allah knows"),
        ("ar", "وأشهد أن محمدا عبده ورسوله، وقال النبي إن الله غفور"),
    ],
)
def test_idempotent(lang, mode, text):
    once = apply(text, lang, mode)
    assert once != text
    assert apply(once, lang, mode) == once


@pytest.mark.parametrize(
    "text",
    [
        "un prophète est venu, les prophètes sont venus",
        "le prophète a dit",  # lowercase: generic
        "Omar ibn Abd al-Aziz et frère Omar",  # someone else
        "Adam Smith",
        "la sourate Youssouf",
        "Muhammad ibn Abd al-Wahhab",
    ],
)
def test_generic_or_someone_else(text):
    assert apply(text, "fr", "full") == text


@pytest.mark.parametrize(
    "text",
    [
        "le Messager d'Allah",  # Allah only inside the title; the title itself takes the formula
        "in sha Allah, nous verrons",
        "bismillah",
        "au nom d'Allah",
        "qu'Allah nous guide",
        "Allah est le plus grand",
        "louange à Allah",
        "Ô Allah, pardonne-nous",
    ],
)
def test_allah_in_fixed_expressions(text):
    assert "exalté" not in apply(text, "fr", "full")


def test_allah_once_per_segment():
    out = apply("Allah voit, Allah entend, Allah sait.", "fr", "full")
    assert out == "Allah (exalté soit-Il) voit, Allah entend, Allah sait."


def test_title_followed_by_name_takes_one_formula():
    assert apply("le Prophète Moïse", "fr", "full") == "le Prophète Moïse (paix sur lui)"
    assert apply("le Prophète Muhammad", "fr", "short") == "le Prophète Muhammad ﷺ"


def test_prayer_upon_the_prophet_is_already_the_formula():
    assert apply("Ô Allah, prie sur Muhammad", "fr", "full") == "Ô Allah, prie sur Muhammad"
    assert apply("اللهم صل على محمد", "ar", "full") == "اللهم صل على محمد"


def test_vowelled_quran_untouched():
    verse = "يَا أَيُّهَا الَّذِينَ آمَنُوا اتَّقُوا اللَّهَ حَقَّ تُقَاتِهِ وَلَا تَمُوتُنَّ إِلَّا وَأَنْتُمْ مُسْلِمُونَ"
    assert apply(verse, "ar", "full") == verse


def test_french_quotation_untouched():
    text = "« Certes, Allah atteint son but. » Et Allah sait."
    assert apply(text, "fr", "full") == "« Certes, Allah atteint son but. » Et Allah (exalté soit-Il) sait."


def test_arabic_fixed_expressions_with_a_clitic():
    text = "أقول قولي هذا واستغفروا الله لي ولكم"
    assert apply(text, "ar", "full") == text


def test_other_language_unchanged():
    assert apply("Allah und der Prophet", "de", "full") == "Allah und der Prophet"
    assert apply("Allah", None, "full") == "Allah"


def test_disabled_by_environment(monkeypatch):
    monkeypatch.setenv("SADA_HONORIFICS", "off")
    assert apply(FR, "fr", "full") == FR
    assert apply_words([{"w": "Allah", "s": 0.0, "e": 1.0}], "fr", "short") == [{"w": "Allah", "s": 0.0, "e": 1.0}]


def test_words_share_the_timing_of_the_name():
    words = [
        {"w": "le", "s": 0.0, "e": 0.2},
        {"w": "Prophète,", "s": 0.2, "e": 0.6},
        {"w": "Abu", "s": 0.6, "e": 0.8},
        {"w": "Bakr", "s": 0.8, "e": 1.0},
        {"w": "dit.", "s": 1.0, "e": 1.2},
    ]
    out = apply_words(words, "fr", "short")
    assert [(w["s"], w["e"]) for w in out] == [(w["s"], w["e"]) for w in words]  # no word added
    assert out[1]["honorific"] == " ﷺ" and out[3]["honorific"] == " (RA)"
    assert [glossary.display(w) for w in out] == ["le", "Prophète ﷺ,", "Abu", "Bakr (RA)", "dit."]
    assert "honorific" not in words[1]  # input untouched
    assert apply_words(out, "fr", "short") == out  # idempotent
    inline = apply_words(words, "fr", "full", inline=True)
    assert inline[1]["w"] == "Prophète (paix et salut sur lui),"
