r"""Connecteur RepareDocs : pilotage du moteur de réparation RepareDocs depuis VigieFS.

Le moteur reste celui de RepareDocs (son Python et ses outils : Office, LibreOffice, Ghostscript,
relecture directe du disque, clichés instantanés). VigieFS le lance, le suit et affiche ses résultats,
avec le même protocole que RepareDocs Web :

    <RD>\runtime\python.exe <RD>\app\RepareDocs.py --web <commande> <fichier.json>   → JSON sur stdout
    info        version, droits administrateur, outils disponibles, phases du traitement
    chemins     contrôle d'un couple dossier analysé / dossier de sortie
    lancer      démarre une tâche détachée (« traitement » ou « restaurer ») décrite par tache.json ;
                le moteur publie son état dans « etat » et s'arrête proprement si le fichier « arret » existe
    resultats   liste paginée des fichiers traités (le suivi SQLite est d'abord lu directement)

Dossier de sortie produit par le moteur : 1-REPARES\, 3-ORIGINAUX_ENDOMMAGES\, RAPPORT.html, journal.txt,
etat_analyse_v2.sqlite (table « fichiers »).
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

from .config import merged_setting
from .rules import relpath, safe_join

STATUTS = ["OK", "OK_ANOMALIE", "REPARE", "REPARE_PARTIEL", "IRRECUPERABLE", "ILLISIBLE", "ENDOMMAGE",
           "PROTEGE", "VIDE", "ERREUR"]
KINDS = ["pdf", "word", "excel", "powerpoint"]
JOURNAUX = ("etat_analyse_v2.sqlite", "etat_analyse.sqlite")
OPTION_DEFAULTS = {
    "kinds": KINDS, "analyse_only": False, "use_disk": True, "use_office": True, "use_lo": True, "use_gs": True,
    "use_vss": True, "resume": True, "deep": False, "office_check_legacy": False, "allow_same_volume": False,
    "workers": 4, "disk_search": 512, "disk_workers": 3,
}
OPTION_BOUNDS = {"workers": (1, 32), "disk_search": (64, 65536), "disk_workers": (1, 8)}
HEARTBEAT_SEC = 25
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RepareDocsError(RuntimeError):
    pass


def clean_options(raw, individual=False):
    """Options du moteur validées (types, bornes) ; une copie isolée n'a ni disque ni cliché à relire."""
    opts = dict(OPTION_DEFAULTS)
    for k, v in (raw or {}).items():
        if k not in OPTION_DEFAULTS:
            continue
        if k == "kinds":
            opts[k] = [x for x in v if x in KINDS] or list(KINDS)
        elif k in OPTION_BOUNDS:
            lo, hi = OPTION_BOUNDS[k]
            try:
                opts[k] = max(lo, min(hi, int(v)))
            except (TypeError, ValueError):
                pass
        else:
            opts[k] = bool(v)
    if individual:
        opts.update(use_disk=False, use_vss=False, resume=False, analyse_only=False)
    return opts


def _within(path, root):
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


