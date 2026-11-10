"""Default repository locations (mode A: everything runs on the mosque's computer)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
DATA = ROOT / "data"
STATE = DATA / "state"            # one folder per orchestrator run (sessions.json, logs)
RECORDINGS = DATA / "recordings"
OUTPUT = DATA / "output"          # pipeline outputs: <run>/session<n>/
CACHE = DATA / "cache"
EVENTS = DATA / "events.json"     # programme of dars and conferences, read by both processes
PUBLISHED = DATA / "published"
BACKOFFICE_DB = DATA / "backoffice" / "minbar.db"
