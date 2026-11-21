"""Fixtures partagées des tests du back-office.

L'application ne parle aux tables que par SQLAlchemy ; les tests aussi. Les helpers de ce module
(`rows`, `one`, `count`, `update`, `audit_rows`, `set_user`…) remplacent les anciennes sondes SQL :
aucune chaîne de SQL n'est écrite dans les tests.

Prébranchement : construire l'environnement d'un test (schéma, mosquée, utilisateurs — le hachage
argon2 des mots de passe pèse l'essentiel —, import d'un run, application) coûtait plus cher que le
test lui-même. Il est donc construit une seule fois par session (`env_base`) ; chaque test en reçoit
une copie isolée (`env`) : base neuve remplie depuis la référence (identifiants réattribués, clés
étrangères suivies, chemins réécrits), fichiers du run recopiés dans son propre dossier.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

# never the real embedding model in tests (it may be downloaded on the dev machine): tests fake it
os.environ.setdefault("SADA_SEMANTIC_SEARCH", "0")
# no background studio checks in tests: they call studio_incidents.tick themselves
os.environ.setdefault("SADA_STUDIO_WATCH_S", "0")

from sada.backoffice import auth, db, importer, migrations, models, orm  # noqa: E402
from sada.backoffice.app import create_app  # noqa: E402

PW = "motdepasse-solide"
RUN = "20261106_220224"


def make_run(root: Path, run: str = RUN, janaza: bool = False) -> None:
    """Minimal pipeline run: 1 session (+ 1 optional janaza), 2 clips."""
    out = root / "output" / run / "session1"
    (out / "clips").mkdir(parents=True)
    segs = [
        {"start": 0.0, "end": 4.0, "text": "Chers frères, aujourd'hui nous parlons de la patience."},
        {"start": 4.0, "end": 9.0, "text": "La patience est une lumière."},
        {"start": 9.0, "end": 14.0, "text": "ومن يتوكل على الله فهو حسبه"},
        {"start": 14.0, "end": 20.0, "text": "Qu'Allah nous accorde la patience."},
    ]
    (out / "transcript.json").write_text(json.dumps({"segments": segs, "duration": 20.0, "model": "test"}))
    (out / "verses.json").write_text(
        json.dumps(
            [
                {
                    "surah": 65,
                    "ayah": 3,
                    "surah_name": "At-Talaaq",
                    "score": 98,
                    "start": 9.0,
                    "end": 14.0,
                    "heard": "ومن يتوكل على الله فهو حسبه",
                    "verse_text": "ومن يتوكل على الله فهو حسبه",
                    "ref": "65:3",
                    "confidence": "sûr",
                    "verse_fr": "Et quiconque place sa confiance en Allah, Il lui suffit.",
                }
            ]
        )
    )
    (out / "translations.json").write_text(
        json.dumps({"seg": {}, "verse_fr": {"65:3@9.0": "Et quiconque place sa confiance en Allah, Il lui suffit."}})
    )
    (out / "notes.md").write_text("# Notes\n\n## Thème (résumé généré)\n\nLa patience\n")
    man = []
    for i, (a, b, kind) in enumerate([(0.0, 10.0, "verset"), (12.0, 20.0, "conclusion")], 1):
        f = f"clip{i}_{kind}_A-VALIDER.mp4"
        (out / "clips" / f).write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))
        man.append(
            {
                "idx": i,
                "kind": kind,
                "start": a,
                "end": b,
                "why": "test",
                "file": f,
                "video_source": str(root / "rec.mkv"),
                "source_offset": 100.0,
                "crop": None,
            }
        )
    (out / "clips" / "manifest.json").write_text(json.dumps(man))
    sessions = [
        {
            "index": 0,
            "planned": "13:30",
            "status": "done",
            "start_clock": "13:31",
            "end_clock": "13:52",
            "start_pos": 100.0,
            "end_pos": 120.0,
        }
    ]
    if janaza:
        j = root / "output" / run / "session2"
        j.mkdir()
        (j / "transcript.json").write_text(json.dumps({"segments": [], "duration": 60}))
        sessions.append({"index": 1, "planned": "janaza", "status": "janaza"})
    st = root / "state" / run
    st.mkdir(parents=True)
    (st / "sessions.json").write_text(json.dumps({"sessions": sessions, "recordings": []}))


# ------------------------------------------------------------------ helpers ORM


def rows(conn, model, *where, order_by=None, **by) -> list[dict]:
    """Lignes de `model` en dicts : `where` (expressions SQLAlchemy) puis `by` (colonne = valeur)."""
    stmt = sa.select(model).where(*where).filter_by(**by)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    return orm.rows(conn, stmt)


def one(conn, model, *where, **by) -> dict | None:
    """Première ligne de `model` en dict, ou None."""
    return orm.first(conn, model, *where, **by)


def count(conn, model, *where, **by) -> int:
    stmt = sa.select(sa.func.count()).select_from(model).where(*where).filter_by(**by)
    return conn.scalar(stmt)


def update(conn, model, values: dict, *where, **by) -> None:
    """Écrit `values` sur les lignes de `model` désignées par `where` / `by`, et valide."""
    conn.execute(sa.update(model).where(*where).filter_by(**by).values(**values))
    orm.done(conn)


def delete(conn, model, *where, **by) -> None:
    """Supprime les lignes de `model` désignées par `where` / `by`, et valide."""
    conn.execute(sa.delete(model).where(*where).filter_by(**by))
    orm.done(conn)


def insert(conn, model, **values) -> int | None:
    """Insère une ligne de `model` et rend son identifiant."""
    res = conn.execute(sa.insert(model).values(**values))
    orm.done(conn)
    return res.inserted_primary_key[0]


def audit_rows(conn, action: str | None = None, like: str | None = None, **by) -> list[dict]:
    """Entrées d'audit dans l'ordre d'écriture (filtre exact `action`, ou `like`)."""
    where = []
    if action is not None:
        where.append(models.audit.c.action == action)
    if like is not None:
        where.append(models.audit.c.action.like(like))
    return rows(conn, models.Audit, *where, order_by=models.audit.c.id, **by)