class RepareDocs:
    def __init__(self, db, data_dir, app_root=None):
        self.db = db
        self.data_dir = os.path.abspath(data_dir)
        self.app_root = app_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base = os.path.join(self.data_dir, "reparedocs")
        self._info, self._info_at = None, 0

    # ------------------------------------------------------------ moteur
    def settings(self):
        return merged_setting(self.db, "repair")

    def candidates(self):
        cfg = (self.settings().get("reparedocs_dir") or "").strip()
        dirs = [cfg, os.path.join(cfg, "RepareDocs")] if cfg else []
        dirs += [os.path.join(self.app_root, "RepareDocs"), os.path.join(os.path.dirname(self.app_root), "RepareDocs")]
        if os.name == "nt":
            dirs += ["C:\\RepareDocs", "C:\\RepareDocs\\RepareDocs",
                     os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "RepareDocs")]
        return dirs

    def engine(self):
        """(python, RepareDocs.py, dossier) ou None : RepareDocs fourni avec VigieFS, configuré ou installé."""
        for d in self.candidates():
            script = os.path.join(d, "app", "RepareDocs.py")
            if not os.path.isfile(script):
                continue
            for py in (os.path.join(d, "runtime", "python.exe"), os.path.join(d, "runtime", "bin", "python3")):
                if os.path.isfile(py):
                    return py, script, d
            if os.name != "nt":            # poste Linux / tests : interpréteur courant
                return sys.executable, script, d
        return None

    @staticmethod
    def missing_parts(eng):
        """Éléments indispensables absents d'une copie de RepareDocs (dossier copié partiellement)."""
        return [p for p in (os.path.join("app", "repare_docs", "__init__.py"),
                            os.path.join("app", "repare_docs", "pilotage.py"))
                if not os.path.isfile(os.path.join(eng[2], p))]

    def command_file(self, cmd, path, timeout=60):
        eng = self.engine()
        if not eng:
            raise RepareDocsError("RepareDocs est introuvable : indiquez son dossier dans Paramètres › Stockage & "
                                  "réparation (dossier contenant runtime\\python.exe et app\\RepareDocs.py).")
        missing = self.missing_parts(eng)
        if missing:
            raise RepareDocsError(f"RepareDocs est incomplet dans {eng[2]} : {', '.join(missing)} absent. Recopiez "
                                  "le dossier app\\repare_docs de RepareDocs (dézippez l'archive complète).")
        os.makedirs(self.base, exist_ok=True)
        try:
            res = subprocess.run([eng[0], eng[1], "--web", cmd, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 stdin=subprocess.DEVNULL, timeout=timeout + 30, cwd=os.path.dirname(eng[1]),
                                 creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise RepareDocsError(f"Le moteur RepareDocs ne répond pas (commande « {cmd} »).") from exc
        except OSError as exc:
            raise RepareDocsError(f"Impossible de lancer le moteur RepareDocs : {exc}") from exc
        if res.stderr:
            with open(os.path.join(self.base, "console.log"), "ab") as err:
                err.write(res.stderr)
        out = res.stdout.decode("utf-8", "replace").strip()
        try:
            data = json.loads(out)
        except ValueError as exc:
            lines = [ln for ln in res.stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
            detail = out[:300] or (lines[-1].strip()[:300] if lines else "aucune sortie")
            raise RepareDocsError(f"Réponse du moteur illisible : {detail} (détails : reparedocs\\console.log)") from exc
        if not isinstance(data, dict):
            raise RepareDocsError("Réponse du moteur inattendue")
        return data

    def command(self, cmd, payload, timeout=60):
        folder = os.path.join(self.base, "commandes")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"cmd_{cmd}_{os.getpid()}_{time.monotonic_ns()}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({**payload, "delai_max": timeout}, fh, ensure_ascii=False)
        try:
            return self.command_file(cmd, path, timeout)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def info(self, refresh=False):
        eng = self.engine()
        if not eng:
            return {"available": False, "candidates": [c for c in self.candidates() if c]}
        if refresh or not self._info or time.time() - self._info_at > 900:
            try:
                self._info = self.command("info", {}, 90)
            except RepareDocsError as exc:
                return {"available": False, "error": str(exc), "dir": eng[2]}
            self._info_at = time.time()
        return {**self._info, "available": True, "dir": eng[2]}

    def check_paths(self, source, output):
        return self.command("chemins", {"source": source, "output": output}, 30)

    # ------------------------------------------------------------ exécutions
    def run(self, run_id):
        row = self.db.query_one("SELECT * FROM reparedocs_runs WHERE id=?", (run_id,))
        if row:
            for k in ("options", "summary", "origin"):
                row[k] = json.loads(row[k] or "{}")
        return row

    def runs(self, limit=50, share_id=None):
        sql, args = "SELECT id FROM reparedocs_runs", []
        if share_id:
            sql += " WHERE share_id=?"
            args.append(share_id)
        return [self.state(self.run(r["id"])) for r in self.db.query(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def busy(self):
        """Exécution en masse (ou remise en place) en cours : une seule à la fois, comme RepareDocs."""
        for r in self.db.query("SELECT id FROM reparedocs_runs WHERE status='running' AND kind IN ('masse','restaurer')"):
            st = self.state(self.run(r["id"]))
            if st["status"] == "running":
                return st
        return None

    def default_output(self, label):
        root = (self.settings().get("reparedocs_output") or "").strip() or os.path.join(self.base, "sorties")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in label).strip() or "traitement"
        return os.path.join(root, f"{safe} {time.strftime('%Y-%m-%d %H%M')}")

    def start(self, kind, source, output, options, actor, share=None, origin=None, skip_roots=()):
        if kind not in ("masse", "individuel", "restaurer"):
            raise ValueError(kind)
        if kind != "restaurer":
            if not source or not os.path.isdir(source):
                raise RepareDocsError("Dossier à analyser introuvable.")
            if _within(output, source):
                raise RepareDocsError("Le dossier de sortie ne doit pas se trouver dans le dossier analysé.")
            for root in skip_roots:
                if root and _within(output, root):
                    raise RepareDocsError("Choisissez un dossier de sortie hors de la corbeille et des archives.")
        if kind != "individuel" and self.busy():
            raise RepareDocsError("Un traitement RepareDocs est déjà en cours : attendez sa fin ou arrêtez-le.")
        opts = clean_options(options, individual=kind == "individuel")
        opts.update(source=source, output=output)
        run_id = self.db.execute(
            "INSERT INTO reparedocs_runs(kind, share_id, source, output, options, status, created_at, actor, origin) "
            "VALUES(?,?,?,?,?, 'running', ?,?,?)",
            (kind, share["id"] if share else None, source, output, json.dumps(opts), time.time(), actor,
             json.dumps(origin or {})))
        task_dir = os.path.join(self.base, "taches", str(run_id))
        os.makedirs(task_dir, exist_ok=True)
        task = {"type": "restaurer" if kind == "restaurer" else "traitement", "options": opts, "output": output,
                "utilisateur": actor, "cree": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "etat": os.path.join(task_dir, "etat.json"), "arret": os.path.join(task_dir, "arret"),
                "console": os.path.join(task_dir, "console.log")}
        with open(os.path.join(task_dir, "tache.json"), "w", encoding="utf-8") as fh:
            json.dump(task, fh, ensure_ascii=False, indent=1)
        with open(task["etat"], "w", encoding="utf-8") as fh:
            json.dump({"running": True, "type": task["type"], "heartbeat": time.time(), "started": time.time(),
                       "logs": [], "output": output, "source": source}, fh)
        self.db.execute("UPDATE reparedocs_runs SET task_dir=? WHERE id=?", (task_dir, run_id))
        try:
            res = self.command_file("lancer", os.path.join(task_dir, "tache.json"), 60)
        except RepareDocsError as exc:
            res = {"ok": False, "error": str(exc)}
        if not res.get("ok"):
            self.db.execute("UPDATE reparedocs_runs SET status='failed', error=?, finished_at=? WHERE id=?",
                            (res.get("error") or "Lancement impossible", time.time(), run_id))
            raise RepareDocsError(res.get("error") or "Lancement impossible")
        self.db.audit(actor, f"RepareDocs : {kind}", source or output)
        return run_id

    def _read_state(self, task_dir):
        path = os.path.join(task_dir or "", "etat.json")
        for _ in range(3):
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                time.sleep(0.1)           # réécriture en cours par le moteur
        return {}

    def state(self, run):
        """État détaillé publié par le moteur, et mise à jour du statut de l'exécution une fois terminée."""
        if not run:
            return None
        st = self._read_state(run.get("task_dir"))
        alive = bool(st.get("running")) and time.time() - float(st.get("heartbeat") or 0) < HEARTBEAT_SEC
        status = run["status"]
        if status == "running" and not alive:
            done, error = st.get("done"), st.get("error")
            if done:
                status = "stopped" if done.get("stopped") else "done"
            else:
                status = "failed"
                error = error or ("Le moteur s'est arrêté sans prévenir (serveur redémarré, processus fermé ?). "
                                  "Relancez : le traitement reprend là où il s'est arrêté.")
            summary = (done or {}).get("summary") or (st.get("progress") or {}).get("counts") or {}
            self.db.execute("UPDATE reparedocs_runs SET status=?, finished_at=?, summary=?, error=? WHERE id=?",
                            (status, time.time(), json.dumps(summary), error, run["id"]))
            run.update(status=status, summary=summary, error=error)
        staging = None
        journal = st.get("journal")
        if alive and journal and run.get("output"):
            d = os.path.dirname(journal)
            if os.path.normcase(d.rstrip("\\/")) != os.path.normcase(run["output"].rstrip("\\/")) and os.path.isdir(d):
                staging = d
        return {**run, "alive": alive, "phase": st.get("phase"), "progress": st.get("progress"), "scan": st.get("scan"),
                "disk": st.get("disk"), "done": st.get("done"), "engine_error": st.get("error"),
                "logs": (st.get("logs") or [])[-300:], "staging": staging, "journal": journal}

    def stop(self, run):
        if run and run.get("task_dir"):
            open(os.path.join(run["task_dir"], "arret"), "a").close()
            return True
        return False

    # ------------------------------------------------------------ résultats
    def journal_of(self, run):
        st = self._read_state(run.get("task_dir"))
        if st.get("journal") and os.path.isfile(st["journal"]):
            return st["journal"]
        for name in JOURNAUX:
            p = os.path.join(run.get("output") or "", name)
            if os.path.isfile(p):
                return p
        return None

    def results(self, run, statuses=(), q="", offset=0, limit=50):
        journal = self.journal_of(run)
        if not journal:
            return {"total": 0, "rows": [], "counts": {}}
        statuses = [s for s in statuses if s in STATUTS]
        where, args = [], []
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        if q:
            where.append("(rel LIKE ? OR method LIKE ? OR cause LIKE ?)")
            args += [f"%{q}%"] * 3
        w = (" WHERE " + " AND ".join(where)) if where else ""
        try:
            conn = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in conn.execute(
                    "SELECT rel, kind, ext, size, status, diag, cause, method, details, output, extras FROM fichiers"
                    f"{w} ORDER BY rel LIMIT ? OFFSET ?", (*args, int(limit), int(offset)))]
                total = conn.execute(f"SELECT COUNT(*) FROM fichiers{w}", args).fetchone()[0]
                counts = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM fichiers GROUP BY status")}
            finally:
                conn.close()
        except sqlite3.Error:
            # Base occupée ou ancien format : le moteur répond à la place.
            return self.command("resultats", {"journal": journal, "status": statuses, "q": q, "offset": offset,
                                              "limit": limit}, 60)
        for r in rows:
            r["output_rel"] = self.output_rel(run, r.get("output"))
        return {"total": total, "rows": rows, "counts": counts}

    def output_rel(self, run, value):
        """Chemin d'un fichier produit, relatif au dossier de sortie (le moteur peut donner un chemin absolu)."""
        if not value:
            return None
        out = run.get("output") or ""
        v = str(value)
        for root in [out] + [os.path.dirname(self.journal_of(run) or "")]:
            if root and os.path.normcase(v).startswith(os.path.normcase(root.rstrip("\\/")) + os.sep):
                return v[len(root.rstrip("\\/")) + 1:].replace("\\", "/")
        return v.replace("\\", "/").lstrip("/")

    def resolve_file(self, run, rel):
        """Fichier ou dossier du dossier de sortie (ou de sa zone tampon pendant le traitement), sinon None."""
        rel = (rel or "").replace("\\", "/").strip("/")
        if "\0" in rel or any(part == ".." for part in rel.split("/")):
            return None
        roots = []
        journal = self.journal_of(run)
        if journal and os.path.dirname(journal) != run.get("output"):
            roots.append(os.path.dirname(journal))
        roots.append(run.get("output") or "")
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            target = os.path.realpath(os.path.join(root, *rel.split("/"))) if rel else os.path.realpath(root)
            if os.path.exists(target) and _within(target, root):
                return target
        return None

    # ------------------------------------------------------------ réparation individuelle
    def start_individual(self, name, content_or_path, actor, share=None, rel=None):
        """Copie le document dans un dossier de travail isolé et lance RepareDocs dessus."""
        name = os.path.basename(name.replace("\\", "/")) or "document"
        work = os.path.join(self.base, "individuel", f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.monotonic_ns() % 100000}")
        src_dir = os.path.join(work, "source")
        os.makedirs(src_dir, exist_ok=True)
        dest = os.path.join(src_dir, name)
        if isinstance(content_or_path, (bytes, bytearray)):
            with open(dest, "wb") as fh:
                fh.write(content_or_path)
        else:
            shutil.copy2(content_or_path, dest)
        origin = {"name": name}
        if share is not None:
            origin.update(share_id=share["id"], rel=rel)
        return self.start("individuel", src_dir, os.path.join(work, "sortie"), {}, actor, share=share, origin=origin)

    def individual_result(self, run):
        res = self.results(run, limit=5)
        return res["rows"][0] if res["rows"] else None

    def apply_individual(self, run, mode, recycle, share, actor):
        """Dépose le fichier réparé dans le partage : copie « (réparé) » ou remplacement (original → corbeille)."""
        row = self.individual_result(run)
        if not row or row.get("status") not in ("REPARE", "REPARE_PARTIEL") or not row.get("output_rel"):
            raise RepareDocsError("Aucun fichier réparé à déposer.")
        repaired = self.resolve_file(run, row["output_rel"])
        rel = (run.get("origin") or {}).get("rel")
        if not repaired or not os.path.isfile(repaired) or not rel:
            raise RepareDocsError("Fichier réparé ou emplacement d'origine introuvable.")
        src = safe_join(share["path"], rel)
        ext = os.path.splitext(repaired)[1]
        base = os.path.splitext(src)[0]
        target = None
        if mode == "replace" and row["status"] == "REPARE" and os.path.isfile(src):
            recycle.discard(share, rel, "remplacé par la version réparée (RepareDocs)", actor)
            target = base + ext
        if not target or os.path.exists(target):          # jamais d'écrasement d'un fichier existant
            target = f"{base} (réparé){ext}"
            if os.path.exists(target):
                target = f"{base} (réparé {time.strftime('%Y%m%d-%H%M%S')}){ext}"
        shutil.copy2(repaired, target)
        self.db.audit(actor, "RepareDocs : dépôt du fichier réparé", target, mode)
        return {"path": relpath(share["path"], target), "mode": mode}
