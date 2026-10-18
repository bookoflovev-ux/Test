"""Lancement : python -m vigiefs [--host 0.0.0.0] [--port 8470] [--data D:\\VigieFS\\data]"""

import argparse
import getpass
import logging
import logging.handlers
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vigiefs", description="VigieFS — supervision de serveurs de fichiers")
    parser.add_argument("--host", default=os.environ.get("VIGIEFS_HOST", "127.0.0.1"),
                        help="adresse d'écoute (0.0.0.0 pour toutes les interfaces)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIGIEFS_PORT", "8470")))
    parser.add_argument("--data", default=os.environ.get("VIGIEFS_DATA"),
                        help="dossier des données (base SQLite, journaux)")
    parser.add_argument("--ssl-certfile", help="certificat TLS (PEM) pour servir en HTTPS")
    parser.add_argument("--ssl-keyfile", help="clé privée TLS (PEM)")
    parser.add_argument("--reset-password", metavar="UTILISATEUR",
                        help="réinitialise le mot de passe d'un compte (ou le crée en administrateur)")
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data or os.path.join(os.getcwd(), "data"))
    os.makedirs(data_dir, exist_ok=True)

    if args.reset_password:
        from .auth import AuthService, hash_password, validate_password_strength
        from .db import Database
        db = Database(os.path.join(data_dir, "vigiefs.db"))
        pwd = getpass.getpass("Nouveau mot de passe : ")
        err = validate_password_strength(pwd)
        if err:
            sys.exit(err)
        user = db.query_one("SELECT id FROM users WHERE username=?", (args.reset_password,))
        if user:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pwd), user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        else:
            AuthService(db).create_user(args.reset_password, pwd, "admin")
        db.audit("console", "réinitialisation mot de passe", args.reset_password)
        print("Mot de passe mis à jour.")
        return

    handler = logging.handlers.RotatingFileHandler(
        os.path.join(data_dir, "vigiefs.log"), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[handler, logging.StreamHandler()])

    import uvicorn
    from .api import create_app

    app = create_app(data_dir)
    scheme = "https" if args.ssl_certfile else "http"
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"\n  VigieFS démarré — interface : {scheme}://{shown}:{args.port}/   (données : {data_dir})\n")
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile, log_level="warning")


if __name__ == "__main__":
    main()
