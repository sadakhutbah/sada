"""Islamic honorifics added after the names they honour, once the text is transcribed or translated.

`apply(text, lang, mode)` writes the formula of respect after:

- the Prophet Muhammad (« Muhammad », « le Prophète », « le Messager d'Allah », النبي, رسول الله…);
- the other prophets (Moïse/Moussa/Moses, Jésus/Issa/Jesus, Ibrahim/Abraham…);
- a few companions (Abu Bakr, Omar, Othmane, Ali, Bilal, Abu Hurayra, Ibn Abbas, Ibn Umar, Anas,
  Aïcha, Khadija, Fatima).

`mode` is `full` (« (paix et salut sur lui) ») or `short` (« ﷺ », « (AS) »); `lang` is `fr`, `en`
or `ar`, any other language is returned unchanged. A formula already said by the speaker in the next
few words (« Moussa alayhi salam », « Allah azza wa jal ») is never doubled, and applying twice
gives the same text. `SADA_HONORIFICS=off` disables everything.

Allah never receives a formula: when the imam does not say one, it is deliberate (Qur'an quoted,
fixed expression such as « je cherche refuge auprès d'Allah »), and adding words there would alter
what is recited. Only a formula the speaker actually says is kept.

Limits, by design of a text-only pass:
- a modern person who bears a prophet's or a companion's name (« Adam », « Omar ») is honoured
  unless the context says otherwise (« frère Omar », « Omar Diallo », « Omar ibn Abd al-Aziz »);
- in Arabic, « علي », « عمر » and « صالح » are ordinary words too: only their full forms
  (« علي بن أبي طالب », « عمر بن الخطاب ») are recognised;
- a Quranic quotation is protected when the caller knows it is one (a detected verse is never
  passed here), when it is fully vowelled (`vocalised`) or between French quotes « … »; a bare
  verse quoted inside a plain segment is treated as speech.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

MODES = ("full", "short")
LANGS = ("fr", "en", "ar")
#: words after a name in which a formula already said by the speaker is looked for
WINDOW_WORDS = 6

MUHAMMAD, ALLAH, PROPHET, COMPANION, COMPANION_F = "muhammad", "allah", "prophet", "companion", "companion_f"

FORMULAS = {
    MUHAMMAD: {
        "full": {"fr": "(paix et salut sur lui)", "en": "(peace be upon him)", "ar": "صلى الله عليه وسلم"},
        "short": {"fr": "ﷺ", "en": "ﷺ", "ar": "ﷺ"},
    },
    PROPHET: {
        "full": {"fr": "(paix sur lui)", "en": "(peace be upon him)", "ar": "عليه السلام"},
        "short": {"fr": "(AS)", "en": "(AS)", "ar": "عليه السلام"},
    },
    COMPANION: {
        "full": {"fr": "(qu'Allah l'agrée)", "en": "(may Allah be pleased with him)", "ar": "رضي الله عنه"},
        "short": {"fr": "(RA)", "en": "(RA)", "ar": "رضي الله عنه"},
    },
    COMPANION_F: {
        "full": {"fr": "(qu'Allah l'agrée)", "en": "(may Allah be pleased with her)", "ar": "رضي الله عنها"},
        "short": {"fr": "(RA)", "en": "(RA)", "ar": "رضي الله عنها"},
    },
}

# ------------------------------------------------------------------ building blocks

#: a word character, Arabic diacritics included (they are not `\w` for `re`)
_W = r"[\w\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]"
_START = rf"(?<!{_W})"
_END = rf"(?!{_W})"
_AP = "['’‘]"
_DIAC = "[\u064b-\u065f\u0670]*"
_ARABIC_LETTER = re.compile(r"[\u0621-\u064a]")


def _ar(phrase: str) -> str:
    """Regex of an Arabic phrase: any vowel marks, any hamza on the alif, ى/ي alike."""
    out = []
    for char in phrase:
        if char == " ":
            out.append(r"\s+")
        elif char in "اأإآ":
            out.append("[اأإآ]" + _DIAC)
        elif char in "ىي":
            out.append("[ىي]" + _DIAC)
        elif _ARABIC_LETTER.match(char):
            out.append(re.escape(char) + _DIAC)
        else:
            out.append(re.escape(char))
    return "".join(out)


def _alt(*parts: str) -> str:
    return "(?:" + "|".join(parts) + ")"


def _words(pattern: str, flags: int = 0) -> re.Pattern:
    return re.compile(_START + pattern + _END, flags)


# ------------------------------------------------------------------ names

_PATRONYMIC_LATIN = r"(?:\s+[IiBb](?:bn|en|in)\s+"
_MARYAM = rf"(?:{_PATRONYMIC_LATIN}Maryam)|,?\s+(?:fils\s+de\s+Marie|son\s+of\s+Mary(?:am)?))"
_CHRIST = r"(?:-Christ)"

_LATIN_MUHAMMAD = _alt(
    r"(?:Muhammad|Mohammed|Mohamed|Mohammad|Mouhammad|Muhammed|Mohamad|Mahomet)"
    rf"(?:{_PATRONYMIC_LATIN}(?:Abd\s?-?[Aa]llah|Abdullah|Abdallah)))?",
    r"Rass?[uo]u?l(?:ullah|oullah|ulah|allah|\s+Allah)",
)
#: titles: Muhammad unless a prophet's name follows (« le Prophète Moïse »)
_LATIN_TITLE = _alt(
    r"(?:[Ll]e|[Dd]u|[Aa]u|[Nn]otre)\s+Prophète",
    rf"(?:[Ll]e|[Dd]u|[Aa]u|[Nn]otre|[Ss]on)\s+(?:Messager|Envoyé)(?:\s+d{_AP}\s?Allah)?",
    rf"(?:le|du|au|Le|Du|Au)\s+(?:messager|envoyé)\s+d{_AP}\s?Allah",
    rf"[Ll]{_AP}Envoyé(?:\s+d{_AP}\s?Allah)?",
    rf"(?:[Tt]he|[Oo]ur|[Hh]is)\s+(?:Prophet|Messenger(?:\s+of\s+Allah)?)(?:{_AP}s)?",
    rf"(?:[Tt]he)\s+messenger\s+of\s+Allah(?:{_AP}s)?",
)
_LATIN_PROPHETS = _alt(
    "Moussa",
    "Moïse",
    "Musa",
    "Moses",
    rf"(?:Issa|Îsa|Isa|Jésus|Jesus){_CHRIST}?{_MARYAM}?",
    "Ibrahim",
    "Abraham",
    "Nouh",
    "Noé",
    "Nuh",
    "Noah",
    "Youssouf",
    "Yusuf",
    "Joseph",
    "Souleymane",
    "Soulayman",
    "Sulayman",
    "Sulaiman",
    "Salomon",
    "Solomon",
    "Dawoud",
    "Daoud",
    "Dawud",
    "David",
    "Haroun",
    "Harun",
    "Aaron",
    "Yaqoub",
    "Yacoub",
    "Yaqub",
    "Jacob",
    "Ismaïl",
    "Ismail",
    "Ismaël",
    "Ishmael",
    "Ishaq",
    "Isaac",
    "Yahya",
    "Zakariya",
    "Zakariyya",
    "Zacharie",
    "Zechariah",
    "Ayyoub",
    "Ayoub",
    "Ayyub",
    "Job",
    "Younous",
    "Yunus",
    "Jonas",
    "Jonah",
    "Adam",
    "Idris",
    "Houd",
    "Hud",
    "Salih",
    "Chouayb",
    "Chouaïb",
    "Shuayb",
    rf"Shu{_AP}ayb",
    "Lout",
    "Loth",
    "Lut",
    "Lot",
)
_LATIN_COMPANIONS = _alt(
    r"(?:Abu|Abou)\s+Bakr(?:\s+(?:as|al|es)-?\s?Siddiq)?",
    rf"(?:Omar|Umar|Oumar|{_AP}Umar)(?:{_PATRONYMIC_LATIN}(?:al|el)-?\s?Khattab))?",
    rf"(?:Othmane|Othman|Outhmane|Uthman|{_AP}Uthman)(?:{_PATRONYMIC_LATIN}(?:Affan|{_AP}Affan)))?",
    rf"(?:Ali|{_AP}Ali)(?:{_PATRONYMIC_LATIN}(?:Abi|Abou|Abu)\s+Talib))?",
    rf"Bilal(?:{_PATRONYMIC_LATIN}Rabah))?",
    r"(?:Abu|Abou)\s+(?:Hurayra|Hurayrah|Hurairah|Hourayra|Hourayrah|Horeira)",
    rf"[Ii]bn\s+{_AP}?(?:Abbas|Abbâs)",
    rf"[Ii]bn\s+{_AP}?(?:Umar|Omar)",
    rf"Anas(?:{_PATRONYMIC_LATIN}Malik))?",
)
_LATIN_COMPANIONS_F = _alt(
    rf"(?:Aïcha|Aicha|Aisha|Aishah|Aïsha|{_AP}A[iï]sha)",
    "Khadija",
    "Khadidja",
    "Khadijah",
    "Fatima",
    "Fatimah",
)

#: a one-letter clitic glued to an Arabic name (« وعائشة », « لأبي بكر »)
_AR_CLITIC = "[وفبل]?"
_AR_MUHAMMAD = _alt(_AR_CLITIC + _ar("محمد") + "[اً]*", _AR_CLITIC + _ar("رسول الله"))
_AR_TITLE = _alt(_ar("النبي"), "[وفب]" + _ar("النبي"), _ar("للنبي"), _ar("نبينا"))
_AR_PROPHETS = _AR_CLITIC + _alt(
    *(
        _ar(name)
        for name in (
            "موسى",
            "عيسى ابن مريم",
            "عيسى بن مريم",
            "عيسى",
            "إبراهيم",
            "نوح",
            "يوسف",
            "سليمان",
            "داود",
            "هارون",
            "يعقوب",
            "إسماعيل",
            "إسحاق",
            "يحيى",
            "زكريا",
            "أيوب",
            "يونس",
            "آدم",
            "إدريس",
            "هود",
            "شعيب",
            "لوط",
        )
    )
)
_AR_COMPANIONS = _AR_CLITIC + _alt(
    *(
        _ar(name)
        for name in (
            "أبو بكر الصديق",
            "أبي بكر الصديق",
            "أبا بكر الصديق",
            "أبو بكر",
            "أبي بكر",
            "أبا بكر",
            "عمر بن الخطاب",
            "عثمان بن عفان",
            "عثمان",
            "علي بن أبي طالب",
            "بلال بن رباح",
            "بلال",
            "أبو هريرة",
            "أبي هريرة",
            "أبا هريرة",
            "ابن عباس",
            "ابن عمر",
            "أنس بن مالك",
        )
    )
)
_AR_COMPANIONS_F = _AR_CLITIC + _alt(*(_ar(name) for name in ("عائشة", "خديجة", "فاطمة الزهراء", "فاطمة")))


@dataclass(frozen=True)
class _Name:
    kind: str
    pattern: re.Pattern
    title: bool = False


_NAMES = {
    "latin": (
        _Name(MUHAMMAD, _words(_LATIN_MUHAMMAD)),
        _Name(MUHAMMAD, _words(_LATIN_TITLE), title=True),
        _Name(PROPHET, _words(_LATIN_PROPHETS)),
        _Name(COMPANION, _words(_LATIN_COMPANIONS)),
        _Name(COMPANION_F, _words(_LATIN_COMPANIONS_F)),
    ),
    "ar": (
        _Name(MUHAMMAD, _words(_AR_MUHAMMAD)),
        _Name(MUHAMMAD, _words(_AR_TITLE), title=True),
        _Name(PROPHET, _words(_AR_PROPHETS)),
        _Name(COMPANION, _words(_AR_COMPANIONS)),
        _Name(COMPANION_F, _words(_AR_COMPANIONS_F)),
    ),
}

# ------------------------------------------------------------------ formulas already said

_I = re.IGNORECASE
_SALAM = r"s+[ae]l(?:aa|a|â|e)m"
_FORMULAS_SAID = {
    MUHAMMAD: re.compile(
        "|".join(
            (
                "ﷺ",
                _START + r"(?-i:SAWS|SAW|SWS|PBUH)" + _END,
                r"\((?:saws|saw|sws|pbuh|s\.a\.w\.s?\.?)\)",
            )
        )
        + "|"
        + _START
        + _alt(
            r"s[ao]ll?[ae]?\s?-?\s?ll?ah?(?:ou|u)?",
            r"s[ao]ll?[ae]?\s?-?\s?all?ah?(?:ou|u)?",
            r"(?:paix|prière)\s+et\s+(?:le\s+)?salut",
            r"peace\s+and\s+blessings",
            rf"{_AP}?al[ae]y?h[ie]\s+(?:a?s-?\s?)?s+al[aâ]t",
            _ar("صلى الله عليه"),
            _ar("صلوا عليه"),
            _ar("صلى عليه"),
            _ar("عليه الصلاة"),
        )
        + _END,
        _I,
    ),
    PROPHET: re.compile(
        _START
        + _alt(
            r"(?:que\s+la\s+)?paix\s+(?:soit\s+)?sur\s+(?:lui|eux)",
            r"peace\s+be\s+upon\s+(?:him|them)",
            rf"{_AP}?al[ae]y?h[ie]?(?:ma|m|s)?\s*[-\s]?\s*(?:[ae][sl]?[-\s]?\s?)?{_SALAM}",
            _ar("عليه السلام"),
            _ar("عليهما السلام"),
            _ar("عليهم السلام"),
        )
        + _END
        + "|"
        + _START
        + r"(?-i:AS)"
        + _END
        + r"|\((?:as|a\.s\.?)\)",
        _I,
    ),
    ALLAH: re.compile(
        _START
        + _alt(
            r"subh?anahu",
            r"soubh?anahou",
            r"ta['’‘3]?[aâ]{1,2}l[aâ]",
            rf"{_AP}?azz?a\s+wa\s*-?\s*j[ae]ll?a?",
            r"jall?a\s+jall?aluh?u?",
            r"tabaraka\s+wa",
            r"(?:exalté|glorifié|loué)\s+soit-[Ii]l",
            r"(?:le\s+)?Très-Haut",
            r"(?:le\s+)?Puissant\s+et\s+(?:le\s+)?Majestueux",
            r"glorified\s+(?:and\s+exalted\s+)?(?:be|is)\s+He",
            r"(?:the\s+)?Most\s+High",
            r"the\s+Exalted",
            _ar("سبحانه"),
            _ar("تعالى"),
            _ar("عز وجل"),
            _ar("جل جلاله"),
            _ar("جل وعلا"),
            _ar("تبارك"),
        )
        + _END
        + "|"
        + _START
        + r"(?-i:SWT)"
        + _END
        + r"|\((?:swt|s\.w\.t\.?)\)",
        _I,
    ),
    COMPANION: re.compile(
        _START
        + _alt(
            rf"qu{_AP}\s?Allah\s+(?:l{_AP}|les\s+|la\s+)agrée",
            rf"qu{_AP}\s?Allah\s+(?:soit|est)\s+(?:satisfait|content)",
            r"may\s+Allah\s+be\s+pleased",
            r"radh?[iy]{1,2}[ae]?\s?-?\s?all?ah?(?:ou|u)?",
            _ar("رضي الله"),
            _ar("رضوان الله"),
        )
        + _END
        + "|"
        + _START
        + r"(?-i:RA)"
        + _END
        + r"|\((?:ra|r\.a\.?)\)",
        _I,
    ),
}
_FORMULAS_SAID[COMPANION_F] = _FORMULAS_SAID[COMPANION]
#: the Prophet's formula and the other prophets' are close enough to stand for each other
_SAID_FOR = {
    MUHAMMAD: (MUHAMMAD, PROPHET),
    PROPHET: (PROPHET, MUHAMMAD),
    ALLAH: (ALLAH,),
    COMPANION: (COMPANION,),
    COMPANION_F: (COMPANION,),
}

#: words before a name that make it someone else (a son, a kunya, a mosque, a surah, a family)
_LATIN_BEFORE = re.compile(
    r"(?:"
    + rf"(?<!{_W})(?:ibn|ben|bin|bint|abu|abou|abî|abi|umm|oum|oumm|frères?|sœur|soeur|brother|sister|"
    + r"cheikh|cheick|sheikh|shaykh|imam|imâm|mosquée|masjid|mosque|sourate|sourat|surah|sura|école|rue|"
    + r"avenue|bani|banu|banou|al|el|M\.|Mr\.?|Dr\.?|monsieur|madame)\s+"
    + rf"|(?<!{_W})(?:fils|fille|filles|enfants|descendants)\s+d{_AP}\s?"
    + rf"|(?<!{_W})(?:son|sons|children|daughter|daughters|family|famille)\s+(?:of|de)\s+"
    + r")$",
    _I,
)
_AR_BEFORE = re.compile(
    rf"(?<!{_W})"
    + _alt(*(_ar(word) for word in ("سورة", "مسجد", "بن", "ابن", "بنت", "أبو", "أبي", "أبا", "أم", "آل", "بني", "بنو")))
    + r"\s+$"
)
#: the prayer upon the Prophet is itself the formula (« Ô Allah, prie sur Muhammad », « اللهم صل على محمد »)
_PRAYER_BEFORE = re.compile(
    _alt(
        r"(?:prie|priez|prier|pray|send\s+blessings)\s+(?:sur|upon|on)\s+(?:(?:notre|our|le|the)\s+)?(?:Prophète\s+|Prophet\s+)?",
        _alt(_ar("صل"), _ar("صلى"), _ar("صلوا"), _ar("صلي"))
        + r"\s+"
        + _ar("على")
        + r"\s+(?:"
        + _ar("نبينا")
        + r"\s+)?",
    )
    + "$",
    _I,
)
#: a French quotation (« … », unclosed up to the end): most often a verse or a hadith, left as quoted
_QUOTED = re.compile(r"«[^»]*(?:»|$)")
#: a patronymic the name did not take: someone else (« Muhammad ibn Abd al-Wahhab »)
_LATIN_SON_OF = re.compile(r"\s+(?:ibn|ben|bin|b\.)\s", _I)
_AR_SON_OF = re.compile(r"\s+" + _alt(_ar("بن"), _ar("ابن")) + _END)
#: a capitalised word right after a Latin name is its surname (« Adam Smith », « Muhammad Ali »)
_SURNAME = re.compile(r"\s+[A-ZÀ-ÖØ-Þ][\w’'-]")


# ------------------------------------------------------------------ the pass


def enabled() -> bool:
    return (os.environ.get("SADA_HONORIFICS") or "").strip().lower() not in {"off", "0", "false", "no", "non"}


def vocalised(text: str) -> bool:
    """Fully vowelled Arabic: in a transcript, that is the Quran (speech comes out bare)."""
    letters = len(_ARABIC_LETTER.findall(text))
    return letters >= 12 and len(re.findall("[\u064b-\u0652]", text)) >= letters * 0.5


def _lang(lang: str | None) -> str:
    code = (lang or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGS else ""


def _said(text: str, end: int, kind: str) -> bool:
    """A formula of that kind already follows within a few words."""
    rest = text[end:]
    window = 0
    for count, match in enumerate(re.finditer(r"\S+", rest), 1):
        window = match.end()
        if count >= WINDOW_WORDS:
            break
    for said in _SAID_FOR[kind]:
        found = _FORMULAS_SAID[said].search(rest)
        if found and found.start() < window:
            return True
    return False


def _candidates(text: str, script: str) -> list[tuple[int, int, _Name]]:
    """Every name found, the longest kept where two overlap."""
    found = [(m.start(), m.end(), name) for name in _NAMES[script] for m in name.pattern.finditer(text)]
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, _Name]] = []
    for item in found:
        if kept and item[0] < kept[-1][1]:
            continue
        kept.append(item)
    return kept


def _spans(pattern: re.Pattern, text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _inside(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(a < end and start < b for a, b in spans)


def insertions(text: str, lang: str | None, mode: str = "full") -> list[tuple[int, str]]:
    """`(offset, formula)` to insert into `text`, offsets in increasing order; [] when nothing to add."""
    code = _lang(lang)
    if not code or not text or not enabled() or vocalised(text):
        return []
    mode = mode if mode in MODES else "full"
    script = "ar" if code == "ar" else "latin"
    before_rule = _AR_BEFORE if script == "ar" else _LATIN_BEFORE
    son_of = _AR_SON_OF if script == "ar" else _LATIN_SON_OF
    said_spans = [span for pattern in _FORMULAS_SAID.values() for span in _spans(pattern, text)]
    quoted = _spans(_QUOTED, text)
    found = _candidates(text, script)
    out: list[tuple[int, str]] = []
    for index, (start, end, name) in enumerate(found):
        if _inside(start, end, said_spans) or _inside(start, end, quoted):
            continue
        if before_rule.search(text[:start]) or son_of.match(text[end:]):
            continue
        if name.kind == MUHAMMAD and _PRAYER_BEFORE.search(text[:start]):
            continue
        if script == "latin" and not name.title and _SURNAME.match(text[end:]):
            continue
        following = found[index + 1] if index + 1 < len(found) else None
        if (
            name.title
            and following
            and following[2].kind in (MUHAMMAD, PROPHET)
            and not following[2].title
            and re.fullmatch(r"[\s,]*", text[end : following[0]])
        ):
            continue  # « le Prophète Moïse », « the Prophet Muhammad »: the name carries the formula
        if _said(text, end, name.kind):
            continue
        out.append((end, FORMULAS[name.kind][mode][code]))
    return out


def apply(text: str, lang: str | None, mode: str = "full") -> str:
    """`text` with its honorifics; unchanged for another language, or when disabled."""
    for offset, formula in reversed(insertions(text, lang, mode)):
        text = f"{text[:offset]} {formula}{text[offset:]}"
    return text


# ------------------------------------------------------------------ timestamped words

_TRAIL = ".,;:!?…»\"')]}"


def _split(word: str) -> tuple[str, str]:
    core = word.rstrip(_TRAIL)
    return core, word[len(core) :]


def _shown(word: dict) -> str:
    core, tail = _split(str(word.get("w") or ""))
    honorific = word.get("honorific") or ""
    return core + honorific + tail


def apply_words(words: list[dict], lang: str | None, mode: str = "full", *, inline: bool = False) -> list[dict]:
    """Honorifics of a run of timestamped words, as one text; the dicts given are not modified.

    The formula goes onto the word that ends the name, so it shares that word's timing and no
    word is added (the clips' page cutting and card intervals stay as they were). With `inline` it is
    written into the word itself (« Prophète (paix et salut sur lui), »); otherwise it is kept in
    `honorific`, shown by `glossary.display` after the word and its gloss.
    """
    out = [dict(word) for word in words]
    shown = [_shown(word) for word in out]
    starts, offset = [], 0
    for text in shown:
        starts.append(offset)
        offset += len(text) + 1
    for position, formula in insertions(" ".join(shown), lang, mode):
        index = max(i for i, start in enumerate(starts) if start < position) if position else 0
        word = out[index]
        core, tail = _split(str(word.get("w") or ""))
        local = position - starts[index]
        if local < len(core):  # the name ends inside the word: keep the rest after the formula
            tail = str(word.get("w") or "")[local:]
            core = str(word.get("w") or "")[:local]
        if inline:
            word["w"] = f"{core}{word.get('honorific') or ''} {formula}{tail}"
            word.pop("honorific", None)
        else:
            word["w"] = core + tail
            word["honorific"] = f"{word.get('honorific') or ''} {formula}"
    return out
