"""Helpers and fixtures shared by the back-office tests (defined in test_backoffice)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backoffice import client, csrf, env, login, make_run  # noqa: E402,F401
