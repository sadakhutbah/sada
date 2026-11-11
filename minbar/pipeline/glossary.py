"""Glossaire des termes islamiques translittérés : vocabulaire donné à Whisper, gloses des sous-titres.

Deux usages, un seul fichier de référence :

- `hotwords()` : la liste des termes est passée à Whisper pour qu'il écrive « tawba » plutôt que
  « taouba » ou un mot français approchant (faster-whisper accepte `hotwords`) ;
- `gloss_words()` : dans les sous-titres français, la première occurrence d'un terme non évident
  reçoit sa traduction entre parenthèses — « la tawba (le repentir) » — sans toucher au minutage.

Les termes par défaut sont dans ce module. Une mosquée peut les compléter ou les corriger avec un
fichier JSON (réglage `MINBAR_GLOSSARY`, sinon `data/glossary.json`) :

    {"terms": [{"term": "tawba", "variants": ["tawba", "taouba"], "fr": "le repentir"}]}

Un terme déjà connu est remplacé (la traduction et les variantes du fichier gagnent) ; les autres
sont ajoutés. Fichier absent ou illisible : les termes par défaut, sans erreur.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

from .. import paths

# (terme, variantes d'orthographe courantes, traduction française courte)
DEFAULT_TERMS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("tawba", ("tawba", "tawbah", "taouba", "taoubah", "tawbat"), "le repentir"),
    ("istighfar", ("istighfar", "istighfare", "istighfar"), "la demande de pardon"),
    ("taqwa", ("taqwa", "taqwah", "takwa"), "la piété, la crainte d'Allah"),
    ("iman", ("iman", "imane", "eemane"), "la foi"),
    ("ihsan", ("ihsan", "ihsane", "ihsaan"), "l'excellence spirituelle"),
    ("sunna", ("sunna", "sunnah", "sounna"), "la tradition prophétique"),
    ("hadith", ("hadith", "hadiths", "hadeeth"), "la parole prophétique"),
    ("dhikr", ("dhikr", "zikr", "dikr"), "l'évocation d'Allah"),
    ("du'a", ("du'a", "dua", "dou'a", "doua", "dou'a"), "l'invocation"),
    ("salat", ("salat", "salah", "salate", "salât"), "la prière"),
    ("zakat", ("zakat", "zakah", "zakat", "zakate"), "l'aumône obligatoire"),
    ("sabr", ("sabr",), "la patience"),
    ("tawhid", ("tawhid", "tawheed", "tawhide"), "l'unicité d'Allah"),
    ("akhira", ("akhira", "akhirah", "akhirat"), "l'au-delà"),
    ("dunya", ("dunya", "dounya", "dunia"), "la vie d'ici-bas"),
    ("jannah", ("jannah", "janna", "djannah"), "le paradis"),
    ("jahannam", ("jahannam", "jahanam", "djahanam"), "l'enfer"),
    ("khutba", ("khutba", "khutbah", "khotba", "khoutba"), "le sermon"),
    ("dars", ("dars", "darse", "ders"), "le cours"),
    ("rasulullah", ("rasulullah", "rasul allah", "rasoulallah"), "le Messager d'Allah"),
    ("sallallahu alayhi wa sallam", ("sallallahu alayhi wa sallam", "sallallahou alayhi wa sallam",
                                     "sallallahu alayhi wasallam"), "que la paix et la bénédiction d'Allah soient sur lui"),
    ("subhanallah", ("subhanallah", "subhan allah", "sobhanallah"), "gloire à Allah"),
    ("al-hamdu lillah", ("al-hamdu lillah", "al hamdu lillah", "alhamdulillah", "el hamdoulillah"),
     "louange à Allah"),
    ("in sha Allah", ("in sha allah", "inshallah", "inchallah", "incha allah"), "si Allah le veut"),
    ("mashallah", ("mashallah", "ma cha allah", "machaallah"), "ce qu'Allah a voulu"),
    ("bismillah", ("bismillah", "bismi allah", "bismillah"), "au nom d'Allah"),
    ("astaghfirullah", ("astaghfirullah", "astaghfiroullah"), "je demande pardon à Allah"),
    ("allahu akbar", ("allahu akbar", "allahou akbar"), "Allah est le plus grand"),
    ("la ilaha illa Allah", ("la ilaha illa allah", "la ilaha illallah"), "il n'y a de dieu qu'Allah"),
    ("sadaqa", ("sadaqa", "sadaqah", "sadaqua"), "l'aumône surérogatoire"),
    ("sawm", ("sawm", "saoum"), "le jeûne"),
    ("siyam", ("siyam", "siyame"), "le jeûne"),
    ("hajj", ("hajj", "hadj", "haj"), "le pèlerinage"),
    ("umra", ("umra", "omra", "umrah"), "le pèlerinage mineur"),
    ("shahada", ("shahada", "chahada", "shahadah"), "le témoignage de foi"),
    ("halal", ("halal", "hallal"), "licite"),
    ("haram", ("haram",), "illicite"),
    ("fatwa", ("fatwa", "fatwas"), "l'avis juridique"),
    ("khatib", ("khatib", "khateeb", "khatibe"), "le prédicateur"),
    ("jumu'a", ("jumu'a", "jumua", "joumou'a", "joumoua"), "la prière du vendredi"),
    ("aïd", ("aïd", "aid", "eid"), "la fête"),
    ("rizq", ("rizq", "rizk"), "la subsistance"),
    ("nafs", ("nafs", "nafss"), "l'âme charnelle"),
    ("fitna", ("fitna", "fitnah"), "la discorde, l'épreuve"),
    ("bid'a", ("bid'a", "bida", "bidah"), "l'innovation religieuse"),
    ("kufr", ("kufr", "koufr", "kufur"), "la mécréance"),
    ("shirk", ("shirk", "chirk", "shirq"), "l'association à Allah"),
    ("tafsir", ("tafsir", "tafseer", "tafsire"), "l'exégèse"),
    ("fiqh", ("fiqh", "feqh", "fikh"), "le droit musulman"),
    ("aqida", ("aqida", "aqidah", "aquida"), "la croyance"),
    ("adhan", ("adhan", "athan", "azan"), "l'appel à la prière"),
    ("qibla", ("qibla", "qiblah", "kibla"), "la direction de la prière"),
    ("wudu", ("wudu", "woudou", "wudhu", "oudou"), "les ablutions"),
    ("ghusl", ("ghusl", "ghousl", "ghusul"), "le bain rituel"),
    ("janaza", ("janaza", "janazah", "djanaza"), "la prière funéraire"),
    ("shukr", ("shukr", "choukour", "shukour"), "la reconnaissance"),
    ("rahma", ("rahma", "rahmah", "rahama"), "la miséricorde"),
    ("baraka", ("baraka", "barakah", "barraqa"), "la bénédiction"),
    ("ummah", ("ummah", "oumma", "umma"), "la communauté"),
    ("minbar", ("minbar", "mimbar"), "la chaire"),
    ("tarawih", ("tarawih", "taraweeh", "tarawih"), "les prières nocturnes du Ramadan"),
    ("i'tikaf", ("i'tikaf", "itikaf", "i'tikâf"), "la retraite spirituelle"),
    ("witr", ("witr", "witre"), "la prière impaire"),
    ("sujud", ("sujud", "sujoud", "soujoud"), "la prosternation"),
    ("ruku", ("ruku", "roukou", "roukou'"), "l'inclinaison"),
    ("rak'a", ("rak'a", "rakaa", "rakat", "raka'a"), "une unité de prière"),
    ("tayammum", ("tayammum", "tayamoum"), "les ablutions sèches"),
    ("nifaq", ("nifaq", "nifak"), "l'hypocrisie"),
    ("mumin", ("mumin", "moumin", "mu'min"), "le croyant"),
)

_PUNCT = " \t\n\r.,;:!?…«»\"()[]{}—–-"


def normalize(text: str) -> str:
    """Clé de comparaison : minuscules, sans accents, apostrophes et espaces normalisés."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("’", "'").replace("‘", "'").replace("`", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _entry(term: str, variants, fr: str) -> dict:
    variants = tuple(v for v in dict.fromkeys([term, *(variants or ())]) if str(v).strip())
    return {"term": str(term).strip(), "variants": variants, "fr": str(fr).strip()}


def default_terms() -> list[dict]:
    return [_entry(term, variants, fr) for term, variants, fr in DEFAULT_TERMS]


def config_path() -> Path:
    return Path(os.environ.get("MINBAR_GLOSSARY") or paths.DATA / "glossary.json")


def _extra_terms(path: Path | None = None) -> list[dict]:
    p = path or config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("terms") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        term = str(it.get("term") or "").strip()
        fr = str(it.get("fr") or "").strip()
        if term and fr:
            out.append(_entry(term, it.get("variants") or (), fr))
    return out


def load(path: Path | None = None) -> list[dict]:
    """Termes en vigueur : ceux du fichier remplacent les termes connus, les autres s'ajoutent."""
    terms = {e["term"]: e for e in default_terms()}
    for e in _extra_terms(path):
        terms[e["term"]] = e
    return list(terms.values())


def hotwords(terms: list[dict] | None = None) -> str:
    """Liste donnée à Whisper (`hotwords`) pour stabiliser l'orthographe des termes."""
    terms = terms if terms is not None else load()
    return ", ".join(e["term"] for e in terms if e.get("term"))


def _index(terms: list[dict]) -> dict[str, dict]:
    idx = {}
    for e in terms:
        for v in e.get("variants") or ():
            key = normalize(v)
            if key:
                idx.setdefault(key, e)
    return idx


MAX_TERM_WORDS = 5


def gloss_words(words: list[dict], terms: list[dict] | None = None) -> list[dict]:
    """Ajoute la traduction d'un terme à sa première occurrence dans le clip.

    `words` : les mots du clip (horodatés) ; les dicts ne sont pas modifiés. La glose est collée au
    dernier mot du terme (« tawba (le repentir) ») : elle apparaît avec le mot surligné, à son
    minutage, et le découpage en pages la prend en compte comme du texte normal.
    """
    terms = terms if terms is not None else load()
    idx = _index(terms)
    out = [dict(w) for w in words]
    used: set[str] = set()
    i = 0
    while i < len(out):
        for n in range(min(MAX_TERM_WORDS, len(out) - i), 0, -1):
            key = normalize(" ".join(str(w.get("w") or "") for w in out[i:i + n]))
            e = idx.get(key) if key else None
            if not e or e["term"] in used:
                continue
            used.add(e["term"])
            last = out[i + n - 1]
            raw = str(last.get("w") or "")
            core = raw.rstrip(_PUNCT)
            last["w"] = core + f" ({e['fr']})" + raw[len(core):]   # ponctuation conservée après la glose
            i += n - 1
            break
        i += 1
    return out
