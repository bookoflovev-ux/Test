"""Surveillance temps réel (watchdog) : journal d'activité, filtrage, détection rançongiciel."""

import logging
import os
import queue
import threading
import time
from collections import defaultdict, deque

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import merged_setting
from .pathcheck import user_path_length
from .rules import ransomware_matchers, relpath, share_screening_matcher
from .scanner import quarantine_file

log = logging.getLogger("vigiefs.monitor")

# Fichiers temporaires bureautiques ignorés dans le journal (bruit).
IGNORED_PREFIXES = ("~$", ".~lock")
IGNORED_SUFFIXES = (".tmp", ".crdownload", ".partial")


class _Handler(FileSystemEventHandler):
    def __init__(self, share_id, sink):
        self.share_id = share_id
        self.sink = sink

    def on_any_event(self, event):
        kind = event.event_type
        if kind not in ("created", "modified", "deleted", "moved"):
            return
        if event.is_directory and kind == "modified":
            return
        self.sink.put((self.share_id, time.time(), kind, event.src_path,
                       getattr(event, "dest_path", None) or None, event.is_directory))


class Monitor:
    def __init__(self, db, alerts, observer_factory=Observer):
        self.db = db
        self.alerts = alerts
        self.queue = queue.Queue()
        self._observer_factory = observer_factory
        self._observer = None
        self._watches = {}         # share_id -> watch
        self.status = {}           # share_id -> texte d'état
        self._shares = {}          # cache share_id -> dict
        self._bursts = defaultdict(deque)
        self._stop = threading.Event()
        self._writer = None
        self._lock = threading.Lock()
        self.events_total = 0
        self.quiet = set()         # partages en réorganisation : pas de détection de rafale

    # --- cycle de vie ---
    def start(self):
        self._observer = self._observer_factory()
        self._observer.daemon = True
        self._observer.start()
        self._writer = threading.Thread(target=self._writer_loop, name="vigiefs-events", daemon=True)
        self._writer.start()

    def stop(self):
        self._stop.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        if self._writer:
            self._writer.join(timeout=5)

    def watch(self, share):
        """(Re)programme la surveillance d'un dossier selon sa configuration."""
        with self._lock:
            self._unwatch_locked(share["id"])
            self._shares[share["id"]] = share
            if not share.get("monitor_enabled"):
                self.status[share["id"]] = "désactivée"
                return
            if not os.path.isdir(share["path"]):
                self.status[share["id"]] = "erreur : dossier inaccessible"
                return
            try:
                handler = _Handler(share["id"], self.queue)
                self._watches[share["id"]] = self._observer.schedule(handler, share["path"], recursive=True)
                self.status[share["id"]] = "active"
            except Exception as exc:  # noqa: BLE001
                self.status[share["id"]] = f"erreur : {exc}"
                log.warning("Surveillance impossible pour %s : %s", share["path"], exc)

    def unwatch(self, share_id):
        with self._lock:
            self._unwatch_locked(share_id)
            self._shares.pop(share_id, None)
            self.status.pop(share_id, None)

    def _unwatch_locked(self, share_id):
        w = self._watches.pop(share_id, None)
        if w is not None:
            try:
                self._observer.unschedule(w)
            except Exception:  # noqa: BLE001
                pass

    def is_active(self, share_id):
        return self.status.get(share_id) == "active"

    # --- auto-réparation (appelée par la garde toutes les 10 s) ---
    def health_check(self, repair=True):
        """Détecte un observateur, un écrivain ou une surveillance de dossier arrêtés et les relance.

        Aucun accès disque ici (un partage réseau bloqué figerait la garde) : une surveillance de
        dossier interrompue est marquée comme telle, puis reprogrammée par le planificateur après
        une sonde d'accès à délai limité.
        """
        repaired, errors = [], []
        if self._stop.is_set() or self._observer is None:
            return {"repaired": repaired, "errors": errors, "watches": 0}
        if not self._observer.is_alive():
            if repair:
                with self._lock:
                    old, self._observer = self._observer, self._observer_factory()
                    self._observer.daemon = True
                    self._observer.start()
                    for sid in list(self._watches):
                        self._watches.pop(sid, None)
                        self.status[sid] = "interrompue"
                try:
                    old.stop()
                except Exception:  # noqa: BLE001
                    pass
                repaired.append("L'observateur de fichiers s'était arrêté (surveillance reprogrammée)")
            else:
                errors.append("observateur de fichiers arrêté")
        if self._writer is None or not self._writer.is_alive():
            if repair:
                self._writer = threading.Thread(target=self._writer_loop, name="vigiefs-events", daemon=True)
                self._writer.start()
                repaired.append("L'écriture du journal d'activité s'était arrêtée")
            else:
                errors.append("écriture du journal d'activité arrêtée")
        try:
            alive = {em.watch: em.is_alive() for em in list(self._observer.emitters)}
        except Exception:  # noqa: BLE001
            alive = None
        with self._lock:
            for sid, w in list(self._watches.items()):
                if alive is not None and not alive.get(w, False):
                    name = self._shares.get(sid, {}).get("name", sid)
                    if repair:
                        self._unwatch_locked(sid)
                        self.status[sid] = "interrompue"
                        repaired.append(f"Surveillance de « {name} » interrompue (reprise au prochain cycle)")
                    else:
                        errors.append(f"surveillance de « {name} » interrompue")
            for sid, st in self.status.items():
                if st.startswith("erreur"):
                    errors.append(f"{self._shares.get(sid, {}).get('name', sid)} : {st}")
            watches = len(self._watches)
        return {"repaired": repaired, "errors": errors, "watches": watches}

    # --- traitement des événements ---
    def _writer_loop(self):
        while not self._stop.is_set():
            batch = []
            try:
                batch.append(self.queue.get(timeout=1))
                while len(batch) < 2000:
                    batch.append(self.queue.get_nowait())
            except queue.Empty:
                pass
            if batch:
                try:
                    self.process_batch(batch)
                except Exception:  # noqa: BLE001
                    log.exception("Erreur de traitement des événements")

    def process_batch(self, batch):
        rs = merged_setting(self.db, "ransomware")
        screening_cfg = merged_setting(self.db, "screening")
        rs_ext, rs_notes = ransomware_matchers(rs)
        prules = merged_setting(self.db, "paths")
        rows = []
        too_long = defaultdict(list)
        screened = defaultdict(list)
        ransom = defaultdict(list)
        touched = defaultdict(int)

        for share_id, ts, kind, src, dest, is_dir in batch:
            share = self._shares.get(share_id)
            if not share:
                continue
            target = dest or src
            name = os.path.basename(target)
            if name.startswith(IGNORED_PREFIXES) or name.lower().endswith(IGNORED_SUFFIXES):
                continue
            size = None
            if not is_dir and kind in ("created", "modified", "moved"):
                try:
                    size = os.path.getsize(target)
                except OSError:
                    pass
            root = share["path"]
            rows.append((share_id, ts, ("dossier_" if is_dir else "") + kind, relpath(root, src),
                         relpath(root, dest) if dest else None, size))
            if kind in ("created", "moved") and prules.get("realtime_alert", True):
                rel_t = relpath(root, target)
                n = user_path_length(share.get("access_path") or root, rel_t)
                if n > prules["crit_len"] or len(name) > prules["name_max"]:
                    too_long[share_id].append({"path": rel_t, "length": n})
            if is_dir:
                continue
            if kind in ("modified", "moved", "deleted"):
                touched[share_id] += 1
            if kind in ("created", "moved"):
                if share_screening_matcher(share, screening_cfg).match(name):
                    screened[share_id].append(target)
                if rs_ext.match(name) or rs_notes.match(name):
                    ransom[share_id].append(relpath(root, target))

        if rows:
            self.db.executemany(
                "INSERT INTO events(share_id, ts, type, path, dest_path, size) VALUES(?,?,?,?,?,?)", rows)
            self.events_total += len(rows)

        for share_id, files in ransom.items():
            share = self._shares[share_id]
            self.alerts.raise_alert(
                f"ransomware:{share_id}", "critical", "ransomware",
                "Fichiers suspects de rançongiciel détectés",
                f"{len(files)} fichier(s) portant une extension ou un nom de note de rançon connus "
                f"sont apparus dans « {share['name']} ». Isolez le poste source et vérifiez les sauvegardes Datto.",
                share=share, details={"files": files[:100]})

        if rs.get("enabled", True):
            now = time.time()
            window = float(rs.get("burst_window_sec", 60))
            threshold = int(rs.get("burst_threshold", 200))
            for share_id, n in touched.items():
                if share_id in self.quiet:
                    continue          # déplacements massifs volontaires (réorganisation en cours)
                dq = self._bursts[share_id]
                dq.extend([now] * n)
                while dq and dq[0] < now - window:
                    dq.popleft()
                if len(dq) >= threshold:
                    share = self._shares[share_id]
                    self.alerts.raise_alert(
                        f"burst:{share_id}", "critical", "ransomware",
                        "Activité massive de modification de fichiers",
                        f"{len(dq)} modifications / renommages / suppressions en {int(window)} s sur "
                        f"« {share['name']} » (seuil {threshold}). Comportement typique d'un chiffrement en cours.",
                        share=share, details={"events": len(dq), "window_sec": window})

        for share_id, items in too_long.items():
            share = self._shares[share_id]
            self.alerts.raise_alert(
                f"longpath-rt:{share_id}", "warning", "paths", "Chemin trop long créé",
                f"{len(items)} élément(s) créé(s) ou renommé(s) sur « {share['name']} » dépassent "
                f"{prules['crit_len']} caractères : ils risquent d'être inaccessibles (Explorateur, Office, sauvegarde).",
                share=share, details={"items": items[:100]})

        for share_id, paths in screened.items():
            share = self._shares[share_id]
            rels = [relpath(share["path"], p) for p in paths]
            moved = []
            if share.get("screening_action") == "quarantine" and screening_cfg.get("quarantine_dir"):
                tag = f"{share['id']}-{share['name']}"
                for rel in rels:
                    try:
                        moved.append(quarantine_file(share["path"], rel, screening_cfg["quarantine_dir"], tag))
                        self.db.audit("système", "quarantaine", rel, f"dossier {share['name']}")
                    except OSError as exc:
                        log.warning("Quarantaine impossible pour %s : %s", rel, exc)
            action = f" — {len(moved)} mis en quarantaine" if moved else ""
            self.alerts.raise_alert(
                f"screening-rt:{share_id}", "warning", "screening",
                "Fichier(s) interdit(s) déposé(s)",
                f"{len(rels)} fichier(s) bloqué(s) par la règle de filtrage sur « {share['name']} »{action}.",
                share=share, details={"files": rels[:100], "quarantined": len(moved)})
