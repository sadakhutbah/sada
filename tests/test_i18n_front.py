"""Textes du front (web/src/i18n.ts) : aucune clé ne doit pouvoir s'afficher à l'écran.

Le front n'a pas de suite de tests JavaScript : on lit le fichier de traductions comme du texte
et on en reconstruit l'arbre des clés (littéraux d'objet, chaînes et commentaires seulement).
"""
from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
I18N = RACINE / "web" / "src" / "i18n.ts"
SRC = RACINE / "web" / "src"
PIPELINE = RACINE / "minbar" / "pipeline"


def _objet(texte: str, i: int) -> tuple[dict, int]:
    """Lit le littéral d'objet qui commence à `texte[i] == "{"` ; rend (arbre, position après)."""
    assert texte[i] == "{"
    i += 1
    arbre: dict = {}
    while True:
        i = _blancs(texte, i)
        if texte[i] == "}":
            return arbre, i + 1
        if texte[i] in "\"'":
            cle, i = _chaine(texte, i)
        else:
            m = re.compile(r"[A-Za-z_$][\w$]*").match(texte, i)
            if m is None:
                raise AssertionError(f"clé illisible près de : {texte[i:i + 40]!r}")
            cle, i = m.group(0), m.end()
        i = _blancs(texte, i)
        assert texte[i] == ":", f"« : » attendu après {cle!r}"
        i = _blancs(texte, i + 1)
        if texte[i] == "{":
            valeur, i = _objet(texte, i)
        elif texte[i] in "\"'`":
            valeur, i = _chaine(texte, i)
        else:
            m = re.compile(r"[^,}\n]+").match(texte, i)
            if m is None:
                raise AssertionError(f"valeur illisible pour {cle!r}")
            valeur, i = m.group(0).strip(), m.end()
        arbre[cle] = valeur
        i = _blancs(texte, i)
        if texte[i] == ",":
            i += 1


def _blancs(texte: str, i: int) -> int:
    """Saute espaces et commentaires."""
    while True:
        while texte[i].isspace():
            i += 1
        if texte.startswith("//", i):
            i = texte.index("\n", i)
        elif texte.startswith("/*", i):
            i = texte.index("*/", i) + 2
        else:
            return i


def _chaine(texte: str, i: int) -> tuple[str, int]:
    q, j, morceaux = texte[i], i + 1, []
    while texte[j] != q:
        if texte[j] == "\\":
            morceaux.append(texte[j + 1])
            j += 2
        else:
            morceaux.append(texte[j])
            j += 1
    return "".join(morceaux), j + 1


def _langue(nom: str) -> dict:
    texte = I18N.read_text(encoding="utf-8")
    m = re.search(rf"^const {nom}(?::[^=]+)? = \{{", texte, re.M)
    assert m, f"objet `{nom}` introuvable dans i18n.ts"
    return _objet(texte, m.end() - 1)[0]


def _chemins(arbre: dict, prefixe: str = "") -> set[str]:
    out: set[str] = set()
    for cle, valeur in arbre.items():
        chemin = f"{prefixe}{cle}"
        out |= _chemins(valeur, chemin + ".") if isinstance(valeur, dict) else {chemin}
    return out


def _genres_du_pipeline() -> set[str]:
    """Genres de clip réellement produits : `add("verset", …)` et les fenêtres `{"kind": "…", "start": …}`.

    Les sous-titres ont aussi un `kind` (speech, verse…) mais sans `start` : ils ne sont pas retenus.
    """
    genres: set[str] = set()
    for f in PIPELINE.glob("*.py"):
        code = f.read_text(encoding="utf-8")
        genres |= set(re.findall(r"\badd\(\s*[\"'](\w+)[\"']", code))
        genres |= set(re.findall(r"[\"']kind[\"']:\s*[\"'](\w+)[\"'],\s*[\"']start[\"']", code))
    return genres


def test_les_deux_langues_ont_les_memes_cles():
    """Une clé ajoutée en français doit l'être en anglais, et réciproquement."""
    fr, en = _chemins(_langue("fr")), _chemins(_langue("en"))
    assert len(fr) > 100                              # le lecteur a bien parcouru tout l'arbre
    assert sorted(fr - en) == [], "manquent en anglais"
    assert sorted(en - fr) == [], "manquent en français"


def test_chaque_genre_de_clip_du_serveur_a_un_libelle():
    """« Clip 1 · clips.kinds.modele » : tout genre que le pipeline écrit en base a son libellé."""
    genres = _genres_du_pipeline()
    assert {"extrait"} <= genres                 # l'extraction voit bien le code
    for nom in ("fr", "en"):
        libelles = _langue(nom)["clips"]["kinds"]
        manquants = sorted(g for g in genres if not libelles.get(g))
        assert manquants == [], f"genres sans libellé ({nom}) : {manquants}"


def test_les_cles_ecrites_en_dur_dans_les_ecrans_existent():
    """Un `t("a.b")` littéral dans un composant doit désigner un texte de la table française."""
    fr = _chemins(_langue("fr"))
    fr |= {re.sub(r"_(zero|one|two|few|many|other)$", "", c) for c in fr}   # pluriels i18next
    absentes = []
    for f in list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")):
        code = f.read_text(encoding="utf-8")
        for cle in re.findall(r"\bt\(\s*\"([A-Za-z_][\w]*(?:\.[\w]+)+)\"", code):
            if cle not in fr:
                absentes.append(f"{f.relative_to(SRC)} : {cle}")
    assert absentes == []


def test_une_cle_absente_ne_s_affiche_jamais_telle_quelle():
    """Le repli global d'i18next est en place : une valeur nouvelle donne un mot lisible, pas la clé."""
    texte = I18N.read_text(encoding="utf-8")
    assert "parseMissingKeyHandler" in texte
    ecrans = "".join(f.read_text(encoding="utf-8") for f in (SRC / "pages").glob("*.tsx"))
    # Les genres passent par l'appel simple : plus de repli local qui rendrait la valeur brute.
    assert "clips.kinds.${c.kind}`, {" not in ecrans
