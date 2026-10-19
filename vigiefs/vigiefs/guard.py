"""Garde interne : auto-réparation des composants, battement de cœur, maintenance de la base.

Toutes les 10 s, la garde vérifie le planificateur, la surveillance temps réel, la file d'événements,
la base, l'espace disque des données et la mémoire. Un composant arrêté est relancé à chaud ; un
blocage impossible à résoudre à chaud déclenche un redémarrage propre par le superviseur.
"""

import ctypes
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time

from . import service
from .config import merged_setting

log = logging.getLogger("vigiefs.guard")

COMPONENT_LABEL = {
    "scheduler": "Planificateur",
    "monitor": "Surveillance temps réel",
    "events": "File d'événements",
    "database": "Base de données",
    "disk": "Espace disque des données",
    "memory": "Mémoire",
    "jobs": "Tâches de fond",
}
STATUS_RANK = {"ok": 0, "repaired": 1, "degraded": 2, "error": 3}
RESTARTABLE_JOBS = {"duplicates", "integrity", "acl_scan", "sensitive", "archive_simulate"}


def rss_mb():
    """Mémoire résidente du processus (Mo), sans dépendance externe."""
    try:
        if sys.platform.startswith("win"):
            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            from ctypes import wintypes
            kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
            # Types explicites : sans eux, le pseudo-handle -1 est tronqué en 32 bits sur Windows x64.
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize / 1048576
            return None
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except (OSError, ValueError, AttributeError):
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return peak / 1048576 if sys.platform == "darwin" else peak / 1024
        except (ImportError, OSError):
            return None


