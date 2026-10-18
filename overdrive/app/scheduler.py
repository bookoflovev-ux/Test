"""Tâches automatiques : nettoyage périodique, optimisation RAM au-delà d'un seuil, sauvegarde planifiée."""
import threading
import time

import psutil

from . import backup, cleaner, config, jobs, memory
from .system import IS_WINDOWS


def _loop():
    last_ram = 0
    while True:
        time.sleep(60)
        cfg = config.get()
        sch = cfg["scheduler"]
        st = cfg["state"]
        try:
            if sch["auto_clean"] and time.time() - st.get("last_clean", 0) > sch["clean_every_hours"] * 3600:
                config.update({"state": {"last_clean": time.time()}})
                jobs.submit("Nettoyage automatique", cleaner.clean, sch["clean_categories"])
            if IS_WINDOWS and sch["auto_ram"] and psutil.virtual_memory().percent >= sch["ram_threshold"] \
                    and time.time() - last_ram > 600:
                last_ram = time.time()
                memory.optimize()
            every = sch.get("backup_every_hours", 0)
            if sch.get("auto_backup") and every and time.time() - st.get("last_backup", 0) > every * 3600 \
                    and cfg["backup"]["sources"] and cfg["backup"]["destination"]:
                config.update({"state": {"last_backup": time.time()}})
                jobs.submit("Sauvegarde automatique", backup.run_backup)
        except Exception:
            pass


def start():
    threading.Thread(target=_loop, daemon=True, name="scheduler").start()