def set_user(conn, username: str | None = None, **values) -> None:
    """Modifie un compte, désigné par son nom d'utilisateur (ou par `id=`)."""
    uid = values.pop("id", None)
    if uid is None:
        update(conn, models.User, values, username=username)
    else:
        update(conn, models.User, values, id=uid)


def set_mosque(conn, mosque_id: int, **values) -> None:
    update(conn, models.Mosque, values, id=mosque_id)


def set_khutba(conn, khutba_id: int, **values) -> None:
    update(conn, models.Khutba, values, id=khutba_id)


def set_clip(conn, clip_id: int, **values) -> None:
    update(conn, models.Clip, values, id=clip_id)


def clip_ids(env) -> list[int]:
    return [c["id"] for c in rows(env["conn"], models.Clip, order_by=models.clips.c.idx)]


# ------------------------------------------------------------------ schéma (migrations)


def columns(conn, table: str) -> set[str]:
    """Colonnes d'une table, lues par l'inspecteur SQLAlchemy (`PRAGMA table_info` auparavant)."""
    conn.commit()
    return {c["name"] for c in sa.inspect(conn.connection()).get_columns(table)}


def table_names(conn) -> set[str]:
    conn.commit()
    return set(sa.inspect(conn.connection()).get_table_names())


def index_names(conn) -> set[str]:
    conn.commit()
    insp = sa.inspect(conn.connection())
    return {i["name"] for t in insp.get_table_names() for i in insp.get_indexes(t)}


def applied_versions(conn) -> list[int]:
    """Versions enregistrées dans `schema_migrations`, dans l'ordre."""
    return list(conn.scalars(sa.select(migrations.schema_migrations.c.version).order_by("version")))


def create_schema(path, md: sa.MetaData) -> None:
    """Crée les tables de `md` dans une base neuve — celle d'avant les migrations."""
    md.create_all(orm.engine(path, db.DEFAULT_DB))


def legacy_metadata(without: dict[str, set[str]] | None = None, skip: tuple[str, ...] = ()) -> sa.MetaData:
    """Schéma d'avant migration : les tables des modèles, privées des colonnes (`without`) et des
    tables (`skip`) qu'une migration a ajoutées après coup.

    Une base construite ainsi est celle que l'ancien code laissait derrière lui : `db.init` doit
    l'adopter, puis rattraper ce qui lui manque sans perdre les données.
    """
    md = sa.MetaData()
    for table in models.TABLES:
        if table.name in skip:
            continue
        cols = [
            sa.Column(
                c.name,
                copy.copy(c.type),
                primary_key=c.primary_key,
                nullable=not c.primary_key,
                server_default=None if c.server_default is None else c.server_default.arg,
            )
            for c in table.columns
            if c.name not in (without or {}).get(table.name, ())
        ]
        sa.Table(table.name, md, *cols)
    return md


# ------------------------------------------------------------------ environnement


