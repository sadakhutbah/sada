"""Le mode d'emploi doit arriver à l'administrateur sous forme de page, pas de texte brut."""

from __future__ import annotations

import re
from pathlib import Path

from sada.backoffice import docpage

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_une_page_sans_version_texte_n_affiche_pas_le_lien():
    html = docpage.page("# Titre", titre="x", source=None)
    assert "Version texte" not in html


def test_le_modele_du_back_office_rend_une_page_complete():
    html = docpage.page("# Titre\n\nUn paragraphe.", titre="x", source="/docs/x.md")
    assert html.startswith("<!doctype html>")
    assert "<h1>Titre</h1>" in html
    assert "<p>Un paragraphe.</p>" in html
    assert "<title>Titre</title>" in html


def test_le_vrai_document_ne_laisse_aucune_syntaxe_brute():
    md = (DOCS / "fr" / "ecrans-mode-emploi.md").read_text(encoding="utf-8")
    html = docpage.rendre(md)
    assert "<h1>" in html
    assert html.count("<h2>") >= 8
    # rien de la syntaxe Markdown ne doit survivre à l'écran
    assert not re.search(r"^#{1,6} ", html, re.M)
    assert "**" not in html
    assert not re.search(r"^\|", html, re.M)


def test_les_six_ecrans_et_les_variables_sont_expliques():
    md = (DOCS / "fr" / "ecrans-mode-emploi.md").read_text(encoding="utf-8")
    for mot in (
        "Avant",
        "Prière",
        "Entracte",
        "Terminé",
        "Janaza",
        "Fin de cours",
        "{heure}",
        "{date}",
        "{mosquee}",
        "{cours}",
    ):
        assert mot in md, f"« {mot} » manque au mode d'emploi"


def test_le_tableau_des_six_moments_est_rendu_en_tableau():
    md = (DOCS / "fr" / "ecrans-mode-emploi.md").read_text(encoding="utf-8")
    html = docpage.rendre(md)
    assert html.count("<table>") == 1
    assert "<th>Écran</th>" in html


def test_un_lien_javascript_ne_passe_pas():
    html = docpage.rendre("[clique](javascript:alert(1))")
    assert "javascript:" not in html
    assert "clique" in html


def test_le_html_du_document_est_echappe():
    html = docpage.rendre("Un <script>alert('x')</script> dans le texte.")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_un_filet_avant_un_titre_ne_double_pas_le_filet():
    html = docpage.rendre("Texte.\n\n---\n\n## Section")
    assert "<hr>" not in html


def test_une_page_anglaise_a_tous_ses_libelles_en_anglais():
    """Le bandeau ne doit pas rester en français au-dessus d'un texte anglais."""
    page = docpage.page("# Title\n\ntext", titre="Title", source="/docs/x.en.md", lang="en")
    assert "Back to the back office" in page and "Plain text" in page
    assert 'lang="en"' in page
    assert "Retour au back-office" not in page
    assert "Version texte" not in page


def test_une_page_francaise_garde_ses_libelles():
    page = docpage.page("# Titre", titre="Titre", source="/docs/x.md", lang="fr")
    assert "Retour au back-office" in page and "Version texte" in page


def test_un_bloc_de_code_garde_ses_retours_a_la_ligne_et_est_echappe():
    html = docpage.rendre("```bash\npython -m sada <db>\n```")
    assert "<pre><code>" in html and "```" not in html
    assert "&lt;db&gt;" in html
    assert "<p>" not in html


def test_le_vrai_document_ne_laisse_pas_de_triple_apostrophe():
    for nom in ("fr/orchestrateur.md", "en/orchestrator.md", "fr/agent-gabarit-ecrans.md"):
        html = docpage.rendre((DOCS / nom).read_text(encoding="utf-8"))
        assert "```" not in html, nom
        assert "<pre><code>" in html, nom
