"""Imported screen template (`ecrans.html`): validation, per-mosque file, preview metadata.

The template produced by a chat assistant is a visual reference, not a broadcast path: the engine
keeps drawing the six screens with Pillow. What this module reads is the `#sada-screens` block —
the texts — so the mosque can find them in the settings form. The HTML file itself is kept as the
mosque's reference and shown in an iframe, same origin only.
"""
from __future__ import annotations

import datetime as dt
import re
import json
import os
import re
from pathlib import Path

from .. import paths
from .. import screens as base

# The repository's data folder; tests point this elsewhere.
DIR = paths.DATA / "screens"
MAX_BYTES = 512 * 1024
EXT = ".html"
# Slots the gabarit contract uses; the engine has a few more (title_cours, foot_cours...).
SLOTS = ("eyebrow", "title", "sub", "foot")

_BLOCK = re.compile(
    r"""<script\b[^>]*\bid\s*=\s*["']sada-screens["'][^>]*>(.*?)</script\s*>""",
    re.IGNORECASE | re.DOTALL,
)


class GabaritRefused(ValueError):
    """File refused, for a reason the administrator can read and fix."""


def path_for(mosque: dict) -> Path:
    """data/screens/<slug>/ecrans.html — one reference file per mosque."""
    return DIR / str(mosque["slug"]) / f"ecrans{EXT}"


def view(mosque: dict) -> dict:
    """What the interface needs to show the imported file; no path of the machine."""
    f = path_for(mosque)
    if not f.is_file():
        return {"present": False, "url": "", "name": f"ecrans{EXT}", "size": 0, "imported_at": ""}
    st = f.stat()
    return {
        "present": True,
        # Versioned by mtime: a new import is not masked by the browser cache.
        "url": f"/screens/{mosque['slug']}/ecrans{EXT}?v={int(st.st_mtime)}",
        "name": f"ecrans{EXT}",
        "size": st.st_size,
        "imported_at": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds"),
    }


ECRANS = ("avant", "priere", "entracte", "termine", "janaza", "dars_fin")


VARIABLES = ("heure", "date", "jour", "cours", "titre", "mosquee")


def remplir(page: str, valeurs: dict[str, str]) -> str:
    """Remplace les variables d'écran par leurs valeurs, comme le fait le moteur.

    Sans cela, l'aperçu du fichier affichait « Début à {heure} » quand le moteur affichait l'heure
    réelle : deux textes différents pour le même écran, et l'administrateur ne savait plus lequel
    croire.
    """
    for nom in VARIABLES:
        valeur = valeurs.get(nom) or ""
        page = page.replace("{" + nom + "}", valeur)
    return page


def lire(mosque: dict) -> str:
    """Le fichier importé, ou une chaîne vide si la mosquée n'en a pas encore déposé."""
    f = path_for(mosque)
    if not f.is_file():
        return ""
    return f.read_text(encoding="utf-8", errors="replace")


def page_antenne(mosque: dict, ecran: str, valeurs: dict[str, str]) -> str:
    """Le document que le moteur doit afficher : écran voulu visible, textes de la mosquée posés.

    C'est le même traitement que l'aperçu, appliqué au fichier conservé — l'antenne et l'aperçu ne
    peuvent donc pas diverger : ce sont les mêmes octets.
    """
    return remplir(pour_ecran(lire(mosque), ecran), valeurs)


def pour_ecran(page: str, ecran: str) -> str:
    """La même page, montrant un autre écran que celui que le fichier met en avant.

    Le modèle pose ``data-actif`` sur un seul écran et sa feuille de style n'affiche que celui-là.
    On déplace l'attribut : le fichier conservé ne bouge pas, l'aperçu suit l'onglet ouvert.
    """
    if ecran not in ECRANS:
        return page
    sans = re.sub(r"\s+data-actif(?=[\s>])", "", page)
    motif = re.compile(r"<[a-zA-Z][^>]*data-ecran=\"%s\"[^>]*>" % re.escape(ecran))
    m = motif.search(sans)
    if not m:
        return page                      # écran absent du fichier : on ne devine pas
    balise = m.group(0)
    return sans[:m.start()] + balise[:-1] + " data-actif>" + sans[m.end():]


