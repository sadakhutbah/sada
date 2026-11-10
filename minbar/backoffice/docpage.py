"""Page de documentation : rendu Markdown minimal, sans dépendance.

Les documents du dossier ``docs/`` sont écrits en Markdown pour rester modifiables et collables
dans un chat. Mais un administrateur qui clique sur « Mode d'emploi » doit tomber sur une page
lisible, pas sur du texte brut avec des dièses. Ce module rend le sous-ensemble Markdown que ces
documents utilisent : titres, paragraphes, listes, tableau, citations, filets, gras, code et liens.

Aucune bibliothèque : le fichier est petit, la dépendance ne le justifierait pas.
"""

from __future__ import annotations

import html
import re

# Palette et polices du back-office, recopiées telles quelles pour que la page ait l'air d'en faire
# partie sans dépendre du build du front.
_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --paper: #faf9f4; --paper-2: #f1f4ea; --ink: #17261c; --muted: #5a695e;
    --line: #d8e0d0; --deep: #1e6b35; --deeper: #154d27; --night: #0f2a19; --leaf: #8dc63f;
  }}
  * {{ box-sizing: border-box; }}
  html {{ background: var(--paper); }}
  body {{
    margin: 0; padding: 0; color: var(--ink); background: var(--paper);
    font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .banniere {{
    position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 14px;
    padding: 14px max(24px, calc((100% - 780px) / 2)); background: var(--night);
    color: #e7efe0; border-bottom: 3px solid var(--leaf);
  }}
  .banniere .marque {{ font-weight: 700; letter-spacing: .02em; }}
  .banniere .filet {{ width: 1px; height: 16px; background: rgba(231,239,224,.3); }}
  .banniere a {{ color: #d5e2cf; text-decoration: none; font-size: .9rem; }}
  .banniere a:hover {{ text-decoration: underline; }}
  main {{ max-width: 780px; margin: 0 auto; padding: 40px 24px 96px; }}
  h1 {{ font-size: 2rem; line-height: 1.2; margin: 0 0 .6rem; letter-spacing: -.01em; }}
  h2 {{
    font-size: 1.22rem; margin: 2.6rem 0 .8rem; padding-top: 1.4rem;
    border-top: 1px solid var(--line); letter-spacing: -.005em;
  }}
  h3 {{ font-size: 1.02rem; margin: 1.6rem 0 .5rem; }}
  p {{ margin: 0 0 .9rem; }}
  strong {{ font-weight: 650; }}
  em {{ color: var(--muted); }}
  ul, ol {{ margin: 0 0 1rem; padding-left: 1.35rem; }}
  li {{ margin: .3rem 0; }}
  li::marker {{ color: var(--deep); }}
  hr {{ border: 0; border-top: 1px solid var(--line); margin: 2rem 0; }}
  a {{ color: var(--deep); text-decoration: underline; text-decoration-color: var(--line);
       text-underline-offset: 3px; }}
  a:hover {{ text-decoration-color: var(--deep); }}
  code {{
    font: .88em/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--paper-2);
    border: 1px solid var(--line); border-radius: 4px; padding: .06em .32em;
  }}
  blockquote {{
    margin: 0 0 1rem; padding: .6rem .9rem; background: var(--paper-2);
    border-left: 3px solid var(--leaf); border-radius: 0 6px 6px 0; color: var(--deeper);
  }}
  blockquote p:last-child {{ margin-bottom: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0 0 1.2rem; font-size: .93rem; }}
  th, td {{ text-align: left; padding: .5rem .7rem; border-bottom: 1px solid var(--line);
            vertical-align: top; }}
  th {{ background: var(--paper-2); font-weight: 650; color: var(--deeper); }}
  tr:last-child td {{ border-bottom: 0; }}
  .avis {{
    margin: 0 0 1.4rem; padding: .6rem .9rem; border: 1px solid var(--line); border-radius: 8px;
    background: var(--paper-2); color: var(--muted); font-size: .88rem;
  }}
  .pied {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
           color: var(--muted); font-size: .86rem; }}
</style>
</head>
<body>
<div class="banniere">
  <span class="marque">Minbar Live</span><span class="filet"></span>
  <a href="/">Retour au back-office</a>
  <span style="margin-left:auto;display:flex;gap:14px;align-items:center">{lien_langue}{lien_texte}</span>
</div>
<main>
{avis}
{corps}
<div class="pied">{pied}</div>
</main>
</body>
</html>
"""

_LIEN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_GRAS = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")


def _inline(texte: str) -> str:
    """Gras, italique, code et liens. L'échappement vient d'abord : aucun HTML du document ne passe."""
    sortie = html.escape(texte, quote=False)
    sortie = _CODE.sub(r"<code>\1</code>", sortie)
    sortie = _GRAS.sub(r"<strong>\1</strong>", sortie)
    sortie = _ITAL.sub(r"<em>\1</em>", sortie)

    def lien(m: re.Match[str]) -> str:
        libelle, cible = m.group(1), m.group(2)
        # Un lien relatif ou http(s) seulement : pas de javascript: glissé dans un document.
        if not re.match(r"^(https?://|/|#|mailto:)", cible):
            return libelle
        return f'<a href="{html.escape(cible, quote=True)}">{libelle}</a>'

    return _LIEN.sub(lien, sortie)


def _cellules(ligne: str) -> list[str]:
    return [c.strip() for c in ligne.strip().strip("|").split("|")]


def _est_separateur(ligne: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:-]*-[\s:|-]*\|?", ligne.strip())) and "-" in ligne


def rendre(markdown: str) -> str:
    """Transforme le Markdown en HTML. Couvre ce que nos documents utilisent, rien de plus."""
    lignes = markdown.splitlines()
    blocs: list[str] = []
    i = 0
    while i < len(lignes):
        ligne = lignes[i]
        nue = ligne.strip()

        if not nue:
            i += 1
            continue

        # Filet — inutile juste avant un titre : le titre porte déjà son filet
        if re.fullmatch(r"(-{3,}|\*{3,})", nue):
            suivant = next((l.strip() for l in lignes[i + 1:] if l.strip()), "")
            if not suivant.startswith("#"):
                blocs.append("<hr>")
            i += 1
            continue

        # Titres
        m = re.match(r"^(#{1,6})\s+(.*)$", nue)
        if m:
            niveau = len(m.group(1))
            blocs.append(f"<h{niveau}>{_inline(m.group(2).strip())}</h{niveau}>")
            i += 1
            continue

        # Tableau : une ligne d'en-tête suivie d'une ligne de tirets
        if nue.startswith("|") and i + 1 < len(lignes) and _est_separateur(lignes[i + 1]):
            entetes = _cellules(nue)
            i += 2
            corps: list[list[str]] = []
            while i < len(lignes) and lignes[i].strip().startswith("|"):
                corps.append(_cellules(lignes[i]))
                i += 1
            t = ["<table>", "<thead><tr>"]
            t += [f"<th>{_inline(c)}</th>" for c in entetes]
            t.append("</tr></thead><tbody>")
            for rang in corps:
                t.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in rang) + "</tr>")
            t.append("</tbody></table>")
            blocs.append("".join(t))
            continue

        # Citation
        if nue.startswith(">"):
            morceaux: list[str] = []
            while i < len(lignes) and lignes[i].strip().startswith(">"):
                morceaux.append(lignes[i].strip()[1:].strip())
                i += 1
            blocs.append(f"<blockquote><p>{_inline(' '.join(morceaux))}</p></blockquote>")
            continue

        # Listes : on garde l'imbrication sur deux niveaux, sans plus
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", ligne)
        if m:
            ordonnee = m.group(2)[0].isdigit()
            balise = "ol" if ordonnee else "ul"
            items: list[str] = []
            while i < len(lignes):
                mm = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", lignes[i])
                if not mm:
                    # ligne de continuation d'un item
                    if lignes[i].strip() and items and lignes[i].startswith("  "):
                        items[-1] += " " + _inline(lignes[i].strip())
                        i += 1
                        continue
                    break
                imbrique = len(mm.group(1)) >= 2
                texte = _inline(mm.group(3).strip())
                if imbrique and items:
                    if not items[-1].endswith("</ul>"):
                        items[-1] += f"<ul><li>{texte}</li></ul>"
                    else:
                        items[-1] = items[-1].replace("</ul>", f"<li>{texte}</li></ul>")
                else:
                    items.append(f"<li>{texte}</li>")
                i += 1
            blocs.append(f"<{balise}>" + "".join(items) + f"</{balise}>")
            continue

        # Paragraphe : on avale les lignes jusqu'au prochain bloc
        morceaux = []
        while i < len(lignes):
            n = lignes[i].strip()
            if not n or re.match(r"^(#{1,6}\s|>|\||(\s*)([-*+]|\d+\.)\s)", lignes[i]) or re.fullmatch(r"(-{3,})", n):
                break
            morceaux.append(n)
            i += 1
        blocs.append(f"<p>{_inline(' '.join(morceaux))}</p>")

    return "\n".join(blocs)


def page(markdown: str, *, titre: str, source: str | None, lang: str = "fr",
         alterne: tuple[str, str] | None = None, avis: str | None = None) -> str:
    """Page complète, prête à servir en ``text/html``.

    ``source`` est l'adresse de la version texte du même document ; ``None`` quand il n'en existe
    pas (page d'index) — le lien disparaît alors, plutôt que de pointer vers lui-même.
    ``alterne`` est (adresse, libellé) de la même page dans l'autre langue. ``avis`` est une phrase
    affichée au-dessus du texte, pour dire par exemple qu'une traduction n'existe pas encore.
    """
    nom = titre
    for ligne in markdown.splitlines():
        if ligne.startswith("# "):
            nom = ligne[2:].strip()
            break
    pied = "Document servi par Minbar Live. Il se met à jour avec l'application."
    lien = (f'<a href="{html.escape(source, quote=True)}">Version texte</a>' if source else "")
    lien_langue = (f'<a href="{html.escape(alterne[0], quote=True)}">{html.escape(alterne[1])}</a>'
                   if alterne else "")
    bandeau_avis = f'<p class="avis">{html.escape(avis)}</p>' if avis else ""
    return _TEMPLATE.format(lang=lang, title=html.escape(nom), corps=rendre(markdown),
                            lien_texte=lien, lien_langue=lien_langue, avis=bandeau_avis, pied=pied)
