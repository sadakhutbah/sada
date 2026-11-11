"""Textes communs du gabarit : lignes arabes saisies, logo et nom de la mosquée.

Le gabarit HTML peut afficher trois choses que les textes d'écran ne portent pas : le logo, le nom
de la mosquée et une ligne en arabe. Les lignes arabes sont saisies par la mosquée et stockées dans
la table `screen_texts`, sous l'écran réservé ``_commun`` (hors des six écrans du moteur).

Règle absolue : aucun texte arabe par défaut. Un champ vide reste vide, et le gabarit n'affiche
alors aucune ligne : rien n'est inventé à la place de la mosquée.
"""
from __future__ import annotations

import re

from . import branding, db

# Écran réservé : il ne fait partie ni de `screens.SCREENS` ni du rendu Pillow.
SCREEN = "_commun"

# Une ligne arabe par contexte du gabarit.
SLOTS = ("arabe_khutba", "arabe_priere", "arabe_janaza", "arabe_dars")

MAX_LEN = 160              # même borne qu'une ligne d'écran sur 1280 px


class InvalidVariables(ValueError):
    """Payload refusé : champ inconnu, texte trop long ou balise HTML."""


def _saved(conn, mosque_id: int) -> dict[str, str]:
    rows = db.all_(conn, "SELECT slot, text FROM screen_texts WHERE mosque_id = ? AND screen = ?",
                   mosque_id, SCREEN)
    return {r["slot"]: r["text"] for r in rows if r["slot"] in SLOTS}


def lire(conn, mosque_id: int) -> dict[str, str]:
    """Les quatre lignes arabes enregistrées ; un champ jamais saisi vaut une chaîne vide."""
    saved = _saved(conn, mosque_id)
    return {slot: saved.get(slot, "") for slot in SLOTS}


def check(payload) -> dict[str, str]:
    """Valide un payload de l'interface : champs connus, 160 caractères, pas de balise HTML."""
    if not isinstance(payload, dict):
        raise InvalidVariables("un objet {champ: texte} est attendu")
    out: dict[str, str] = {}
    for slot, valeur in payload.items():
        if slot not in SLOTS:
            raise InvalidVariables(f"champ inconnu : {slot}")
        texte = "" if valeur is None else str(valeur).strip().replace("\n", " ")
        if len(texte) > MAX_LEN:
            raise InvalidVariables(f"{slot} : {MAX_LEN} caractères au maximum")
        if "<" in texte or ">" in texte:
            raise InvalidVariables(f"{slot} : les balises HTML ne sont pas acceptées")
        out[slot] = texte
    return out


def enregistrer(conn, mosque_id: int, valeurs, user_id=None, ip: str | None = None) -> dict[str, str]:
    """Enregistre les lignes fournies ; un champ vide efface la ligne.

    Les champs absents du payload gardent leur valeur : un formulaire partiel ne vide pas les
    autres lignes.
    """
    clean = check(valeurs or {})
    for slot, texte in clean.items():
        conn.execute("DELETE FROM screen_texts WHERE mosque_id = ? AND screen = ? AND slot = ?",
                     (mosque_id, SCREEN, slot))
        if texte:
            conn.execute("INSERT INTO screen_texts (mosque_id, screen, slot, text, updated_at) "
                         "VALUES (?,?,?,?,?)", (mosque_id, SCREEN, slot, texte, db.now_iso()))
    db.audit(conn, mosque_id, user_id, "ecrans.variables", "mosque", mosque_id, ip=ip,
             champs=[slot for slot, texte in clean.items() if texte])
    conn.commit()
    return lire(conn, mosque_id)


def variables(conn, mosque: dict) -> dict[str, str]:
    """Valeurs communes remises au gabarit, à l'antenne comme dans l'aperçu.

    Le logo vaut une chaîne vide quand la mosquée n'en a pas : le gabarit masque alors l'image au
    lieu de recevoir un emblème qui ne lui appartient pas. Les lignes arabes sont exactement celles
    saisies ; aucune n'est fournie par le code.
    """
    return {"logo": branding.url(mosque) if (mosque.get("logo_path") or "").strip() else "",
            "nom": mosque.get("name") or "",
            **lire(conn, mosque["id"])}
