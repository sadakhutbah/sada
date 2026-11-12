"""Le module de mise en route : ce qu'il affirme, il l'a vérifié.

Ces tests ne touchent ni au réseau ni au disque hors d'un dossier temporaire : ils éprouvent la
règle du module — un pas n'est « ok » que si le contrôle qui le suit repasse — et le fait qu'un
examen ne modifie jamais rien.
"""

from __future__ import annotations

from collections import namedtuple

import pytest

from sada import installer as inst


def test_un_examen_ne_modifie_rien(tmp_path, monkeypatch):
    """`--verifier` sur une machine incomplète doit sortir en échec sans rien tenter."""
    monkeypatch.setattr(inst, "REQUIRED_MODULES", (("module_qui_n_existe_pas", "fantome"),))
    monkeypatch.setattr(inst, "_telecharger_moteur", lambda: pytest.fail("téléchargement tenté"))
    code = inst.install(["--verifier"])
    assert code == 1


def test_une_machine_complete_sort_en_succes():
    etapes = inst.examen()
    if not all(e.etat == inst.OK for e in etapes):
        pytest.skip("machine incomplète : l'examen propre est couvert par le cas ci-dessus")
    assert inst.install(["--verifier"]) == 0


def test_le_navigateur_absent_donne_la_commande_qui_le_prend(tmp_path, monkeypatch):
    from sada.backoffice import template_render as tr

    monkeypatch.setattr(tr, "EMBEDDED", tmp_path / "vide")
    monkeypatch.setattr(tr, "CHROMIUM_CANDIDATES", ())
    etape = inst._moteur_ok()
    assert etape.etat == inst.MANQUE
    assert etape.automatique is True
    assert "installer" in etape.remede


def test_une_dependance_manquante_est_automatique(monkeypatch):
    monkeypatch.setattr(inst, "REQUIRED_MODULES", (("module_qui_n_existe_pas", "fantome"),))
    etape = inst._modules_ok()
    assert etape.etat == inst.MANQUE
    assert etape.automatique is True
    assert "fantome" in etape.detail and "pip" in etape.remede


def test_les_dependances_presentes_ne_declenchent_rien():
    etape = inst._modules_ok()
    if etape.etat != inst.OK:
        pytest.skip("dépendances incomplètes sur cette machine")
    assert etape._action is None


def test_un_python_trop_ancien_bloque(monkeypatch):
    # on ne remplace que la vue du module : pytest lit aussi sys.version_info pendant le test
    Vieux = namedtuple("version_info", "major minor micro")
    monkeypatch.setattr(inst.sys, "version_info", Vieux(3, 8, 0))
    etape = inst._python_ok()
    assert etape.etat == inst.BLOQUE
    assert "3.10" in etape.detail


def test_le_plan_affiche_toujours_un_remede_pour_ce_qui_manque(capsys):
    etapes = [inst.Etape("Brique", inst.MANQUE, "absent", "faites ceci")]
    inst.afficher(etapes, "Essai")
    sortie = capsys.readouterr().out
    assert "Brique" in sortie and "faites ceci" in sortie


def test_sans_terminal_on_ne_suppose_jamais_un_accord(monkeypatch):
    monkeypatch.setattr(inst.sys.stdin, "isatty", lambda: False)
    assert inst._confirmer("Lancer ? ") is False


def test_un_relais_d_une_autre_plateforme_ne_compte_pas(tmp_path, monkeypatch):
    """Sous Windows, le mediamtx Linux d'un dépôt partagé ne doit pas passer pour installé."""
    (tmp_path / "mediamtx").write_text("biele linux")  # présent, mais mauvais système
    monkeypatch.setattr(inst, "RAIL_BIN", tmp_path)
    monkeypatch.setattr(inst.sys, "platform", "win32")
    assert inst._relais_ok().etat == inst.MANQUE
    (tmp_path / "mediamtx.exe").write_text("windows")
    assert inst._relais_ok().etat == inst.OK


def test_une_dependance_reparee_debloque_la_suite(monkeypatch, capsys):
    """Le module refait son examen après chaque pas : un correctif peut en débloquer un autre."""
    etats = iter([inst.MANQUE, inst.MANQUE, inst.OK, inst.OK, inst.OK, inst.OK, inst.OK])

    def faux_examen():
        return [inst.Etape("Brique", next(etats, inst.OK), "", "", automatique=True, _action=lambda: True)]

    monkeypatch.setattr(inst, "examen", faux_examen)
    assert inst.install(["--oui"]) == 0
    sortie = capsys.readouterr().out
    assert "in place" in sortie and "ready" in sortie