class Guard:
    def __init__(self, engine, data_dir, control=None, interval=10):
        self.engine = engine
        self.db = engine.db
        self.alerts = engine.alerts
        self.data_dir = os.path.abspath(data_dir)
        self.rdir = service.run_dir(self.data_dir)
        self.control = control
        self.interval = interval
        self.components = {}
        self.seq = 0
        self.started_at = time.time()
        self.last_check_at = None
        self.last_checkpoint = time.time()
        self.last_daily = None
        self.maintenance_info = self.db.get_setting("db_maintenance", {}) or {}
        self._maint_thread = None
        self._restart_requested = False
        self._memory_over_since = None
        self._stop = threading.Event()
        self._thread = None

    # --- cycle de vie ---
    def start(self):
        self._report_startup()
        self.beat()
        self._thread = threading.Thread(target=self._loop, name="vigiefs-garde", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001
                log.exception("Erreur de la garde")
            self.beat()
            self._stop.wait(self.interval)

    def beat(self):
        """Battement de cœur lu par le superviseur (numéro de séquence : insensible aux sauts d'horloge)."""
        self.seq += 1
        worst = max((c["status"] for c in self.components.values()), key=STATUS_RANK.get, default="ok")
        service.write_json(os.path.join(self.rdir, service.HEARTBEAT), {
            "pid": os.getpid(), "run": os.environ.get("VIGIEFS_RUN_ID"), "seq": self.seq, "ts": time.time(),
            "status": worst,
            "components": {k: v["status"] for k, v in self.components.items()}})

    # --- démarrage : incident précédent et tâches interrompues ---
    def _report_startup(self):
        raw = os.environ.get("VIGIEFS_LAST_INCIDENT")
        if raw:
            try:
                inc = json.loads(raw)
            except ValueError:
                inc = {"kind": "inconnu", "detail": raw}
            label = service.INCIDENT_LABEL.get(inc.get("kind"), inc.get("kind") or "incident")
            if inc.get("kind") == "interruption":
                label = "arrêt brutal du service ou du serveur"
            self.alerts.raise_alert(
                "service:incident", "warning", "service", "VigieFS a redémarré automatiquement",
                f"Le service a été relancé après un incident : {label}"
                f"{' (' + inc['detail'] + ')' if inc.get('detail') and inc.get('kind') != 'interruption' else ''}. "
                "La supervision a repris ; consultez Service & disponibilité pour le détail.",
                details=inc)
        interrupted = getattr(self.db, "interrupted_jobs", [])
        if interrupted:
            risky = [j for j in interrupted if j["type"] not in RESTARTABLE_JOBS]
            self.alerts.raise_alert(
                "service:jobs", "warning" if risky else "info", "service",
                "Tâches interrompues par un redémarrage",
                f"{len(interrupted)} tâche(s) en cours lors de l'arrêt précédent ont été interrompues"
                + (f", dont {len(risky)} à vérifier puis relancer (modification de fichiers ou de droits)" if risky else "")
                + ". Les analyses planifiées repartiront automatiquement.",
                details={"jobs": [{"id": j["id"], "type": j["type"], "title": j["title"]} for j in interrupted[:50]]})

    # --- contrôles ---
    def _set(self, name, status, detail="", **extra):
        prev = self.components.get(name, {})
        comp = {"name": name, "label": COMPONENT_LABEL.get(name, name), "status": status, "detail": detail,
                "repairs": prev.get("repairs", 0), "last_repair": prev.get("last_repair"),
                "since": prev.get("since") if prev.get("status") == status else time.time(), **extra}
        self.components[name] = comp
        return comp

    def _repaired(self, name, detail):
        comp = self.components.setdefault(name, {"repairs": 0})
        comp["repairs"] = comp.get("repairs", 0) + 1
        comp["last_repair"] = time.time()
        service.journal(self.rdir, "reparation", component=name, detail=detail)
        log.warning("Auto-réparation — %s : %s", COMPONENT_LABEL.get(name, name), detail)
        self.alerts.raise_alert(
            f"service:repair:{name}", "warning", "service", f"Composant relancé : {COMPONENT_LABEL.get(name, name)}",
            f"{detail}. VigieFS l'a relancé automatiquement, sans interruption du service.",
            details={"component": name, "repairs": comp["repairs"]})

    def check(self):
        cfg = merged_setting(self.db, "service")
        now = time.time()
        self.last_check_at = now
        self._check_scheduler(cfg, now)
        self._check_monitor()
        self._check_events()
        self._check_database(cfg, now)
        self._check_disk(cfg)
        self._check_memory(cfg, now)
        jobs = len(self.engine.jobs.running) + len(self.engine.running)
        self._set("jobs", "ok", f"{jobs} en cours" if jobs else "aucune en cours", running=jobs)
        # Les alertes de réparation se résolvent après une heure de bon fonctionnement.
        for name, comp in self.components.items():
            if comp["status"] == "ok" and comp.get("last_repair") and now - comp["last_repair"] > 3600:
                self.alerts.resolve(f"service:repair:{name}")
        if now - self.started_at > 86400:
            self.alerts.resolve("service:incident")

    def _check_scheduler(self, cfg, now):
        eng = self.engine
        stall = int(cfg.get("stall_minutes", 15)) * 60
        if eng.stopping:
            return
        if not eng.scheduler_alive():
            eng.restart_scheduler()
            self._repaired("scheduler", "Le planificateur s'était arrêté")
            self._set("scheduler", "repaired", "relancé")
            return
        age = now - (eng.last_tick_done_at or eng.started_at)
        if age > stall:
            minutes = int(age // 60)
            self._set("scheduler", "error", f"aucun cycle terminé depuis {minutes} min", age=age)
            self.alerts.raise_alert(
                "service:scheduler", "critical", "service", "Planificateur bloqué",
                f"Le planificateur n'a terminé aucun cycle depuis {minutes} min (accès réseau bloqué ?).",
                details={"age_sec": round(age)})
            if age > 2 * stall:
                self.request_restart(f"planificateur bloqué depuis {minutes} min")
            return
        self.alerts.resolve("service:scheduler")
        prev = self.components.get("scheduler", {})
        self._set("scheduler", "repaired" if prev.get("status") == "repaired" and now - (prev.get("last_repair") or 0) < 300
                  else "ok", f"dernier cycle il y a {int(age)} s", age=age)

    def _check_monitor(self):
        res = self.engine.monitor.health_check(repair=True)
        for item in res["repaired"]:
            self._repaired("monitor", item)
        if res["errors"]:
            self._set("monitor", "degraded", " ; ".join(res["errors"][:3]), watches=res["watches"])
        else:
            self._set("monitor", "repaired" if res["repaired"] else "ok",
                      f"{res['watches']} dossier(s) surveillé(s)", watches=res["watches"])

    def _check_events(self):
        backlog = self.engine.monitor.queue.qsize()
        status = "error" if backlog > 200000 else "degraded" if backlog > 50000 else "ok"
        self._set("events", status, f"{backlog} événement(s) en attente d'écriture", backlog=backlog)

    def _check_database(self, cfg, now):
        try:
            started = time.monotonic()
            self.db.query_one("SELECT 1 AS ok")
            latency = (time.monotonic() - started) * 1000
        except sqlite3.Error as exc:
            self._set("database", "error", f"inaccessible : {exc}")
            return
        info = self.maintenance_info
        detail = f"réponse en {latency:.0f} ms"
        if info.get("backup_at"):
            detail += f" · sauvegarde {time.strftime('%d/%m %H:%M', time.localtime(info['backup_at']))}"
        status = "error" if info.get("integrity") not in (None, "ok") else "degraded" if latency > 5000 else "ok"
        if info.get("integrity") not in (None, "ok"):
            detail = f"contrôle d'intégrité en échec : {info['integrity']}"
        self._set("database", status, detail, latency_ms=round(latency), **{k: info.get(k) for k in
                                                                            ("backup_at", "backup_file", "integrity", "checked_at")})
        if now - self.last_checkpoint > 3600:
            self.last_checkpoint = now
            try:
                self.db.checkpoint()
            except sqlite3.Error as exc:
                log.warning("Point de contrôle WAL impossible : %s", exc)
        hour = int(cfg.get("db_backup_hour", 2))
        today = time.strftime("%Y-%m-%d")
        done_today = time.strftime("%Y-%m-%d", time.localtime(info.get("backup_at") or 0)) == today
        due = (time.localtime(now).tm_hour >= hour and not done_today) or not info.get("backup_at")
        if due and self.last_daily != today and not (self._maint_thread and self._maint_thread.is_alive()):
            self.last_daily = today
            self._maint_thread = threading.Thread(target=self.maintain_database, name="vigiefs-maintenance-base",
                                                  daemon=True)
            self._maint_thread.start()

    def maintain_database(self):
        """Contrôle d'intégrité rapide, sauvegarde en ligne cohérente, optimisation (une fois par jour)."""
        cfg = merged_setting(self.db, "service")
        info = dict(self.maintenance_info)
        try:
            result = self.db.quick_check()
            info.update(integrity=result, checked_at=time.time())
            if result != "ok":
                self.alerts.raise_alert(
                    "service:db", "critical", "service", "Base VigieFS endommagée",
                    f"Le contrôle d'intégrité de la base a échoué ({result}). Restaurez la dernière sauvegarde "
                    "saine avec « python -m vigiefs restore-db ».", details={"result": result})
            else:
                self.alerts.resolve("service:db")
                folder = os.path.join(self.data_dir, "sauvegardes-base")
                path = self.db.backup_to(folder, keep=int(cfg.get("db_backups_keep", 7)))
                info.update(backup_at=time.time(), backup_file=os.path.basename(path))
            self.db.optimize()
        except (sqlite3.Error, OSError) as exc:
            log.exception("Maintenance de la base en échec")
            info.update(error=str(exc), checked_at=time.time())
        self.maintenance_info = info
        try:
            self.db.set_setting("db_maintenance", info)
        except sqlite3.Error:
            pass
        return info

    def _check_disk(self, cfg):
        try:
            du = shutil.disk_usage(self.data_dir)
        except OSError as exc:
            self._set("disk", "error", str(exc))
            return
        free_mb = du.free / 1048576
        min_mb = int(cfg.get("min_free_mb", 1024))
        status = "error" if free_mb < min_mb else "degraded" if free_mb < 2 * min_mb else "ok"
        self._set("disk", status, f"{free_mb / 1024:.1f} Go libres".replace(".", ","), free=du.free, total=du.total)
        if status == "ok":
            self.alerts.resolve("service:disk")
        else:
            self.alerts.raise_alert(
                "service:disk", "critical" if status == "error" else "warning", "service",
                "Espace disque insuffisant pour VigieFS",
                f"Le volume des données VigieFS ({self.data_dir}) n'a plus que {free_mb:.0f} Mo libres : "
                "la base, les journaux et les sauvegardes de droits risquent de ne plus pouvoir être écrits.",
                details={"free": du.free, "total": du.total})

    def _check_memory(self, cfg, now):
        mb = rss_mb()
        limit = int(cfg.get("max_memory_mb", 4096) or 0)
        if mb is None:
            self._set("memory", "ok", "mesure indisponible")
            return
        if limit and mb > limit:
            self._memory_over_since = self._memory_over_since or now
            busy = len(self.engine.jobs.running) + len(self.engine.running)
            self._set("memory", "degraded", f"{mb:.0f} Mo utilisés (limite {limit} Mo)", rss_mb=round(mb))
            # Redémarrage planifié : dès qu'aucune tâche ne tourne, ou au plus tard après une heure.
            if not busy or now - self._memory_over_since > 3600:
                self.request_restart(f"mémoire {mb:.0f} Mo au-delà de la limite de {limit} Mo")
            return
        self._memory_over_since = None
        self._set("memory", "ok", f"{mb:.0f} Mo utilisés", rss_mb=round(mb))

    def request_restart(self, reason):
        """Redémarrage propre via le superviseur (sans effet hors mode service)."""
        if self._restart_requested or not (self.control and self.control.supervised):
            return False
        self._restart_requested = True
        log.warning("Redémarrage automatique demandé : %s", reason)
        self.control.request("restart", "garde automatique", reason)
        return True

    # --- synthèse pour l'API ---
    def snapshot(self):
        worst = max((c["status"] for c in self.components.values()), key=STATUS_RANK.get, default="ok")
        return {"status": worst, "checked_at": self.last_check_at, "seq": self.seq,
                "components": [self.components[k] for k in COMPONENT_LABEL if k in self.components]}
