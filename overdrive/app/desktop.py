"""Intégration bureau Windows : ouverture de l'interface en fenêtre d'application, démarrage caché
via la tâche planifiée (sans fenêtre cmd ni UAC à chaque fois), icône dans la zone de notification."""
import ctypes
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

from . import config, journal
from .system import IS_WINDOWS, run

TASK = "Overdrive"                # lancement à la demande (droits admin, caché)
TASK_AUTOSTART = "Overdrive Autostart"  # lancement à l'ouverture de session
ASSETS = os.path.join(config.BASE_DIR, "assets")


PORT = None  # port forcé par la ligne de commande (sinon celui de la configuration)


def url():
    return f"http://127.0.0.1:{PORT or config.get()['port']}"


def is_running(timeout=0.8):
    try:
        with urllib.request.urlopen(url() + "/api/health", timeout=timeout) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


def _edge():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(p):
                return p
    return None


def open_ui():
    """Ouvre l'interface dans une fenêtre d'application (Edge --app), sinon dans le navigateur par défaut."""
    from .system import is_admin
    if IS_WINDOWS and is_admin():
        # Depuis le serveur (administrateur), on passe par l'Explorateur pour ouvrir la fenêtre en mode normal :
        # un navigateur lancé en administrateur affiche un avertissement et ne doit pas l'être.
        import subprocess
        lnk = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Overdrive.lnk")
        subprocess.Popen(["explorer.exe", lnk if os.path.exists(lnk) else url()])
        return
    edge = _edge() if IS_WINDOWS else None
    if edge:
        import subprocess
        subprocess.Popen([edge, f"--app={url()}", "--window-size=1440,900"], creationflags=0x08000000)
    else:
        webbrowser.open(url())


def start_hidden_server():
    """Démarre le serveur en administrateur sans fenêtre : via la tâche planifiée créée par l'installeur,
    sinon en demandant l'élévation (UAC) une seule fois."""
    if not IS_WINDOWS:
        return False
    if run(["schtasks", "/Run", "/TN", TASK], timeout=15)["ok"]:
        return True
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    script = os.path.join(config.BASE_DIR, "run.py")
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, f'"{script}" --background', config.BASE_DIR, 0)
    return rc > 32


def launch_and_open():
    """Action du raccourci bureau : démarre Overdrive si besoin, puis ouvre l'interface."""
    if not is_running():
        if not start_hidden_server():
            return False
        for _ in range(60):
            time.sleep(0.5)
            if is_running():
                break
        else:
            return False
    open_ui()
    return True


# --- Démarrage avec Windows -------------------------------------------------------------------------------------------

def autostart_status():
    if not IS_WINDOWS:
        return {"available": False, "enabled": False}
    r = run(["schtasks", "/Query", "/TN", TASK_AUTOSTART, "/FO", "CSV", "/NH"], timeout=15)
    if not r["ok"]:
        return {"available": False, "enabled": False}
    disabled = "disabled" in r["out"].lower() or "désactiv" in r["out"].lower()
    return {"available": True, "enabled": not disabled}


def set_autostart(enabled: bool):
    if not autostart_status()["available"]:
        raise RuntimeError("Tâche de démarrage absente : relancez « Installer Overdrive.bat ».")
    r = run(["schtasks", "/Change", "/TN", TASK_AUTOSTART, "/ENABLE" if enabled else "/DISABLE"], timeout=15)
    if not r["ok"]:
        raise RuntimeError(r["err"] or r["out"])
    journal.log("settings", f"Démarrage avec Windows {'activé' if enabled else 'désactivé'}", "success")
    return autostart_status()


# --- Icône de la zone de notification ---------------------------------------------------------------------------------

def start_tray(server):
    """Icône près de l'horloge : ouvrir, optimisation express, Game Boost, quitter. Ignorée si pystray est absent."""
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None
    from . import gaming, jobs
    from .main import _quick

    def notify(icon, text):
        try:
            icon.notify(text, "Overdrive")
        except Exception:
            pass

    def quick(icon, _item):
        job = jobs.submit("Optimisation express", _quick)

        def wait():
            while job.status == "running":
                time.sleep(1)
            if job.status == "done":
                r = job.result
                notify(icon, f"Optimisation terminée : {r['clean']['freed'] / 1024 ** 2:.0f} Mo nettoyés.")
            else:
                notify(icon, f"Échec : {job.error}")
        threading.Thread(target=wait, daemon=True).start()

    def boost(icon, _item):
        try:
            r = gaming.game_boost()
            notify(icon, f"Game Boost : {len(r['killed'])} appli(s) fermée(s).")
        except Exception as e:
            notify(icon, f"Game Boost : {e}")

    def quit_(icon, _item):
        icon.stop()
        server.should_exit = True

    image = Image.open(os.path.join(ASSETS, "icon-256.png"))
    icon = pystray.Icon("Overdrive", image, "Overdrive — centre de pilotage", menu=pystray.Menu(
        pystray.MenuItem("Ouvrir Overdrive", lambda i, it: open_ui(), default=True),
        pystray.MenuItem("Optimisation express", quick),
        pystray.MenuItem("Game Boost", boost),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter Overdrive", quit_),
    ))
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
    return icon
