"""Exécution de tâches longues en arrière-plan (scan, mises à jour, sauvegardes...)."""
import threading
import time
import traceback
import uuid

from . import journal

_jobs = {}
_lock = threading.Lock()


class Job:
    def __init__(self, name):
        self.id = uuid.uuid4().hex[:10]
        self.name = name
        self.status = "running"
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = None
        self.started = time.time()
        self.finished = None

    def update(self, progress=None, message=None):
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def to_dict(self):
        return {k: getattr(self, k) for k in ("id", "name", "status", "progress", "message", "result", "error", "started", "finished")}


def submit(name, fn, *args, **kwargs) -> Job:
    """fn(job, *args, **kwargs) — le job sert à remonter la progression."""
    job = Job(name)
    with _lock:
        _jobs[job.id] = job
        # garde les 50 derniers
        if len(_jobs) > 50:
            for old in sorted(_jobs.values(), key=lambda j: j.started)[: len(_jobs) - 50]:
                _jobs.pop(old.id, None)

    def runner():
        try:
            job.result = fn(job, *args, **kwargs)
            job.status = "done"
            job.progress = 1.0
        except Exception as e:  # remonté tel quel à l'interface
            job.status = "error"
            job.error = str(e) or e.__class__.__name__
            journal.log("jobs", f"{name} : échec — {job.error}", "error", trace=traceback.format_exc()[-800:])
        finally:
            job.finished = time.time()

    threading.Thread(target=runner, daemon=True, name=f"job-{name}").start()
    return job


def get(job_id):
    job = _jobs.get(job_id)
    return job.to_dict() if job else None


def all_jobs():
    return [j.to_dict() for j in sorted(_jobs.values(), key=lambda j: -j.started)]
