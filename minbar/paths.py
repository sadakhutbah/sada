"""Emplacements par défaut du dépôt (mode A : tout tourne sur l'ordinateur de la mosquée)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
DATA = ROOT / "data"
STATE = DATA / "state"            # un dossier par run de l'orchestrateur (sessions.json, journaux)
RECORDINGS = DATA / "recordings"
OUTPUT = DATA / "output"          # sorties du pipeline : <run>/session<n>/
CACHE = DATA / "cache"
PUBLISHED = DATA / "published"
BACKOFFICE_DB = DATA / "backoffice" / "minbar.db"
