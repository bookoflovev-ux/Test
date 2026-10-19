"""Tâches de fond persistées : doublons, nettoyage, archivage, intégrité, restaurations."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("vigiefs.jobs")

JOB_LABELS = {
    "duplicates": "Recherche de doublons",
    "duplicates_resolve": "Suppression de doublons",
    "cleanup": "Nettoyage des fichiers inutiles",
    "archive_simulate": "Simulation d'archivage",
    "archive_run": "Archivage à froid",
    "archive_restore": "Restauration d'archives",
    "integrity": "Contrôle d'intégrité des documents",
    "repair_batch": "Réparation de documents",
    "recycle_restore": "Restauration depuis la corbeille",
    "acl_scan": "Analyse des droits d'accès",
    "matrix_apply": "Application d'une matrice de droits",
    "rights_ops": "Modification de droits",
    "sensitive": "Recherche de données sensibles",
    "acl_restore": "Restauration des droits",
    "restructure_propose": "Proposition d'architecture",
    "restructure_prepare": "Réorganisation : création des dossiers",
    "restructure_execute": "Réorganisation : déplacement vérifié",
    "restructure_rollback": "Réorganisation : retour arrière",
}


class JobCancelled(Exception):
    pass


class JobContext:
    """Passé à la fonction de travail : progression, annulation, journal."""

    def __init__(self, manager, job_id, actor):
        self.manager = manager
        self.id = job_id
        self.actor = actor
        self.cancel_event = threading.Event()
        self.state = {}
        self._last_flush = 0

    def progress(self, force=False, **values):
        self.state.update(values)
        now = time.time()
        if force or now - self._last_flush > 1:
            self._last_flush = now
            self.manager.db.execute("UPDATE jobs SET progress=? WHERE id=?",
                                    (json.dumps(self.state, default=str), self.id))

    def check(self):
        if self.cancel_event.is_set():
            raise JobCancelled()

    @property
    def cancelled(self):
        return self.cancel_event.is_set()


class JobManager:
    def __init__(self, db, workers=2):
        self.db = db
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vigiefs-job")
        self.running = {}
        self._lock = threading.Lock()

    def submit(self, job_type, fn, actor, share=None, client_id=None, params=None, title=None, exclusive=True):
        """Planifie `fn(ctx)` ; renvoie l'id de tâche. `exclusive` : une seule tâche de ce type par dossier."""
        share_id = share["id"] if share else None
        if exclusive and share_id:
            dup = self.db.query_one(
                "SELECT id FROM jobs WHERE type=? AND share_id=? AND status IN ('queued','running')",
                (job_type, share_id))
            if dup:
                return dup["id"]
        label = title or JOB_LABELS.get(job_type, job_type)
        if share:
            label += f" — {share['name']}"
        job_id = self.db.execute(
            "INSERT INTO jobs(type, title, share_id, client_id, status, params, actor, created_at) "
            "VALUES(?,?,?,?, 'queued', ?, ?, ?)",
            (job_type, label, share_id, client_id or (share["client_id"] if share else None),
             json.dumps(params or {}, default=str), actor, time.time()))
        ctx = JobContext(self, job_id, actor)
        with self._lock:
            self.running[job_id] = ctx
        self.executor.submit(self._run, job_id, fn, ctx)
        return job_id

    def _run(self, job_id, fn, ctx):
        self.db.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (time.time(), job_id))
        try:
            ctx.check()
            result = fn(ctx)
            self.db.execute(
                "UPDATE jobs SET status='done', finished_at=?, result=?, progress=? WHERE id=?",
                (time.time(), json.dumps(result, default=str), json.dumps(ctx.state, default=str), job_id))
        except JobCancelled:
            self.db.execute("UPDATE jobs SET status='cancelled', finished_at=?, progress=? WHERE id=?",
                            (time.time(), json.dumps(ctx.state, default=str), job_id))
        except Exception as exc:  # noqa: BLE001
            log.exception("Tâche %s en échec", job_id)
            self.db.execute("UPDATE jobs SET status='failed', finished_at=?, error=?, progress=? WHERE id=?",
                            (time.time(), str(exc), json.dumps(ctx.state, default=str), job_id))
        finally:
            with self._lock:
                self.running.pop(job_id, None)

    def cancel(self, job_id):
        ctx = self.running.get(job_id)
        if ctx:
            ctx.cancel_event.set()
            return True
        return False

    def get(self, job_id, with_result=True):
        row = self.db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row:
            row["params"] = json.loads(row["params"] or "{}")
            row["progress"] = json.loads(row["progress"] or "{}")
            row["result"] = json.loads(row["result"]) if with_result and row["result"] else None
        return row

    def latest(self, job_type, share_id, status="done"):
        row = self.db.query_one("SELECT id FROM jobs WHERE type=? AND share_id=? AND status=? "
                                "ORDER BY id DESC LIMIT 1", (job_type, share_id, status))
        return self.get(row["id"]) if row else None

    def shutdown(self):
        for ctx in list(self.running.values()):
            ctx.cancel_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
