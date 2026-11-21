"""Seeds the preprod mosque (run inside the back office container by `preprod.sh seed`). Idempotent.

Local test values only. Prints one JSON line with what was issued (agent token, stream key: shown once).
"""

import json
import os
import sys

from sqlalchemy import select, update

from sada.backoffice import auth, db, destinations, instance, internal, live_input, orm
from sada.backoffice.models import Mosque, User

SLUG = "preprod"
PASSWORD = os.environ["PREPROD_PASSWORD"]
ACCOUNTS = (
    ("preprod-admin", "admin", "Admin Préprod", "admin@preprod.test"),
    ("preprod-responsable", "responsable", "Responsable Préprod", "responsable@preprod.test"),
)
DESTINATION = ("YouTube (faux serveur local)", "rtmp://fake-destination:1935/live/youtube-preprod")

issue_agent, issue_key = "--issue-agent" in sys.argv, "--issue-key" in sys.argv
path = os.environ.get("SADA_DB", str(db.DEFAULT_DB))
db.init(path)
conn = db.connect(path)
out: dict = {}

mosque = orm.first(conn, Mosque, slug=SLUG)
if mosque is None:
    mid = db.create_mosque(conn, SLUG, "Mosquée Préprod")
    mosque = orm.first(conn, Mosque, id=mid)
for username, role, name, email in ACCOUNTS:
    exists = conn.scalar(select(User.id).where(User.username == username))
    if exists is None:
        auth.create_user(conn, mosque["id"], username, PASSWORD, role, name)
    conn.execute(update(User).where(User.username == username).values(email=email))
    orm.done(conn)
user = orm.first(conn, User, username="preprod-admin")

# hosted: the machine's folders (a mosque writes under its slug), the same as the orchestrator's SADA_STATE_DIR
instance.save_platform_folders({"state": "/app/data/state", "output": "/app/data/output", "publish": "/app/data/publish"})

# the encoder reads the relay (a computer pushes: Studio, OBS), the replays are named by the live
live_input.save(conn, mosque, "push", "", user)
if not destinations.effective(conn, mosque["id"]):
    destinations.save(conn, mosque, [{"label": DESTINATION[0], "url": DESTINATION[1], "enabled": True}], user)
if issue_agent:
    out["agent_token"] = internal.new_agent_token(conn, mosque["id"])
if issue_key:
    out["publish_key"] = internal.new_publish_key(conn, mosque["id"])
print("SEED " + json.dumps(out))
