"""Orchestrateur : analyses planifiées, évaluation des règles proactives, rétention."""

import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import merged_setting
from .jobs import JobManager
from .monitor import Monitor
from .optimize import JunkClassifier
from .pathcheck import PathAnalyzer
from .quotas import QuotaService
from .rights import RightsService
from .storage import ArchiveService, RecycleBin, share_policy
from .rules import (days_until_full, growth_per_day, quota_state, ransomware_matchers,
                    share_screening_matcher)
from .scanner import ScanCancelled, quarantine_file, scan_tree

log = logging.getLogger("vigiefs.engine")


def fmt_num(value, digits=1):
    return f"{value:.{digits}f}".replace(".", ",")


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(n) < 1024 or unit == "To":
            return f"{fmt_num(n)} {unit}" if unit != "o" else f"{int(n)} o"
        n /= 1024


class Engine:
    def __init__(self, db, alerts, max_parallel_scans=2, monitor=None, data_dir=None):
        self.db = db
        self.alerts = alerts
        self.data_dir = data_dir or os.path.dirname(os.path.abspath(db.path))
        self.jobs = JobManager(db)
        self.recycle = RecycleBin(db, self.data_dir)
        self.archive = ArchiveService(db, self.recycle)
        self.rights = RightsService(db, alerts, self.data_dir)
        self.quotas = QuotaService(db, alerts, self.rights)
        self._last_quota_check = 0
        self.monitor = monitor or Monitor(db, alerts)
        self.executor = ThreadPoolExecutor(max_workers=max_parallel_scans, thread_name_prefix="vigiefs-scan")
        self.running = {}          # share_id -> {"started", "progress", "cancel"}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_purge = 0
        self.started_at = time.time()

    # --- cycle de vie ---
    def start(self):
        self.monitor.start()
        for share in self.shares():
            self.monitor.watch(share)
        self._thread = threading.Thread(target=self._loop, name="vigiefs-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for job in list(self.running.values()):
            job["cancel"].set()
        self.monitor.stop()
        self.jobs.shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def shares(self):
        return self.db.query("SELECT * FROM shares ORDER BY id")

    def share(self, share_id):
        return self.db.query_one("SELECT * FROM shares WHERE id = ?", (share_id,))

    # --- planificateur ---
    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Erreur du planificateur")
            tick = int(merged_setting(self.db, "general").get("scheduler_tick_sec", 30))
            self._stop.wait(max(5, tick))

    def tick(self):
        now = time.time()
        for share in self.shares():
            available = self.check_availability(share)
            if available and not self.monitor.is_active(share["id"]) and share["monitor_enabled"]:
                self.monitor.watch(share)
            interval = (share["scan_interval_min"] or 0) * 60
            if available and interval > 0 and (share["last_scan_at"] or 0) + interval <= now:
                self.request_scan(share["id"])
            if available and share.get("archive_enabled") and self.archive.settings().get("root"):
                every = (share.get("archive_interval_days") or 30) * 86400
                if (share.get("last_archive_at") or 0) + every <= now:
                    self.start_archive(share, share_policy(share), "planificateur")
        rights_cfg = merged_setting(self.db, "rights")
        for share in self.shares():
            every_h = share.get("acl_interval_h") or 0
            if rights_cfg.get("scheduled_scans", True) and every_h and os.path.isdir(share["path"]) \
                    and (share.get("last_acl_at") or 0) + every_h * 3600 <= now:
                self.db.execute("UPDATE shares SET last_acl_at=? WHERE id=?", (now, share["id"]))
                self.start_acl_scan(share, "planificateur")
        qcfg = merged_setting(self.db, "quotas")
        if now - self._last_quota_check >= int(qcfg.get("check_interval_min", 15)) * 60:
            self._last_quota_check = now
            self.executor.submit(self._safe, self.quotas.check_all, True)
        if now - self._last_purge > 3600:
            self.purge()
            self._last_purge = now

    def check_availability(self, share):
        """Vérifie l'accès au dossier et l'espace libre du volume ; lève/résout les alertes."""
        fp = f"path:{share['id']}"
        if not os.path.isdir(share["path"]):
            self.alerts.raise_alert(
                fp, "critical", "path", "Dossier inaccessible",
                f"Le dossier « {share['name']} » ({share['path']}) est introuvable ou inaccessible. "
                "Vérifiez le volume, le partage réseau et les droits du compte de service.",
                share=share)
            return False
        self.alerts.resolve(fp)
        warn_pct = share.get("disk_free_warn_pct") or 0
        if warn_pct > 0:
            try:
                du = shutil.disk_usage(share["path"])
                free_pct = du.free * 100.0 / du.total if du.total else 100
                fpd = f"disk:{share['id']}"
                if free_pct < warn_pct:
                    sev = "critical" if free_pct < warn_pct / 2 else "warning"
                    self.alerts.raise_alert(
                        fpd, sev, "disk", "Espace disque faible",
                        f"Le volume hébergeant « {share['name']} » n'a plus que {fmt_num(free_pct)} % "
                        f"d'espace libre ({fmt_bytes(du.free)} sur {fmt_bytes(du.total)}).",
                        share=share, details={"free": du.free, "total": du.total, "free_pct": round(free_pct, 2)})
                else:
                    self.alerts.resolve(fpd)
            except OSError:
                pass
        return True

    # --- analyses ---
    def request_scan(self, share_id):
        with self._lock:
            if share_id in self.running:
                return False
            job = {"started": time.time(), "progress": {"file_count": 0, "total_size": 0},
                   "cancel": threading.Event()}
            self.running[share_id] = job
        self.executor.submit(self._run_scan, share_id, job)
        return True

    def cancel_scan(self, share_id):
        job = self.running.get(share_id)
        if job:
            job["cancel"].set()
            return True
        return False

    def _run_scan(self, share_id, job):
        try:
            share = self.share(share_id)
            if share:
                self.scan_share(share, job)
        except Exception:  # noqa: BLE001
            log.exception("Analyse en échec (dossier %s)", share_id)
        finally:
            with self._lock:
                self.running.pop(share_id, None)

    def scan_share(self, share, job=None):
        job = job or {"progress": {}, "cancel": threading.Event()}
        started = time.time()
        scan_id = self.db.execute(
            "INSERT INTO scans(share_id, started_at, status) VALUES(?, ?, 'running')", (share["id"], started))
        rs_ext, rs_notes = ransomware_matchers(merged_setting(self.db, "ransomware"))
        screening_cfg = merged_setting(self.db, "screening")
        try:
            if not os.path.isdir(share["path"]):
                raise FileNotFoundError(f"Dossier introuvable : {share['path']}")
            report = scan_tree(
                share["path"], stale_days=share["stale_days"] or 0,
                screening=share_screening_matcher(share, screening_cfg),
                ransom_ext=rs_ext, ransom_notes=rs_notes,
                progress=job["progress"], cancel=job["cancel"],
                junk=JunkClassifier.from_settings(self.db), skip_roots=self.skip_roots(),
                paths=PathAnalyzer(share.get("access_path") or share["path"], merged_setting(self.db, "paths")))
        except ScanCancelled:
            self.db.execute("UPDATE scans SET status='cancelled', finished_at=? WHERE id=?", (time.time(), scan_id))
            self.db.execute("UPDATE shares SET last_scan_status='annulée' WHERE id=?", (share["id"],))
            return None
        except Exception as exc:  # noqa: BLE001
            self.db.execute("UPDATE scans SET status='failed', finished_at=?, error=? WHERE id=?",
                            (time.time(), str(exc), scan_id))
            self.db.execute("UPDATE shares SET last_scan_at=?, last_scan_status='échec' WHERE id=?",
                            (time.time(), share["id"]))
            self.alerts.raise_alert(f"scan:{share['id']}", "warning", "scan", "Échec de l'analyse",
                                    f"L'analyse de « {share['name']} » a échoué : {exc}", share=share)
            return None

        finished = time.time()
        self.store_folder_stats(share["id"], report)
        self.db.execute(
            "UPDATE scans SET status='done', finished_at=?, total_size=?, file_count=?, dir_count=?, "
            "error_count=?, stale_count=?, stale_size=?, report=? WHERE id=?",
            (finished, report["total_size"], report["file_count"], report["dir_count"], report["error_count"],
             report["stale_count"], report["stale_size"], json.dumps(report), scan_id))
        self.db.execute(
            "UPDATE shares SET last_scan_at=?, last_size=?, last_file_count=?, last_scan_status='ok' WHERE id=?",
            (finished, report["total_size"], report["file_count"], share["id"]))
        self.alerts.resolve(f"scan:{share['id']}")
        self.evaluate(share, report, screening_cfg)
        for q in self.quotas.list(share["id"]):
            self._safe(self.quotas.check, self.quotas.get(q["id"]))
        return scan_id

    def store_folder_stats(self, share_id, report):
        stats = report.pop("_folder_stats", None)
        if stats is None:
            return
        self.db.execute("DELETE FROM folder_stats WHERE share_id=?", (share_id,))
        self.db.executemany(
            "INSERT INTO folder_stats(share_id, rel_path, depth, size, files, subdirs, direct_items, max_path_len, "
            "newest_mtime) VALUES(?,?,?,?,?,?,?,?,?)",
            [(share_id, s["rel_path"], s["depth"], s["size"], s["files"], s["subdirs"], s["direct_items"],
              s["max_path_len"], s["newest_mtime"]) for s in stats])

    def evaluate(self, share, report, screening_cfg):
        """Règles proactives appliquées après chaque analyse."""
        sid, name, size = share["id"], share["name"], report["total_size"]
        self.evaluate_paths(share, report.get("paths"))

        level, pct = quota_state(share, size)
        if level:
            self.alerts.raise_alert(
                f"quota:{sid}", level, "quota",
                "Quota dépassé" if pct >= 100 else "Seuil de quota atteint",
                f"« {name} » utilise {fmt_bytes(size)} sur {fmt_bytes(share['quota_bytes'])} ({fmt_num(pct)} %).",
                share=share, details={"size": size, "quota": share["quota_bytes"], "pct": round(pct, 2)})
        else:
            self.alerts.resolve(f"quota:{sid}")

        forecast = merged_setting(self.db, "forecast")
        if forecast.get("enabled") and share["quota_bytes"]:
            days = self.forecast_days(share, size)
            fp = f"forecast:{sid}"
            if days is not None and days <= forecast.get("warn_days", 30) and not level:
                sev = "critical" if days <= forecast.get("crit_days", 7) else "warning"
                self.alerts.raise_alert(
                    fp, sev, "forecast", "Saturation du quota prévue",
                    f"Au rythme actuel, « {name} » atteindra son quota dans environ {days:.0f} jour(s).",
                    share=share, details={"days": round(days, 1)})
            else:
                self.alerts.resolve(fp)

        screened = report["screened"]
        if screened:
            moved = 0
            if share["screening_action"] == "quarantine" and screening_cfg.get("quarantine_dir"):
                tag = f"{sid}-{name}"
                for f in screened:
                    try:
                        quarantine_file(share["path"], f["path"], screening_cfg["quarantine_dir"], tag)
                        self.db.audit("système", "quarantaine", f["path"], f"dossier {name}")
                        moved += 1
                    except OSError as exc:
                        log.warning("Quarantaine impossible pour %s : %s", f["path"], exc)
            suffix = f" — {moved} mis en quarantaine" if moved else ""
            self.alerts.raise_alert(
                f"screening:{sid}", "warning", "screening", "Fichiers interdits présents",
                f"{len(screened)} fichier(s) enfreignent la règle de filtrage sur « {name} »{suffix}.",
                share=share, details={"files": [f["path"] for f in screened[:100]], "quarantined": moved})
        else:
            self.alerts.resolve(f"screening:{sid}")

        if report["ransomware"]:
            self.alerts.raise_alert(
                f"ransomware-scan:{sid}", "critical", "ransomware", "Traces de rançongiciel détectées",
                f"{len(report['ransomware'])} fichier(s) suspects (extensions chiffrées ou notes de rançon) "
                f"trouvés dans « {name} ».",
                share=share, details={"files": [f["path"] for f in report["ransomware"][:100]]})
        else:
            self.alerts.resolve(f"ransomware-scan:{sid}")

        if report["error_count"]:
            self.alerts.raise_alert(
                f"access:{sid}", "info", "scan", "Éléments illisibles pendant l'analyse",
                f"{report['error_count']} élément(s) de « {name} » n'ont pas pu être lus "
                "(droits insuffisants du compte de service ?).",
                share=share, details={"errors": report["errors"]}, notify=False)
        else:
            self.alerts.resolve(f"access:{sid}")

    def evaluate_paths(self, share, paths):
        if not paths:
            return
        sid, name = share["id"], share["name"]
        r = paths["rules"]
        fp = f"longpath:{sid}"
        if paths["over_crit"] or paths["over_warn"]:
            sev = "critical" if paths["over_crit"] else "warning"
            self.alerts.raise_alert(
                fp, sev, "paths", "Chemins trop longs",
                f"« {name} » contient {paths['over_crit']} chemin(s) au-delà de {r['crit_len']} caractères (limite "
                f"Windows MAX_PATH) et {paths['over_warn']} au-delà du seuil d'alerte de {r['warn_len']}. "
                "Ces fichiers peuvent devenir impossibles à ouvrir, copier ou sauvegarder.",
                share=share, details={"longest": paths["longest"][:50], "over_crit": paths["over_crit"],
                                      "over_warn": paths["over_warn"]})
        else:
            self.alerts.resolve(fp)
        naming = sum(paths["naming"]["counts"].values())
        fpn = f"naming:{sid}"
        if naming:
            self.alerts.raise_alert(
                fpn, "info", "paths", "Noms de fichiers non conformes",
                f"{naming} nom(s) incompatibles avec Windows ou OneDrive/SharePoint sur « {name} » "
                "(caractères interdits, noms réservés, espaces en fin de nom…).",
                share=share, details={"counts": paths["naming"]["counts"], "samples": paths["naming"]["samples"][:50]},
                notify=False)
        else:
            self.alerts.resolve(fpn)

    def forecast_days(self, share, size=None, lookback_days=30):
        since = time.time() - lookback_days * 86400
        pts = [(r["finished_at"], r["total_size"]) for r in self.db.query(
            "SELECT finished_at, total_size FROM scans WHERE share_id=? AND status='done' AND finished_at>=? "
            "ORDER BY finished_at", (share["id"], since))]
        slope = growth_per_day(pts)
        return days_until_full(share, size if size is not None else share["last_size"], slope)

    def growth(self, share, lookback_days=30):
        since = time.time() - lookback_days * 86400
        pts = [(r["finished_at"], r["total_size"]) for r in self.db.query(
            "SELECT finished_at, total_size FROM scans WHERE share_id=? AND status='done' AND finished_at>=? "
            "ORDER BY finished_at", (share["id"], since))]
        return growth_per_day(pts)

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("Tâche de fond en échec")
            return None

    def start_acl_scan(self, share, actor):
        return self.jobs.submit("acl_scan", lambda ctx: self.acl_scan(share, ctx), actor, share=share)

    def acl_scan(self, share, ctx=None):
        from . import matrix as mx
        res = self.rights.scan(share, ctx)
        applied = self.db.query_one("SELECT * FROM matrices WHERE share_id=? AND status='applied' "
                                    "ORDER BY applied_at DESC LIMIT 1", (share["id"],))
        fp = f"matrix-drift:{share['id']}"
        if applied:
            snap = self.rights.latest(share["id"])
            m = json.loads(applied["data"])
            m["options"] = {**m.get("options", {}), "reset_below": False, "remove_denies": False}
            gaps = mx.plan(m, snap)["ops"]
            res["matrix_gaps"] = len(gaps)
            if gaps:
                self.alerts.raise_alert(
                    fp, "warning", "permissions", "Droits différents de la matrice",
                    f"{len(gaps)} écart(s) entre les droits réels de « {share['name']} » et la matrice appliquée "
                    f"« {applied['name']} ».", share=share,
                    details={"matrix_id": applied["id"], "gaps": gaps[:50]})
            else:
                self.alerts.resolve(fp)
        return res

    def skip_roots(self):
        return [self.recycle.root(), self.archive.settings().get("root")]

    def start_archive(self, share, policy, actor):
        self.db.execute("UPDATE shares SET last_archive_at=? WHERE id=?", (time.time(), share["id"]))
        return self.jobs.submit(
            "archive_run", lambda ctx: self.archive.run(share, policy, ctx, actor, ctx.id),
            actor, share=share, params={"policy": policy})

    # --- rétention ---
    def purge(self):
        ret = merged_setting(self.db, "retention")
        now = time.time()
        self.db.execute("DELETE FROM events WHERE ts < ?", (now - ret["events_days"] * 86400,))
        self.db.execute("DELETE FROM alerts WHERE status='resolved' AND resolved_at < ?",
                        (now - ret["resolved_alerts_days"] * 86400,))
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self.db.execute("DELETE FROM jobs WHERE finished_at < ?", (now - ret["events_days"] * 86400,))
        self.recycle.purge_expired()
        keep = int(ret["scans_per_share"])
        for share in self.shares():
            self.db.execute(
                "DELETE FROM scans WHERE share_id=? AND id NOT IN "
                "(SELECT id FROM scans WHERE share_id=? ORDER BY started_at DESC LIMIT ?)",
                (share["id"], share["id"], keep))
