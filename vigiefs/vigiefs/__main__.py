"""Lancement et pilotage de VigieFS.

    python -m vigiefs [serve]     interface + moteur (mode console)
    python -m vigiefs supervise   mode service 24 h/24 : relance automatique sur plantage ou blocage
    python -m vigiefs status      état du service, dernier incident, disponibilité
    python -m vigiefs stop        arrêt volontaire (jamais relancé automatiquement)
    python -m vigiefs restart     redémarrage propre
    python -m vigiefs restore-db  restaure la dernière sauvegarde saine de la base
    python -m vigiefs demo        jeu de démonstration fictif (dossier data-demo) puis lancement

Options communes : --host 0.0.0.0 --port 8470 --data D:\\VigieFS\\data
"""

import argparse
import faulthandler
import getpass
import logging
import logging.handlers
import os
import shutil
import signal
import sqlite3
import sys
import threading
import time

COMMANDS = ("serve", "supervise", "status", "stop", "restart", "restore-db", "demo")


def _parser():
    parser = argparse.ArgumentParser(prog="vigiefs", description="VigieFS — supervision de serveurs de fichiers")
    parser.add_argument("command", nargs="?", default="serve", choices=COMMANDS,
                        help="serve (défaut), supervise, status, stop, restart, restore-db, demo")
    parser.add_argument("--host", default=os.environ.get("VIGIEFS_HOST", "127.0.0.1"),
                        help="adresse d'écoute (0.0.0.0 pour toutes les interfaces)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIGIEFS_PORT", "8470")))
    parser.add_argument("--data", default=os.environ.get("VIGIEFS_DATA"),
                        help="dossier des données (base SQLite, journaux)")
    parser.add_argument("--ssl-certfile", help="certificat TLS (PEM) pour servir en HTTPS")
    parser.add_argument("--ssl-keyfile", help="clé privée TLS (PEM)")
    parser.add_argument("--reset-password", metavar="UTILISATEUR",
                        help="réinitialise le mot de passe d'un compte (ou le crée en administrateur)")
    parser.add_argument("--supervised", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", default=os.environ.get("VIGIEFS_SERVICE_MODE", "console"),
                        help="supervise : type d'hébergement affiché (windows-service, systemd, tache-planifiee…)")
    parser.add_argument("--open-browser", action="store_true",
                        help="supervise : ouvre le navigateur dès que l'interface répond")
    parser.add_argument("--hang-timeout", type=int, default=300,
                        help="supervise : délai sans battement de cœur avant relance (s)")
    parser.add_argument("--reason", default="", help="stop / restart : motif journalisé")
    parser.add_argument("--wait", type=int, default=120, help="stop / restart : attente maximale (s)")
    parser.add_argument("--from", dest="from_file", help="restore-db : fichier de sauvegarde à restaurer")
    return parser


def _logging(data_dir, filename, console=True):
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(data_dir, filename), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[handler, logging.StreamHandler()] if console else [handler])


def _reset_password(args, data_dir):
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


def _crash_reporting(rdir):
    """Plantages natifs et piles des threads (signal SIGUSR1 du superviseur avant un arrêt pour blocage)."""
    log = logging.getLogger("vigiefs")
    fh = open(os.path.join(rdir, "plantages.log"), "a", encoding="utf-8")  # noqa: SIM115 — vit avec le processus
    faulthandler.enable(file=fh, all_threads=True)
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, file=fh, all_threads=True)

    def thread_hook(hook_args):
        log.critical("Exception non gérée dans le thread %s", getattr(hook_args.thread, "name", "?"),
                     exc_info=(hook_args.exc_type, hook_args.exc_value, hook_args.exc_traceback))
    threading.excepthook = thread_hook


def serve(args, data_dir):
    supervised = args.supervised or os.environ.get("VIGIEFS_SUPERVISED") == "1"
    # Sous superviseur, la console est déjà capturée dans run/console.log : journal fichier uniquement.
    _logging(data_dir, "vigiefs.log", console=not supervised)
    import uvicorn

    from . import service
    from .api import create_app

    _crash_reporting(service.run_dir(data_dir))
    try:
        app = create_app(data_dir)
    except sqlite3.DatabaseError as exc:
        logging.getLogger("vigiefs").critical(
            "Base de données illisible (%s). Restaurez la dernière sauvegarde : python -m vigiefs restore-db "
            "--data \"%s\"", exc, data_dir)
        return 1
    config = uvicorn.Config(app, host=args.host, port=args.port, ssl_certfile=args.ssl_certfile,
                            ssl_keyfile=args.ssl_keyfile, log_level="warning", timeout_keep_alive=30)
    server = uvicorn.Server(config)
    app.state.control.bind(server)
    if supervised:
        service.watch_stdin(server)
    scheme = "https" if args.ssl_certfile else "http"
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"\n  VigieFS démarré — interface : {scheme}://{shown}:{args.port}/   (données : {data_dir})\n", flush=True)
    server.run()
    return 0


