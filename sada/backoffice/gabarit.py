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

from ..i18n import _
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
        raise GabaritRefused(_("Format refused: an {ext} file is expected.", ext=EXT))
    if not content:
        raise GabaritRefused(_("Empty file."))
    if len(content) > MAX_BYTES:
        raise GabaritRefused(_("File too large ({size} KB maximum).", size=MAX_BYTES // 1024))
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise GabaritRefused(_("Unreadable file: UTF-8 encoding expected.")) from None
    head = text[:4096].lower()
    if "<html" not in head and "<!doctype" not in head:
        raise GabaritRefused(_("This file is not an HTML page."))

    m = _BLOCK.search(text)
    if not m:
        raise GabaritRefused(
            _("This file is not a Sadā template: the block "
              "<script id=\"sada-screens\"> cannot be found.")
        )
    try:
        data = json.loads(m.group(1).strip())
    except json.JSONDecodeError as e:
        raise GabaritRefused(
            _("The #sada-screens block is not valid JSON (line {line}, column {col}).",
              line=e.lineno, col=e.colno)
        ) from None
    if not isinstance(data, dict):
        raise GabaritRefused(_("The #sada-screens block must contain a JSON object."))

    missing = [s for s in base.SCREENS if s not in data]
    if missing:
        raise GabaritRefused(
            _("Incomplete template: the screens {screens} are missing from the #sada-screens block.",
              screens=", ".join(missing))
        )

    taken: dict[str, dict[str, str]] = {}
    ignored: list[dict] = []
    empty: list[str] = []
    for screen, block in data.items():
        if screen not in base.SCREENS:
            ignored.append({"screen": str(screen), "slot": "", "value": "",
                            "reason": _("unknown screen for the engine")})
            continue
        if not isinstance(block, dict):
            raise GabaritRefused(_("Screen \"{screen}\" must be an object {{\"id\": …, \"texts\": …}}.",
                                   screen=screen))
        # The contract wraps texts in "texts"; a flat object is accepted as well.
        texts = dict(block["texts"] if isinstance(block.get("texts"), dict) else block)
        for slot, value in texts.items():
            if not isinstance(value, str):
                ignored.append({"screen": screen, "slot": str(slot), "value": "",
                                "reason": _("non-text value (a string is expected)")})
                continue
            texte = value.replace("\n", " ").strip()
            if not texte:
                empty.append(f"{screen}.{slot}")
                continue
            if slot not in base.DEFAULTS[screen]:
                raison = (_("screen \"{screen}\" has no field \"{slot}\" in the engine",
                            screen=screen, slot=slot) if slot in SLOTS
                          else _("unknown field for the engine: \"{slot}\"", slot=slot))
                ignored.append({"screen": screen, "slot": slot, "value": texte, "reason": raison})
                continue
            if len(texte) > base.MAX_LEN:
                ignored.append({"screen": screen, "slot": slot, "value": texte[:120] + "…",
                                "reason": _("text too long ({max} characters maximum)", max=base.MAX_LEN)})
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
    """One sentence for the interface: what was taken, what was not, and why not."""
    n = sum(len(v) for v in taken.values())
    bits = [_("texts kept: {n}", n=n)]
    if ignored:
        bits.append(_("ignored: {n}", n=len(ignored)))
    if empty:
        bits.append(_("empty fields left as is: {n}", n=len(empty)))
    return _("Template imported: {parts}. The file becomes the mosque's visual reference; "
             "the live keeps the engine's rendering.", parts=", ".join(bits))
