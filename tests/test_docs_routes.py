"""Les pages de documentation doivent répondre, et lister tout ce qu'elles servent."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from minbar.backoffice.app import create_app
from minbar.backoffice.internal import token


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MINBAR_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    app = create_app(db_path=tmp_path / "bo.db")
    with TestClient(app) as c:
        yield c


def test_chaque_document_a_sa_page(client: TestClient):
    for url in ("/docs", "/docs/ecrans-mode-emploi", "/docs/orchestrateur",
                "/docs/agent-gabarit-ecrans"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-type"].startswith("text/html"), url


def test_les_documents_restent_lisibles_en_texte_pour_un_chat(client: TestClient):
    for url in ("/docs/ecrans-mode-emploi.md", "/docs/orchestrateur.md",
                "/docs/agent-gabarit-ecrans.md"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-type"].startswith("text/markdown"), url
        assert len(r.text) > 2000, url


def test_la_page_index_liste_tous_les_documents(client: TestClient):
    html = client.get("/docs").text
    for url in ("/docs/ecrans-mode-emploi", "/docs/orchestrateur", "/docs/agent-gabarit-ecrans"):
        assert f'href="{url}"' in html


def test_aucune_page_de_document_n_affiche_du_markdown_brut(client: TestClient):
    for url in ("/docs/ecrans-mode-emploi", "/docs/orchestrateur", "/docs/agent-gabarit-ecrans"):
        html = client.get(url).text
        assert "<h1>" in html, url
        assert "\n## " not in html, url
        assert "<table>" not in html or "</table>" in html, url


def test_un_document_absent_est_un_404_propre(client: TestClient):
    assert client.get("/docs/inexistant").status_code == 404
    assert client.get("/docs/inexistant.md").status_code == 404


def test_la_documentation_ne_demande_pas_de_compte(client: TestClient):
    """Un administrateur doit pouvoir lire l'aide avant même d'avoir ouvert la moindre session."""
    assert not client.cookies                        # aucune session posée
    r = client.get("/docs/ecrans-mode-emploi", follow_redirects=False)
    assert r.status_code == 200                      # ni redirection vers la connexion, ni 403
    assert token()