def _open_browser_when_ready(args):
    from . import service
    url = f"{'https' if args.ssl_certfile else 'http'}://localhost:{args.port}/"
    probe = service.probe_url("127.0.0.1", args.port, tls=bool(args.ssl_certfile))

    def run():
        import webbrowser
        for _ in range(120):
            if service.http_probe(probe, timeout=2):
                webbrowser.open(url)
                return
            time.sleep(1)
    threading.Thread(target=run, name="vigiefs-navigateur", daemon=True).start()


def supervise(args, data_dir):
    _logging(data_dir, "superviseur.log")
    from . import service
    if args.open_browser and os.environ.get("VIGIEFS_NO_BROWSER") != "1":
        _open_browser_when_ready(args)
    args.data_dir = data_dir
    sup = service.Supervisor(
        service.worker_command(args), data_dir, mode=args.mode, hang_timeout=args.hang_timeout,
        probe=service.probe_url(args.host, args.port, tls=bool(args.ssl_certfile)), cwd=os.getcwd())
    sup.install_signal_handlers()
    scheme = "https" if args.ssl_certfile else "http"
    print(f"\n  VigieFS en mode service 24 h/24 — interface : {scheme}://localhost:{args.port}/\n"
          f"  Arrêt volontaire : Ctrl+C dans cette fenêtre, ou python -m vigiefs stop --data \"{data_dir}\"\n",
          flush=True)
    return sup.run()


def _fmt_ts(ts):
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts)) if ts else "—"


def _fmt_dur(sec):
    sec = int(sec or 0)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{d} j {h} h {m:02d} min" if d else f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s"


def status(args, data_dir):
    from . import service
    st = service.status(data_dir)
    state = st["state"]
    print(f"VigieFS : {st['status']}")
    if st["running"]:
        print(f"  Superviseur : PID {state.get('supervisor_pid')} depuis {_fmt_ts(state.get('supervisor_started_at'))}"
              f" (mode {state.get('mode')})")
        if state.get("child_pid"):
            print(f"  Moteur      : PID {state.get('child_pid')} en service depuis "
                  f"{_fmt_dur(time.time() - (state.get('child_started_at') or time.time()))}")
        print(f"  Relances    : {state.get('restarts', 0)} (dont {state.get('incidents', 0)} après incident)")
    if state.get("last_incident"):
        inc = state["last_incident"]
        print(f"  Dernier incident : {_fmt_ts(inc.get('at'))} — "
              f"{service.INCIDENT_LABEL.get(inc.get('kind'), inc.get('kind'))} ({inc.get('detail')})")
    if st["voluntary_stop"]:
        vs = st["voluntary_stop"]
        print(f"  Arrêté volontairement le {_fmt_ts(vs.get('at'))} par {vs.get('actor')}"
              f"{' — ' + vs['reason'] if vs.get('reason') else ''}")
    events = service.read_journal(service.run_dir(data_dir), since=time.time() - 30 * 86400)
    av = service.availability(events, time.time() - 30 * 86400)
    if av["pct"] is not None:
        print(f"  Disponibilité 30 j : {str(av['pct']).replace('.', ',')} % — {av['incidents']} incident(s), "
              f"indisponibilité subie {_fmt_dur(av['unplanned_sec'])}")
    if st["running"]:
        return 0
    return 3 if st["voluntary_stop"] or state.get("status") in (None, "arrêté") else 1


def _actor():
    try:
        return f"console ({getpass.getuser()})"
    except Exception:  # noqa: BLE001
        return "console"


