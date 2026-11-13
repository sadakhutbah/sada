"""Langue du discours : un code ISO-639-1, mesuré sur toute la durée.

Les moteurs ne parlent pas la même langue. faster-whisper rend un code (« ar », « fr ») mesuré
sur les 30 premières secondes ; les API distantes (Groq, OpenAI) rendent le NOM complet
(« English », « french ») — et se trompent sur une khutba qui mêle arabe et français. L'aval
(détection du discours, reprise des citations, sous-titres, traductions) attend un code ISO.

D'où ce module, seul endroit qui :

- traduit nom → code ISO-639-1 (`code_of`) ;
- mesure la langue sur les segments de toute la durée (`measure`), en comptant l'arabe écrit en
  lettres latines (`translit_ratio`) comme de l'arabe : le distant rend souvent le même propos
  en translittération là où le local écrit l'écriture arabe.

Les deux moteurs passent par `measure` : un même audio donne la même langue des deux côtés.
"""

from __future__ import annotations

from ..text import arabic_ratio, is_translit_word

# Langues de Whisper (large-v3) : ce que l'API peut rendre en clair. Les noms sont ceux du
# modèle, en minuscules.
_NAME_CODES = """
en English
zh Chinese
de German
es Spanish
ru Russian
ko Korean
fr French
ja Japanese
pt Portuguese
tr Turkish
pl Polish
ca Catalan
nl Dutch
ar Arabic
sv Swedish
it Italian
id Indonesian
hi Hindi
fi Finnish
vi Vietnamese
he Hebrew
uk Ukrainian
el Greek
ms Malay
cs Czech
ro Romanian
da Danish
hu Hungarian
ta Tamil
no Norwegian
th Thai
ur Urdu
hr Croatian
bg Bulgarian
lt Lithuanian
la Latin
mi Maori
ml Malayalam
cy Welsh
sk Slovak
te Telugu
fa Persian
lv Latvian
bn Bengali
sr Serbian
az Azerbaijani
sl Slovenian
kn Kannada
et Estonian
mk Macedonian
br Breton
eu Basque
is Icelandic
hy Armenian
ne Nepali
mn Mongolian
bs Bosnian
kk Kazakh
sq Albanian
sw Swahili
gl Galician
mr Marathi
pa Punjabi
si Sinhala
km Khmer
sn Shona
yo Yoruba
so Somali
af Afrikaans
oc Occitan
ka Georgian
be Belarusian
tg Tajik
sd Sindhi
gu Gujarati
am Amharic
yi Yiddish
lo Lao
uz Uzbek
fo Faroese
ht Haitian Creole
ps Pashto
tk Turkmen
nn Nynorsk
mt Maltese
sa Sanskrit
lb Luxembourgish
my Myanmar
bo Tibetan
tl Tagalog
mg Malagasy
as Assamese
tt Tatar
haw Hawaiian
ln Lingala
ha Hausa
ba Bashkir
jw Javanese
su Sundanese
yue Cantonese
"""

LANGUAGE_NAMES = {
    name.strip().lower(): code for code, name in (line.split(None, 1) for line in _NAME_CODES.strip().splitlines())
}

# Codes ISO-639-2 que certains fournisseurs renvoient à la place du nom.
_ALPHA3 = {
    "ara": "ar",
    "fre": "fr",
    "fra": "fr",
    "eng": "en",
    "spa": "es",
    "deu": "de",
    "ger": "de",
    "ita": "it",
    "nld": "nl",
    "dut": "nl",
    "por": "pt",
    "rus": "ru",
    "tur": "tr",
    "urd": "ur",
    "hin": "hi",
    "ben": "bn",
    "fas": "fa",
    "per": "fa",
    "heb": "he",
    "tam": "ta",
    "tel": "te",
}

TRANSLIT_SURE = 0.5  # part de mots translittérés : le segment est de l'arabe, pas du français


def code_of(value) -> str:
    """Code ISO-639-1 d'une langue nommée par un moteur : « English » -> « en », « ar » -> « ar ».

    Rend « » quand la langue n'est pas reconnue : l'appelant décide alors quoi écrire, plutôt que
    de propager un nom qui ne veut rien dire en aval.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if len(text) == 2 and text.isalpha():
        return text
    if text in _ALPHA3:
        return _ALPHA3[text]
    return LANGUAGE_NAMES.get(text, "")


def translit_ratio(text: str) -> float:
    """Part des mots qui ressemblent à de l'arabe écrit en lettres latines (« yaqul », « al-talaq »)."""
    words = [w for w in (text or "").split() if w.strip('.,;:!?«»"()')]
    if not words:
        return 0.0
    return sum(1 for w in words if is_translit_word(w)) / len(words)


def reads_arabic(text: str) -> bool:
    """Le segment dit de l'arabe, en écriture arabe ou en translittération."""
    return arabic_ratio(text) > 0.5 or translit_ratio(text) >= TRANSLIT_SURE


def arabic_share(segs: list[dict]) -> tuple[float, float]:
    """(secondes d'arabe, secondes de parole) : la mesure porte sur la durée, pas sur le nombre de segments."""
    arabic = 0.0
    total = 0.0
    for seg in segs:
        length = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
        total += length
        if reads_arabic(seg.get("text") or ""):
            arabic += length
    return arabic, total


def dominant_language(segs: list[dict], detected: str = "") -> str:
    """Langue du discours sur toute la durée (voir `measure`, dont c'est le seul code)."""
    return measure(segs, detected)[0]


def measure(segs: list[dict], detected: str = "") -> tuple[str, float]:
    """(code ISO-639-1, probabilité) de la langue du discours, mesurés sur toute la durée.

    La probabilité est la part de la durée de parole tenue dans cette langue : 0.52 pour une
    khutba qui alterne arabe et français, 1.0 pour un discours d'une seule langue. Elle ne dépend
    donc pas de la confiance — très variable — que chaque moteur attache à sa détection initiale,
    et les deux moteurs rendent le même nombre sur le même audio.
    """
    arabic, total = arabic_share(segs)
    if not total:
        return code_of(detected) or "und", 0.0
    share = arabic / total
    if share >= 0.5:
        return "ar", round(share, 3)
    code = code_of(detected)
    if code in ("", "ar"):
        code = "fr"
    return code, round(1.0 - share, 3)
