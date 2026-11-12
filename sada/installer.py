"""Mise en route de l'application : un seul geste, et un état qu'on peut vérifier.

Deux publics, deux entrées :

- ``python -m sada.installer`` — le geste ordinaire. Il regarde ce qui manque, fait ce qui manque,
  et s'arrête net au premier blocage en disant quoi faire. Rien à connaître, rien à retenir.
- ``python -m sada.installer --verifier`` — le même examen, sans rien changer : pour savoir
  où en est une machine, ou pour diagnostiquer après coup.

La règle tenue ici : **un pas n'est annoncé fait qu'après avoir été vérifié**. Un téléchargement
qui échoue n'est pas « presque installé », et un exécutable présent mais qui refuse de démarrer
n'est pas installé du tout. C'est exactement ce qui distingue « ça a l'air bon » de « c'est bon ».

Ce module ne touche ni la base, ni les comptes, ni les réglages : il prépare la machine, pas la
mosquée. Les étapes qui laissent une trace (navigateur dans ``vendor/``) sont réversibles en
supprimant le dossier, et il le dit avant de le faire.
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .i18n import _

# Versions minimales des briques sans lesquelles le rendu des écrans ne peut pas fonctionner.
MIN_PYTHON = (3, 10)


def _console_lisible() -> None:
    """Rend la console Windows capable d'afficher le bilan tel qu'il est écrit.

    Une console cp1252 refuse « é » ou « → » et lève au milieu du bilan. On demande d'abord à
    Windows de passer en UTF-8 (`chcp 65001`), puis on autorise le remplacement : le bilan garde
    ses accents là où c'est possible, et un caractère exotique ne peut plus interrompre une
    installation.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)     # type: ignore[attr-defined]
        except Exception:                          # noqa: BLE001 — purement cosmétique
            pass
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_console_lisible()