def stop(args, data_dir):
    from . import service
    rdir = service.run_dir(data_dir)
    if not service.supervisor_running(rdir):
        # Marqueur d'arrêt volontaire : la tâche de garde (installation sans service) ne relancera pas.
        service.write_json(os.path.join(rdir, service.STOPPED),
                           {"actor": _actor(), "reason": args.reason or "", "at": time.time()})
        print("VigieFS n'est pas en cours d'exécution : arrêt volontaire enregistré.")
        return 0
    service.send_command(rdir, "stop", _actor(), args.reason or "")
    print("Arrêt demandé, attente de la fin des opérations en cours…", flush=True)
    end = time.time() + args.wait
    while time.time() < end:
        if not service.supervisor_running(rdir):
            print("VigieFS est arrêté. Il ne redémarrera pas automatiquement tant qu'il n'est pas relancé "
                  "(service, tâche planifiée ou « python -m vigiefs supervise »).")
            return 0
        time.sleep(0.5)
    print("Le superviseur ne s'est pas arrêté dans le délai imparti.", file=sys.stderr)
    return 1


def restart(args, data_dir):
    from . import service
    rdir = service.run_dir(data_dir)
    if not service.supervisor_running(rdir):
        print("VigieFS n'est pas lancé en mode service : démarrez le service (ou « python -m vigiefs supervise »).",
              file=sys.stderr)
        return 1
    before = (service.read_json(os.path.join(rdir, service.STATE), {}) or {}).get("child_started_at")
    service.send_command(rdir, "restart", _actor(), args.reason or "")
    print("Redémarrage demandé…", flush=True)
    end = time.time() + args.wait
    while time.time() < end:
        state = service.read_json(os.path.join(rdir, service.STATE), {}) or {}
        if state.get("child_started_at") != before and state.get("status") == "en service":
            print("VigieFS a redémarré et est de nouveau en service.")
            return 0
        time.sleep(0.5)
    print("Redémarrage non confirmé dans le délai imparti : consultez « python -m vigiefs status ».", file=sys.stderr)
    return 1


def restore_db(args, data_dir):
    from . import service
    if service.supervisor_running(service.run_dir(data_dir)):
        print("Arrêtez d'abord VigieFS (python -m vigiefs stop).", file=sys.stderr)
        return 1
    folder = os.path.join(data_dir, "sauvegardes-base")
    candidates = [args.from_file] if args.from_file else sorted(
        (os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".db")), reverse=True) \
        if os.path.isdir(folder) else []
    for path in candidates:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            conn.close()
        except sqlite3.Error:
            ok = False
        if not ok:
            print(f"Sauvegarde ignorée (endommagée) : {path}")
            continue
        target = os.path.join(data_dir, "vigiefs.db")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(target + suffix):
                os.replace(target + suffix, f"{target}{suffix}.endommagee-{stamp}")
        shutil.copy2(path, target)
        service.journal(service.run_dir(data_dir), "reparation", component="database",
                        detail=f"base restaurée depuis {os.path.basename(path)}")
        print(f"Base restaurée depuis {path}. Relancez VigieFS.")
        return 0
    print("Aucune sauvegarde saine trouvée.", file=sys.stderr)
    return 1


def demo(args, data_dir):
    """Prépare le jeu fictif (une seule fois), ouvre le navigateur et lance VigieFS en mode supervisé."""
    from . import demo as demo_data
    print("Préparation du jeu de démonstration…", flush=True)
    res = demo_data.create(data_dir)
    if res["created"]:
        for s in res["shares"]:
            print(f"  {s['client']} / {s['name']} : {s['files']} fichiers fictifs")
    else:
        print("  Jeu de démonstration déjà présent : réutilisé.")
    print(f"  Dossiers fictifs : {res['base']}\n"
          "  Pour tout effacer : arrêtez VigieFS puis supprimez le dossier de données de la démo.", flush=True)
    args.open_browser = True
    return supervise(args, data_dir)


def main(argv=None):
    args = _parser().parse_args(argv)
    default_dir = "data-demo" if args.command == "demo" else "data"
    data_dir = os.path.abspath(args.data or os.path.join(os.getcwd(), default_dir))
    os.makedirs(data_dir, exist_ok=True)
    if args.reset_password:
        _reset_password(args, data_dir)
        return 0
    handler = {"serve": serve, "supervise": supervise, "status": status, "stop": stop, "restart": restart,
               "restore-db": restore_db, "demo": demo}[args.command]
    return handler(args, data_dir)


if __name__ == "__main__":
    sys.exit(main())