def parse(content: bytes, filename: str) -> tuple[dict[str, dict[str, str]], list[dict], list[str]]:
    """Reads and checks a template.

    Returns `(texts, ignored, empty)`: the supported non-empty texts to apply, each ignored value
    with its reason, and the fields left empty by the file (`écran.champ`). Raises GabaritRefused
    when the file is not a usable template.
    """
    name = os.path.basename(filename or "")
    if Path(name).suffix.lower() != EXT:
        raise GabaritRefused(f"Format refusé : un fichier {EXT} est attendu.")
    if not content:
        raise GabaritRefused("Fichier vide.")
    if len(content) > MAX_BYTES:
        raise GabaritRefused(f"Fichier trop volumineux ({MAX_BYTES // 1024} Ko maximum).")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise GabaritRefused("Fichier illisible : encodage UTF-8 attendu.") from None
    head = text[:4096].lower()
    if "<html" not in head and "<!doctype" not in head:
        raise GabaritRefused("Ce fichier n'est pas une page HTML.")

    m = _BLOCK.search(text)
    if not m:
        raise GabaritRefused(
            "Ce fichier n'est pas un gabarit Sadā : le bloc "
            "<script id=\"sada-screens\"> est introuvable."
        )
    try:
        data = json.loads(m.group(1).strip())
    except json.JSONDecodeError as e:
        raise GabaritRefused(
            f"Le bloc #sada-screens n'est pas un JSON valide (ligne {e.lineno}, colonne {e.colno})."
        ) from None
    if not isinstance(data, dict):
        raise GabaritRefused("Le bloc #sada-screens doit contenir un objet JSON.")

    missing = [s for s in base.SCREENS if s not in data]
    if missing:
        raise GabaritRefused(
            "Gabarit incomplet : les écrans " + ", ".join(missing) + " manquent dans le bloc #sada-screens."
        )

    taken: dict[str, dict[str, str]] = {}
    ignored: list[dict] = []
    empty: list[str] = []
    for screen, block in data.items():
        if screen not in base.SCREENS:
            ignored.append({"screen": str(screen), "slot": "", "value": "",
                            "reason": "écran inconnu du moteur"})
            continue
        if not isinstance(block, dict):
            raise GabaritRefused(f"L'écran « {screen} » doit être un objet {{\"id\": …, \"texts\": …}}.")
        # The contract wraps texts in "texts"; a flat object is accepted as well.
        texts = dict(block["texts"] if isinstance(block.get("texts"), dict) else block)
        for slot, value in texts.items():
            if not isinstance(value, str):
                ignored.append({"screen": screen, "slot": str(slot), "value": "",
                                "reason": "valeur non textuelle (une chaîne est attendue)"})
                continue
            texte = value.replace("\n", " ").strip()
            if not texte:
                empty.append(f"{screen}.{slot}")
                continue
            if slot not in base.DEFAULTS[screen]:
                raison = (f"l'écran « {screen} » n'a pas de champ « {slot} » dans le moteur"
                          if slot in SLOTS else f"champ inconnu du moteur : « {slot} »")
                ignored.append({"screen": screen, "slot": slot, "value": texte, "reason": raison})
                continue
            if len(texte) > base.MAX_LEN:
                ignored.append({"screen": screen, "slot": slot, "value": texte[:120] + "…",
                                "reason": f"texte trop long ({base.MAX_LEN} caractères maximum)"})
                continue
            taken.setdefault(screen, {})[slot] = texte
    return taken, ignored, empty


def store(mosque: dict, content: bytes) -> Path:
    """Writes the reference file atomically; the previous one, if any, is replaced."""
    f = path_for(mosque)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, f)
    return f


def summary(taken: dict, ignored: list, empty: list) -> str:
    """One French sentence for the interface: what was taken, what was not, and why not."""
    n = sum(len(v) for v in taken.values())
    bits = [f"{n} texte{'s' if n != 1 else ''} repris"]
    if ignored:
        bits.append(f"{len(ignored)} ignoré{'s' if len(ignored) != 1 else ''}")
    if empty:
        bits.append(f"{len(empty)} champ{'s' if len(empty) != 1 else ''} vide"
                    f"{'s' if len(empty) != 1 else ''} laissé{'s' if len(empty) != 1 else ''} tel quel")
    return ("Gabarit importé : " + ", ".join(bits)
            + ". Le fichier devient la référence visuelle de la mosquée ; le direct garde le rendu du moteur.")
