"""ASGI entry point of the back-office, for the official `fastapi run` / `fastapi dev` command.

The application is built from the environment (see `[tool.fastapi]` in pyproject.toml):

    MINBAR_DB              SQLite database (default: data/backoffice/minbar.db)
    MINBAR_SECURE_COOKIES  "1" for Secure cookies (behind HTTPS)
    MINBAR_IMPORT_EVERY    automatic import of runs every N seconds (0 = disabled)

The in-house command line (`python -m minbar.backoffice serve`) remains available for the
--host / --port options; it builds the same application.
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
