"""Point d'entrée ASGI du back-office, pour la commande officielle `fastapi run` / `fastapi dev`.

L'application est construite à partir de l'environnement (voir `[tool.fastapi]` dans pyproject.toml) :

    MINBAR_DB              base SQLite (défaut : data/backoffice/minbar.db)
    MINBAR_SECURE_COOKIES  « 1 » pour des cookies Secure (derrière HTTPS)
    MINBAR_IMPORT_EVERY    import automatique des runs toutes les N secondes (0 = désactivé)

La ligne de commande maison (`python -m minbar.backoffice serve`) reste disponible pour les
options --host / --port ; elle construit la même application.
"""
from __future__ import annotations

import os

from . import db
from .app import create_app

DB_PATH = os.environ.get("MINBAR_DB") or str(db.DEFAULT_DB)

db.init(DB_PATH)

app = create_app(
    DB_PATH,
    secure_cookies=os.environ.get("MINBAR_SECURE_COOKIES", "").lower() in ("1", "true", "yes"),
    auto_import_s=float(os.environ.get("MINBAR_IMPORT_EVERY", "60") or 60),
)