REQUIRED_MODULES = (
    ("websockets", "websockets"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("PIL", "pillow"),
    ("sqlalchemy", "sqlalchemy"),
    ("argon2", "argon2-cffi"),
)

OK = "ok"
MANQUE = "manque"
BLOQUE = "bloque"


@dataclass
class Etape:
    """Un point de l'examen : ce qu'on regarde, ce qu'on trouve, et quoi faire s'il manque."""

    nom: str
    etat: str = OK
    detail: str = ""
    remede: str = ""
    # un pas automatique sait se refaire seul ; les autres demandent une main humaine
    automatique: bool = False
    _action: Callable[[], bool] | None = field(default=None, repr=False)


def _python_ok() -> Etape:
    version = sys.version_info
    lisible = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < MIN_PYTHON:
        return Etape("Python", BLOQUE,
                     _("{version} — {min_major}.{min_minor} minimum", version=lisible,
                       min_major=MIN_PYTHON[0], min_minor=MIN_PYTHON[1]),
                     _("install a recent Python (python.org, “Add to PATH” box)"))
    return Etape("Python", OK, f"{lisible} ({platform.system()})")


def _modules_ok() -> Etape:
    manquants = []
    for module, paquet in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except Exception:                          # noqa: BLE001 — un module cassé compte comme absent
            manquants.append(paquet)
    if manquants:
        return Etape(_("Python dependencies"), MANQUE, ", ".join(manquants),
                     f"{_pip()} install --upgrade {_paquet()}",
                     automatique=True, _action=lambda: _installer_dependances(manquants))
    return Etape(_("Python dependencies"), OK,
                 _("{count} modules present", count=len(REQUIRED_MODULES)))


def _moteur_ok() -> Etape:
    from .backoffice import template_render as tr

    fourni = tr.embedded_path()
    if fourni is not None:
        return Etape(_("Rendering browser"), OK, _taille(fourni.parent.parent.parent))
    try:
        pris = tr.browser_path()
    except tr.RenderUnavailable:
        pris = None
    if pris is not None:
        return Etape(_("Rendering browser"), MANQUE,
                     _("no bundled browser; the machine's own will be used ({path})", path=pris),
                     _("python -m sada.installer  (downloads the bundled browser, ≈ 280 MB)"),
                     automatique=True, _action=lambda: _telecharger_moteur())
    return Etape(_("Rendering browser"), MANQUE, _("no browser, neither bundled nor on the machine"),
                 _("python -m sada.installer  (downloads the bundled browser, ≈ 280 MB)"),
                 automatique=True, _action=lambda: _telecharger_moteur())


RAIL_BIN = Path(__file__).resolve().parent.parent / "tools" / "bin"


def _relais_ok() -> Etape:
    """Le relais vidéo (mediamtx) : indispensable au direct, inutile au rendu d'un écran."""
    # Un binaire d'une autre plateforme ne sert à rien : sur Windows, c'est mediamtx.exe qui
    # compte, pas le fichier Linux qu'un dépôt partagé peut laisser traîner à côté.
    nom = "mediamtx.exe" if sys.platform.startswith("win") else "mediamtx"
    binaire = RAIL_BIN / nom
    if binaire.is_file():
        return Etape(_("Video relay"), OK, str(binaire.relative_to(RAIL_BIN.parent.parent)))
    return Etape(_("Video relay"), MANQUE, _("no binary for this system"),
                 _("live machine: put {name} in tools/bin (see tools/mediamtx.sh)", name=nom))



def _ffmpeg_ok() -> Etape:
    nom = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    dans_le_projet = RAIL_BIN / nom
    if dans_le_projet.is_file():
        return Etape("ffmpeg", OK, str(dans_le_projet.relative_to(RAIL_BIN.parent.parent)))
    found = shutil.which("ffmpeg")
    if found:
        return Etape("ffmpeg", OK, found)
    return Etape("ffmpeg", MANQUE, _("not found, neither in tools/bin nor in the PATH"),
             _("live machine: drop ffmpeg into tools/bin, or install it"))


def examen() -> list[Etape]:
    """L'état de la machine, sans rien modifier."""
    etapes = [_python_ok(), _modules_ok()]
    if etapes[1].etat == OK:
        etapes.append(_moteur_ok())
        etapes.append(_relais_ok())
        etapes.append(_ffmpeg_ok())
    else:
        etapes.append(Etape(_("Rendering browser"), BLOQUE, _("checked after the dependencies")))
        etapes.append(Etape(_("Video relay"), BLOQUE, _("checked after the dependencies")))
        etapes.append(Etape("ffmpeg", BLOQUE, _("checked after the dependencies")))
    return etapes


# --------------------------------------------------------------------------- actions élémentaires
def _pip() -> str:
    return f'"{sys.executable}" -m pip'


def _paquet() -> str:
    """Le paquet à réinstaller : celui du dépôt si on y est, sinon le nom publié."""
    racine = Path(__file__).resolve().parent.parent
    return f'"{racine}"' if (racine / "pyproject.toml").is_file() else "sada"


def _installer_dependances(paquets: list[str]) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *paquets]
    return subprocess.call(cmd) == 0


def _telecharger_moteur() -> bool:
    from .backoffice.template_render import RenderUnavailable, fetch_engine

    try:
        fetch_engine()
    except (RenderUnavailable, OSError) as exc:
        print("  " + _("download impossible: {error}", error=exc))
        return False
    return True


def _taille(dossier: Path) -> str:
    """Taille d'un dossier en mégaoctets, telle qu'elle sera vécue sur le disque."""
    octets = 0
    try:
        for root, _dirs, files in os.walk(dossier):
            for name in files:
                try:
                    octets += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        return _("unknown size")
    return _("{size:.0f} MB", size=octets / (1024 * 1024))


# ----------------------------------------------------------------------------------- présentation
# state marks shown in the summary (values stay keys, translated at display time)
MARQUES = {OK: "ok", MANQUE: "to do", BLOQUE: "blocked"}


