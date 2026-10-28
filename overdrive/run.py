"""Point d'entrée d'Overdrive.

  pythonw run.py --open        raccourci du bureau : démarre Overdrive caché si besoin, puis ouvre l'interface
  pythonw run.py --background  serveur seul, sans fenêtre (tâche planifiée / démarrage avec Windows)
  python  run.py               mode console (dépannage) : serveur + ouverture de l'interface
"""
import argparse
import os
import sys
import threading
import time

from app import config


def _redirect_output():
    """Sous pythonw il n'y a pas de console : tout part dans data/overdrive.log (sinon uvicorn plante)."""
    path = os.path.join(config.DATA_DIR, "overdrive.log")
    if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 ** 2:
        os.replace(path, path + ".old")
    log = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


def main():
    cfg = config.get()
    parser = argparse.ArgumentParser(description="Overdrive — centre de pilotage PC")
    parser.add_argument("--port", type=int, default=cfg["port"])
    parser.add_argument("--open", action="store_true", help="ouvrir l'interface (démarre le serveur caché si besoin)")
    parser.add_argument("--background", action="store_true", help="serveur sans fenêtre ni ouverture de l'interface")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir l'interface")
    args = parser.parse_args()

    hidden = args.open or args.background or sys.stdout is None
    if hidden:
        _redirect_output()

    from app import desktop
    desktop.PORT = args.port
    if args.open:
        ok = desktop.launch_and_open()
        if not ok:
            print(time.strftime("%F %T"), "Impossible de démarrer Overdrive (tâche planifiée absente ou UAC refusé).")
        return 0 if ok else 1
    if desktop.is_running():  # déjà lancé : on ouvre simplement l'interface
        if not (args.background or args.no_browser):
            desktop.open_ui()
        return 0

    import uvicorn

    from app.main import app
    from app.system import is_admin

    host = "0.0.0.0" if cfg["allow_remote"] else "127.0.0.1"
    print("=" * 64)
    print(time.strftime("%F %T"), " OVERDRIVE — centre de pilotage PC")
    print(f"  Interface locale : {desktop.url()}")
    if cfg["allow_remote"]:
        print(f"  Accès à distance : http://<IP-du-PC>:{args.port}/?token=…  (lien complet dans Paramètres)")
    if not is_admin():
        print("  ⚠ Lancé sans droits administrateur : certaines optimisations seront indisponibles.")
    print("=" * 64)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=args.port, log_level="warning", use_colors=False))
    app.state.server = server
    desktop.start_tray(server)
    if not (args.background or args.no_browser):
        threading.Thread(target=lambda: (time.sleep(1.5), desktop.open_ui()), daemon=True).start()
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
