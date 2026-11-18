"""Command line of the back-office.

    python -m sada.backoffice create-admin --username admin [--mosque ma-mosquee --mosque-name "Votre mosquée"]
    python -m sada.backoffice create-user --username imam1 --role imam --name "Imam Abdallah"
    python -m sada.backoffice mosque --slug ma-mosquee [--name ...] [--orchestrator-url ...]  # créer / configurer
    python -m sada.backoffice import [--run 20261106_220224] [--no-video]
    python -m sada.backoffice serve [--host 0.0.0.0] [--port 8090]

The password is asked on the keyboard (never as an argument); for a script: --password-stdin.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select, update

from ..i18n import _
from . import auth, db, importer, internal, orm
from .models import Mosque, User


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass(_("Password ({min_chars} characters min.): ", min_chars=auth.MIN_PASSWORD))
    if pw != getpass.getpass(_("Confirm: ")):
        sys.exit(_("The two entries differ."))
    return pw


def _create_mosque(conn, slug: str, name: str) -> dict:
    mid = db.create_mosque(conn, slug, name)
    print(_("Mosque created: {slug} (#{id})", slug=slug, id=mid))
    return orm.first(conn, Mosque, id=mid)


def _mosque(conn, slug: str | None, name: str | None = None, create: bool = False) -> dict:
    if slug:
        m = orm.first(conn, Mosque, slug=slug)
    else:
        ms = orm.rows(conn, select(Mosque).order_by(Mosque.id))
        if len(ms) > 1:
            sys.exit(_("Several mosques: specify --mosque <slug>."))
        m = ms[0] if ms else None
    if m is None:
        if not create:
            sys.exit(
                _(
                    "No mosque: create it with `mosque --slug ... --name ...` or "
                    "`create-admin --mosque ... --mosque-name ...`."
                )
            )
        m = _create_mosque(conn, slug or "mosquee", name or "Ma mosquée")
    return m


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m sada.backoffice", description="Sadā")
    p.add_argument(
        "--db",
        default=os.environ.get("SADA_DB", str(db.DEFAULT_DB)),
        help=_("SQLite database (default: data/backoffice/sada.db)"),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("create-admin", help=_("create an administrator account (and the mosque if needed)"))
    a.add_argument("--username", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--mosque", help=_("slug of the mosque"))
    a.add_argument("--mosque-name", help=_("name of the mosque if it is created"))
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--platform", action="store_true", help=_("also platform administrator (model, transcription)"))

    u = sub.add_parser("create-user", help=_("create an account (admin, manager, imam)"))
    u.add_argument("--username", required=True)
    u.add_argument("--role", required=True, choices=auth.ROLES)
    u.add_argument("--name", default="")
    u.add_argument("--mosque")
    u.add_argument("--password-stdin", action="store_true")

    m = sub.add_parser("mosque", help=_("create or configure a mosque"))
    m.add_argument("--slug", required=True)
    m.add_argument("--name")
    for f in db.MOSQUE_SETTINGS:
        if f != "name":
            m.add_argument("--" + f.replace("_", "-"))
    m.add_argument(
        "--agent-token", action="store_true", help=_("issue a new orchestrator token (replaces the previous one)")
    )
    m.add_argument("--revoke-agent-token", action="store_true", help=_("revoke the orchestrator token"))
    m.add_argument(
        "--publish-key", action="store_true", help=_("issue a new stream key for OBS (replaces the previous one)")
    )

    i = sub.add_parser("import", help=_("import the pipeline runs"))
    i.add_argument("--mosque")
    i.add_argument("--run", help=_("one precise run (e.g. 20261106_220224); default: all"))
    i.add_argument("--no-video", action="store_true", help=_("do not extract the video of the session"))

    x = sub.add_parser("search-index", help=_("index the imported sermons for the search (backfill)"))
    x.add_argument("--force", action="store_true", help=_("rebuild the index of every sermon, even unchanged"))
    x.add_argument("--embed", action="store_true", help=_("also compute the semantic vectors (never during a live)"))

    e = sub.add_parser("fetch-engine", help=_("download the browser that renders the screens"))
    e.add_argument("--version", help=_("Chrome for Testing version (default: the stable one)"))

    s = sub.add_parser("serve", help=_("start the back office"))
    s.add_argument("--host", default=os.environ.get("SADA_BO_HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.environ.get("SADA_BO_PORT", "8090")))
    s.add_argument(
        "--secure-cookies", action="store_true", help=_("Secure cookies (behind HTTPS, central server mode)")
    )
    s.add_argument(
        "--import-every", type=float, default=60.0, help=_("automatic import every N seconds (0 = disabled)")
    )

    args = p.parse_args(argv)
    db.init(args.db)
    conn = db.connect(args.db)
    try:
        if args.cmd == "create-admin":
            mo = _mosque(conn, args.mosque, args.mosque_name, create=True)
            uid = auth.create_user(conn, mo["id"], args.username, _password(args), "admin", args.name)
            # the first administrator of an install is also the platform's: a self-hosted
            # mosque keeps its model and transcription settings
            first = not conn.scalar(select(User.id).where(User.platform_admin == 1, User.id != uid).limit(1))
            if args.platform or first:
                conn.execute(update(User).where(User.id == uid).values(platform_admin=1))
                orm.done(conn)
            db.audit(
                conn,
                mo["id"],
                None,
                "utilisateur.cree",
                "utilisateur",
                uid,
                identifiant=args.username,
                role="admin",
                plateforme=bool(args.platform or first),
                via="ligne de commande",
            )
            print(_("Administrator “{username}” created for {mosque}.", username=args.username, mosque=mo["name"]))
        elif args.cmd == "create-user":
            mo = _mosque(conn, args.mosque)
            uid = auth.create_user(conn, mo["id"], args.username, _password(args), args.role, args.name)
            db.audit(
                conn,
                mo["id"],
                None,
                "utilisateur.cree",
                "utilisateur",
                uid,
                identifiant=args.username,
                role=args.role,
                via="ligne de commande",
            )
            print(
                _(
                    "Account “{username}” ({role}) created for {mosque}.",
                    username=args.username,
                    role=auth.role_label(args.role),
                    mosque=mo["name"],
                )
            )
        elif args.cmd == "mosque":
            mo = _mosque(conn, args.slug, args.name or args.slug, create=True)
            vals = {k: getattr(args, k) for k in db.MOSQUE_SETTINGS if getattr(args, k) is not None}
            if vals:
                db.update_mosque(conn, mo["id"], **vals)
                print(_("Configuration updated: {fields}", fields=", ".join(vals)))
            if args.revoke_agent_token:
                internal.revoke_agent_token(conn, mo["id"])
                db.audit(conn, mo["id"], None, "jeton_agent.revoque", "mosquee", mo["id"], via="ligne de commande")
                print(_("Orchestrator token revoked."))
            if args.publish_key:
                k = internal.new_publish_key(conn, mo["id"])
                db.audit(conn, mo["id"], None, "cle_diffusion.emise", "mosquee", mo["id"], via="ligne de commande")
                print(_("Stream key (shown once). In OBS, server and key:"))
                print(f"  rtmp://<host>:1935/live/{mo['slug']}?user={mo['slug']}&pass={k}")
                print(f"  srt://<host>:8890?streamid=publish:live/{mo['slug']}:{mo['slug']}:{k}")
            if args.agent_token:
                t = internal.new_agent_token(conn, mo["id"])
                db.audit(conn, mo["id"], None, "jeton_agent.emis", "mosquee", mo["id"], via="ligne de commande")
                print(_("Orchestrator token (shown once, set it as SADA_AGENT_TOKEN on the mosque's machine):"))
                print(t)
        elif args.cmd == "import":
            mo = _mosque(conn, args.mosque)
            res = (
                [importer.import_run(conn, mo, args.run, not args.no_video)]
                if args.run
                else importer.scan(conn, mo, not args.no_video)
            )
            for r in res:
                print(
                    _(
                        "{run}: {khutbas} khutba(s), {clips} clip(s) added",
                        run=r["run"],
                        khutbas=r["khutbas_new"],
                        clips=r["clips_new"],
                    )
                    + (_("; ignored: {runs}", runs=", ".join(r["skipped"])) if r["skipped"] else "")
                )
            if not res:
                print(_("No run found."))
        elif args.cmd == "search-index":
            from . import search, traitement

            n = search.index_all(conn, force=args.force)
            if args.embed:
                while traitement.embed_pending(conn, budget_s=3600):
                    pass
            print(
                _(
                    "{n} sermon(s) indexed, {pending} segment(s) without semantic vector",
                    n=n,
                    pending=search.pending_embeddings(conn),
                )
            )
        elif args.cmd == "fetch-engine":
            from .template_render import RenderUnavailable, fetch_engine

            try:
                fetch_engine(args.version)
            except RenderUnavailable as exc:
                print(_("Failed: {error}", error=exc))
                return 1
        elif args.cmd == "serve":
            import uvicorn

            from .app import create_app

            if not conn.scalar(select(User.id).where(User.role == "admin", User.active == 1).limit(1)):
                print(
                    _("Warning: no administrator. Create one: python -m sada.backoffice create-admin --username admin")
                )
            conn.close()
            app = create_app(args.db, secure_cookies=args.secure_cookies, auto_import_s=args.import_every)
            print(
                _("Back office: http://{host}:{port}/  (database {db})", host=args.host, port=args.port, db=args.db),
                flush=True,
            )
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning", proxy_headers=False)
            return 0
    except (ValueError, FileNotFoundError) as e:
        sys.exit(_("Error: {error}", error=e))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
