"""SQLite database of the back-office.

The schema is described by SQLAlchemy 2.0 models (`models.py`, metadata + Core) and applied by
versioned migrations (`migrations.py`). Every query goes through SQLAlchemy (`orm.py`): SQLite file
by default, PostgreSQL when `SADA_DATABASE_URL` is set.

Every business table carries `mosque_id`: that is the partitioning key (tenant). Queries from the
application ALWAYS filter by the mosque of the logged-in user.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa

from .. import defaults, paths
from ..i18n import _
from . import migrations, models

DEFAULT_DB = paths.BACKOFFICE_DB
# settings a mosque can change (Settings → Mosque, `python -m sada.backoffice mosque`)
MOSQUE_SETTINGS = (
    "name",
    "timezone",
    "mawaqit_url",
    "orchestrator_url",
    "orchestrator_token",
    "state_dir",
    "output_dir",
    "publish_dir",
    "clip_title",
    "logo_path",
    "brand_green",
    "brand_gold",
)
MASK = "••••••"  # value shown in place of a stored secret; sent back as is = unchanged

# schéma SQLAlchemy (source de vérité) et migrations versionnées, réexportés pour introspection
metadata = models.metadata
MIGRATIONS = migrations.MIGRATIONS
LATEST_VERSION = migrations.LATEST


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def connect(path: str | Path):
    """ORM session (`orm.Db`) on the SQLite file `path`, or on PostgreSQL when `SADA_DATABASE_URL`
    is set (one schema per path)."""
    from . import orm

    return orm.Db(orm.engine(path, DEFAULT_DB))


def init(path: str | Path) -> None:
    """Crée ou met à jour la base `path` : schéma des modèles SQLAlchemy + migrations versionnées."""
    migrations.init(path)


def audit(
    conn, mosque_id, user_id, action: str, object_type: str = "", object_id=None, ip: str | None = None, **details
):
    from . import orm

    conn.execute(
        sa.insert(models.Audit).values(
            mosque_id=mosque_id,
            user_id=user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            details=json.dumps(details, ensure_ascii=False),
            ip=ip,
            created_at=now_iso(),
        )
    )
    orm.done(conn)


# ------------------------------------------------------------------ mosques


def mosque_dirs(m: dict) -> dict:
    """Effective folders of a mosque (empty values = folders of the repository, mode A)."""
    return {
        "state": Path(m.get("state_dir") or paths.STATE),
        "output": Path(m.get("output_dir") or paths.OUTPUT),
        "publish": Path(m.get("publish_dir") or local_folder_default(m)),
    }


def local_folder_default(m: dict) -> Path:
    """Default folder of the "Local folder" connection: data/published/<mosque>."""
    return paths.PUBLISHED / m["slug"]


DEFAULT_TZ = "Europe/Paris"


def timezone(mosque: dict) -> dt.tzinfo:
    """Mosque time zone; on Windows the tz database comes from the tzdata package.

    A bare Windows install has no zoneinfo data at all: rather than crash every preview with
    ZoneInfoNotFoundError, fall back to the fixed default offset (the tzdata dependency is declared,
    so this only covers a machine where it is missing)."""
    key = mosque.get("timezone") or DEFAULT_TZ
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        if key != DEFAULT_TZ:
            try:
                return ZoneInfo(DEFAULT_TZ)
            except ZoneInfoNotFoundError:
                pass
        return dt.timezone(dt.timedelta(hours=1))  # Europe/Paris en hiver


def update_mosque(conn, mosque_id: int, **vals) -> None:
    if "timezone" in vals:
        # Validate strictly here: timezone() is deliberately lenient at runtime (it falls back so a
        # machine without tzdata can still preview), so calling it would accept any garbage typed by
        # the mosque and only break months later in the screen previews.
        try:
            ZoneInfo(vals["timezone"] or DEFAULT_TZ)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError(_("unknown time zone: {zone} (e.g. Europe/Paris)", zone=vals["timezone"])) from None
    if vals:
        from . import orm

        conn.execute(sa.update(models.Mosque).where(models.Mosque.id == mosque_id).values(**vals))
        orm.done(conn)


def create_mosque(conn, slug: str, name: str, **fields) -> int:
    fields.setdefault("clip_title", defaults.CLIP_TITLE)
    m = models.Mosque(slug=slug, name=name, created_at=now_iso(), **fields)
    conn.add(m)
    conn.flush()
    mid = m.id
    conn.add(models.Connector(mosque_id=mid, key="local", enabled=1, settings="{}"))
    audit(conn, mid, None, "mosquee.creee", "mosquee", mid, slug=slug)
    return mid
