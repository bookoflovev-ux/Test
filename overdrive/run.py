"""Point d'entrée : python run.py  (ou start.bat sous Windows, qui demande les droits administrateur)."""
import argparse
import sys
import threading
import time
import webbrowser

import uvicorn

from app import config
from app.system import is_admin


def main():
    cfg = config.get()
    parser = argparse.ArgumentParser(description="Overdrive — centre de pilotage PC")
    parser.add_argument("--port", type=int, default=cfg["port"])
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir l'interface automatiquement")
    args = parser.parse_args()

    host = "0.0.0.0" if cfg["allow_remote"] else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    print("=" * 64)
    print("  OVERDRIVE — centre de pilotage PC")
    print(f"  Interface locale : {url}")
    if cfg["allow_remote"]:
        print(f"  Accès à distance : http://<IP-du-PC>:{args.port}/?token={cfg['token']}")
    if not is_admin():
        print("  ⚠ Lancé sans droits administrateur : certaines optimisations seront indisponibles.")
    print("=" * 64)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    from app.main import app
    uvicorn.run(app, host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
