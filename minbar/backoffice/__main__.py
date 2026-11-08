"""Ligne de commande du back-office.

    python -m minbar.backoffice create-admin --username admin [--mosque ma-mosquee --mosque-name "Votre mosquée"]
    python -m minbar.backoffice create-user --username imam1 --role imam --name "Imam Abdallah"
    python -m minbar.backoffice mosque --slug ma-mosquee [--name ...] [--orchestrator-url ...]  # créer / configurer
    python -m minbar.backoffice import [--run 20261106_220224] [--no-video]
    python -m minbar.backoffice serve [--host 0.0.0.0] [--port 8090]

Le mot de passe est demandé au clavier (jamais en argument) ; pour un script : --password-stdin.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import auth, db, importer


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Mot de passe (10 caractères min.) : ")
    if pw != getpass.getpass("Confirmer : "):
        sys.exit("Les deux saisies diffèrent.")
    return pw


def _create_mosque(conn, slug: str, name: str) -> dict:
    mid = db.create_mosque(conn, slug, name)
    print(f"Mosquée créée : {slug} (#{mid})")
    return db.one(conn, "SELECT * FROM mosques WHERE id = ?", mid)


def _mosque(conn, slug: str | None, name: str | None = None, create: bool = False) -> dict:
    if slug:
        m = db.one(conn, "SELECT * FROM mosques WHERE slug = ?", slug)
    else:
        ms = db.all_(conn, "SELECT * FROM mosques ORDER BY id")
        if len(ms) > 1:
            sys.exit("Plusieurs mosquées : précisez --mosque <slug>.")
        m = ms[0] if ms else None
    if m is None:
        if not create:
            sys.exit("Aucune mosquée : créez-la avec `mosque --slug ... --name ...` ou `create-admin --mosque ... --mosque-name ...`.")
        m = _create_mosque(conn, slug or "mosquee", name or "Ma mosquée")
    return m


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m minbar.backoffice", description="Back-office Minbar Live")
    p.add_argument("--db", default=os.environ.get("MINBAR_DB", str(db.DEFAULT_DB)), help="base SQLite (défaut : data/backoffice/minbar.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("create-admin", help="créer un compte administrateur (et la mosquée si besoin)")
    a.add_argument("--username", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--mosque", help="slug de la mosquée")
    a.add_argument("--mosque-name", help="nom de la mosquée si elle est créée")
    a.add_argument("--password-stdin", action="store_true")

    u = sub.add_parser("create-user", help="créer un compte (admin, responsable, imam)")
    u.add_argument("--username", required=True)
    u.add_argument("--role", required=True, choices=auth.ROLES)
    u.add_argument("--name", default="")
    u.add_argument("--mosque")
    u.add_argument("--password-stdin", action="store_true")

    m = sub.add_parser("mosque", help="créer ou configurer une mosquée")
    m.add_argument("--slug", required=True)
    m.add_argument("--name")
    for f in db.MOSQUE_SETTINGS:
        if f != "name":
            m.add_argument("--" + f.replace("_", "-"))

    i = sub.add_parser("import", help="importer les runs du pipeline")
    i.add_argument("--mosque")
    i.add_argument("--run", help="un run précis (ex. 20261106_220224) ; défaut : tous")
    i.add_argument("--no-video", action="store_true", help="ne pas extraire la vidéo de la session")

    s = sub.add_parser("serve", help="lancer le back-office")
    s.add_argument("--host", default=os.environ.get("MINBAR_BO_HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.environ.get("MINBAR_BO_PORT", "8090")))
    s.add_argument("--secure-cookies", action="store_true", help="cookies Secure (derrière HTTPS, mode serveur central)")
    s.add_argument("--import-every", type=float, default=60.0, help="import automatique toutes les N s (0 = désactivé)")

    args = p.parse_args(argv)
    db.init(args.db)
    conn = db.connect(args.db)
    try:
        if args.cmd == "create-admin":
            mo = _mosque(conn, args.mosque, args.mosque_name, create=True)
            uid = auth.create_user(conn, mo["id"], args.username, _password(args), "admin", args.name)
            db.audit(conn, mo["id"], None, "utilisateur.cree", "utilisateur", uid, identifiant=args.username, role="admin", via="ligne de commande")
            print(f"Administrateur « {args.username} » créé pour {mo['name']}.")
        elif args.cmd == "create-user":
            mo = _mosque(conn, args.mosque)
            uid = auth.create_user(conn, mo["id"], args.username, _password(args), args.role, args.name)
            db.audit(conn, mo["id"], None, "utilisateur.cree", "utilisateur", uid, identifiant=args.username, role=args.role, via="ligne de commande")
            print(f"Compte « {args.username} » ({auth.ROLE_LABELS[args.role]}) créé pour {mo['name']}.")
        elif args.cmd == "mosque":
            mo = _mosque(conn, args.slug, args.name or args.slug, create=True)
            vals = {k: getattr(args, k) for k in db.MOSQUE_SETTINGS if getattr(args, k) is not None}
            if vals:
                db.update_mosque(conn, mo["id"], **vals)
                print("Configuration mise à jour : " + ", ".join(vals))
        elif args.cmd == "import":
            mo = _mosque(conn, args.mosque)
            res = [importer.import_run(conn, mo, args.run, not args.no_video)] if args.run else importer.scan(conn, mo, not args.no_video)
            for r in res:
                print(f"{r['run']} : {r['khutbas_new']} khutba(s), {r['clips_new']} clip(s) ajoutés" + (f" ; ignorées : {', '.join(r['skipped'])}" if r["skipped"] else ""))
            if not res:
                print("Aucun run trouvé.")
        elif args.cmd == "serve":
            import uvicorn

            from .app import create_app
            if not db.one(conn, "SELECT id FROM users WHERE role = 'admin' AND active = 1"):
                print("Attention : aucun administrateur. Créez-en un : python -m minbar.backoffice create-admin --username admin")
            conn.close()
            app = create_app(args.db, secure_cookies=args.secure_cookies, auto_import_s=args.import_every)
            print(f"Back-office : http://{args.host}:{args.port}/  (base {args.db})", flush=True)
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning", proxy_headers=False)
            return 0
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"Erreur : {e}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