def afficher(etapes: list[Etape], entete: str | None = None) -> None:
    entete = entete or _("Sadā setup")
    print(f"\n{entete}")
    print("-" * len(entete))
    largeur = max(len(e.nom) for e in etapes)
    for e in etapes:
        print(f"  {e.nom.ljust(largeur)}  {_(MARQUES[e.etat]):<8}  {e.detail}")
        if e.etat != OK and e.remede:
            print(f"  {'':<{largeur}}  {'':<8}  → {e.remede}")
    print()


# ------------------------------------------------------------------------------------------ main
def _prefixe() -> str:
    """Invocation à recopier : le module du back-office, et non le chemin d'un fichier."""
    return "python -m sada.backoffice"


def install(argv: list[str] | None = None) -> int:
    """Fait ce qui peut l'être, s'arrête au premier blocage. Le code de sortie dit la vérité."""
    argv = sys.argv[1:] if argv is None else argv
    verifier = "--verifier" in argv
    non_interactif = "--oui" in argv or "--verifier" in argv

    entete = _("Sadā — setup")
    print(f"\n{entete}")
    print("-" * len(entete))

    etapes = examen()
    if verifier:
        afficher(etapes, _("State of the machine (nothing modified)"))
        return 0 if all(e.etat == OK for e in etapes) else 1

    # Ce que le poste ne peut pas avoir (relais vidéo, ffmpeg : affaire du poste de direct) est
    # signalé sans arrêter la mise en route ; ce qui empêcherait l'application de tourner arrête.
    # L'examen est refait à chaque tour : un pas réparé peut en débloquer un autre (les dépendances
    # d'abord, le navigateur ensuite), et c'est le fait constaté qui décide, pas le plan d'origine.
    while True:
        etapes = examen()
        prochaine = next((e for e in etapes if e.etat == MANQUE and e.automatique), None)
        if prochaine is None:
            break
        print()
        print("  " + _("{name}: to do ({detail})", name=prochaine.nom, detail=prochaine.detail))
        action = prochaine._action
        if action is None:
            print("  " + _("{name}: to do — {remedy}", name=prochaine.nom, remedy=prochaine.remede))
            break
        if not non_interactif and not _confirmer("  " + _("Run it now? [{answer}]", answer=_oui_non(True)) + " "):
            print("  " + _("{name}: left aside — {remedy}", name=prochaine.nom, remedy=prochaine.remede))
            break
        print("  " + _("{name}: running…", name=prochaine.nom))
        if not action():
            print("  " + _("{name}: failed — {remedy}", name=prochaine.nom, remedy=prochaine.remede))
            return 3
        print("  " + _("{name}: done", name=prochaine.nom))

    for etape in examen():
        if etape.etat == BLOQUE:
            print("  " + _("{name}: blocked — {remedy}", name=etape.nom, remedy=etape.remede or etape.detail))
            return 2
        if etape.etat == OK:
            print("  " + _("{name}: in place ({detail})", name=etape.nom, detail=etape.detail))

    print()
    print("  " + _("Final check"))
    restantes = examen()
    afficher(restantes, _("State after installation"))
    bloquantes = [e for e in restantes if e.etat != OK and e.automatique]
    if not bloquantes and all(e.etat != BLOQUE for e in restantes):
        print("  " + _("The application is ready. Next: create an administrator account, then "
                        "start the server."))
        print(f"    {_prefixe()} create-admin --username admin")
        print(f"    {_prefixe()} serve")
        absents = [e.nom for e in restantes if e.etat != OK]
        if absents:
            print("  " + _("To plan for a live machine: {items} (nothing to do elsewhere).",
                          items=", ".join(absents)))
        print()
        return 0
    print("  " + _("Some points are still to be handled (see above).") + "\n")
    return 1


def _confirmer(question: str) -> bool:
    if not sys.stdin.isatty():
        return False                               # jamais d'accord supposé à la place d'une personne
    try:
        reponse = input(question).strip().lower()
    except EOFError:
        return False
    return reponse in ("", "o", "oui", "y", "yes")


def _oui_non(defaut: bool) -> str:
    return _("Y/n") if defaut else _("y/N")


if __name__ == "__main__":
    sys.exit(install(sys.argv[1:]))