class Client(TestClient):
    """Test client that keeps its session's anti-replay token, as the front end does."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.csrf = ""
        self.login_cookie = ""
        self.last_login = None

    def ecrire(self, chemin: str, corps: dict | None = None, methode: str = "POST"):
        """JSON write on the API, with the session token in a header."""
        return self.request(methode, f"/api/v1{chemin}", json=corps or {}, headers={"X-CSRF": self.csrf})


def client(env, username=None, headers=None) -> Client:
    c = Client(env["app"], follow_redirects=False, headers=headers or {})
    if username:
        login(c, username)
    return c


def login(c: Client, username: str, password: str = PW, expect_ok: bool | None = None):
    pre = c.get("/api/v1/login-challenge").json()["pre"]
    r = c.post("/api/v1/login", data={"username": username, "password": password, "pre": pre})
    if r.status_code == 200:
        c.csrf = r.json()["csrf"]
    c.login_cookie = r.headers.get("set-cookie", "")
    c.last_login = r
    if expect_ok if expect_ok is not None else password == PW:
        assert r.status_code == 200, f"connexion refusée : {r.status_code} {r.text[:300]}"
    return r


def csrf(c: Client, url: str = "/api/v1/session") -> str:
    return c.csrf


@pytest.fixture(scope="session")
def env_base(tmp_path_factory):
    """Environnement de référence, construit une fois pour toute la session."""
    root = tmp_path_factory.mktemp("base")
    make_run(root, janaza=True)
    path = root / "bo.db"
    db.init(path)
    conn = db.connect(path)
    mid = db.create_mosque(
        conn,
        "ma-mosquee",
        "Mosquée Exemple",
        state_dir=str(root / "state"),
        output_dir=str(root / "output"),
        publish_dir=str(root / "pub"),
        orchestrator_url="http://127.0.0.1:9",
    )
    ids = {r: auth.create_user(conn, mid, r, PW, r, r.capitalize()) for r in auth.ROLES}
    ids["imam2"] = auth.create_user(conn, mid, "imam2", PW, "imam", "Autre imam")
    update(conn, models.User, {"platform_admin": 1}, id=ids["admin"])
    importer.import_run(conn, one(conn, models.Mosque, id=mid), RUN, extract_video=False, log=lambda *a: None)
    kid = one(conn, models.Khutba)["id"]
    update(conn, models.Khutba, {"imam_id": ids["imam"]}, id=kid)
    yield {"root": root, "path": path, "conn": conn, "mid": mid, "ids": ids, "kid": kid}
    conn.close()


def _clone(src, dst, old_root: Path, new_root: Path) -> dict[str, dict[int, int]]:
    """Recopie la base de référence dans une base neuve.

    Les identifiants sont réattribués par la base (les séquences PostgreSQL restent maîtresses) et
    les clés étrangères suivies : le dictionnaire rendu donne, par table, ancien id -> nouvel id.
    Les chemins absolus du dossier de référence sont réécrits vers celui du test.
    """
    remap: dict[str, dict[int, int]] = {}
    old = str(old_root)
    for table in models.metadata.sorted_tables:
        if table.name == migrations.schema_migrations.name:
            continue
        refs = {c.name: next(iter(c.foreign_keys)).column.table.name for c in table.columns if c.foreign_keys}
        keys = [c.name for c in table.primary_key.columns]
        auto = len(keys) == 1 and isinstance(table.c[keys[0]].type, sa.Integer)
        for row in src.execute(sa.select(table)).mappings():
            vals = {k: (v.replace(old, str(new_root)) if isinstance(v, str) else v) for k, v in row.items()}
            if auto:
                vals.pop(keys[0], None)
            for col, ref in refs.items():
                if vals.get(col) is not None:
                    vals[col] = remap[ref][vals[col]]
            res = dst.execute(table.insert().values(**vals))
            if auto:
                remap.setdefault(table.name, {})[row[keys[0]]] = res.inserted_primary_key[0]
    dst.commit()
    return remap


@pytest.fixture
def env(tmp_path, env_base):
    """Copie isolée de l'environnement de référence : base, fichiers et application pour ce test."""
    base = env_base
    for name in ("output", "state", "pub"):
        if (base["root"] / name).exists():
            shutil.copytree(base["root"] / name, tmp_path / name)
    path = tmp_path / "bo.db"
    db.init(path)
    conn = db.connect(path)
    moved = _clone(base["conn"], conn, base["root"], tmp_path)
    app = create_app(path, regen_background=False)
    app.state.events_file = str(tmp_path / "events.json")  # never the real programme file
    yield {
        "app": app,
        "conn": conn,
        "path": path,
        "mid": moved["mosques"][base["mid"]],
        "ids": {name: moved["users"][uid] for name, uid in base["ids"].items()},
        "kid": moved["khutbas"][base["kid"]],
        "root": tmp_path,
        "tmp": tmp_path,
    }
    conn.close()


@pytest.fixture(autouse=True)
def transcription_locale(monkeypatch, tmp_path):
    """Les tests tournent sur le moteur local, quel que soit le réglage de la machine.

    Sans cela, un poste configuré pour la transcription distante (`SADA_TRANSCRIPTION_MODE`, ou un
    `data/transcription.json` de production) enverrait les fichiers de test à un vrai fournisseur.
    Les tests du distant posent eux-mêmes leurs variables.
    """
    for name in [k for k in os.environ if k.startswith("SADA_TRANSCRIPTION_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "transcription-absent.json"))


def _drop_test_schemas() -> None:
    """Drop the per-test PostgreSQL schemas (`t_*`) so the test database does not grow."""
    url = orm.database_url()
    if not url:
        return
    with orm._LOCK:
        for e in list(orm._ENGINES.values()):
            e.dispose()
        orm._ENGINES.clear()
    eng = sa.create_engine(sa.make_url(url).set(drivername="postgresql+psycopg"))
    try:
        names = [n for n in sa.inspect(eng).get_schema_names() if n.startswith("t_")]
        for name in names:  # one transaction per schema: a single one runs out of locks
            with eng.begin() as c:
                c.execute(sa.schema.DropSchema(name, cascade=True, if_exists=True))
    finally:
        eng.dispose()


@pytest.fixture(scope="session", autouse=True)
def _clean_test_schemas():
    _drop_test_schemas()  # leftovers of an interrupted run
    yield
    _drop_test_schemas()
