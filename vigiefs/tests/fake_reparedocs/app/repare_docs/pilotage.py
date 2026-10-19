"""Faux moteur RepareDocs pour les tests, calqué sur repare_docs/pilotage.py de RepareDocs V2.5.

    RepareDocs.py --web <commande> <fichier.json>     info | chemins | lancer | tache | resultats

Reproduit ce que VigieFS exploite du vrai moteur : réponses JSON ASCII, tâche détachée relancée par « tache »,
fichier d'état réécrit avec battement de cœur, suivi SQLite (mêmes colonnes), dossiers 1-REPARES,
2-REPARES_PARTIELLEMENT et 3-ORIGINAUX_ENDOMMAGES, refus d'une sortie sur le disque analysé (simulé par
FAKE_RD_SAME_VOLUME=1), remise en place sans bilan « done » (seulement son journal), fichier témoin d'arrêt.
Les dommages sont décidés par le nom du fichier (abime, tronqu → réparé ; coup → partiel ; zeros, perdu → perdu)."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

VERSION = "2.5-test"
JOURNAL = "etat_analyse_v2.sqlite"
PHASES = ["Recensement", "Analyse", "Récupération disque", "Réparation", "Rapport"]
KINDS = {".pdf": "pdf", ".docx": "word", ".doc": "word", ".xlsx": "excel", ".xls": "excel", ".pptx": "powerpoint"}
COLS = ["path", "rel", "ext", "kind", "size", "mtime", "status", "diag", "cause", "method", "output",
        "extras", "details", "head", "duration", "ts"]
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "RepareDocs.py")


def _reply(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))
    sys.stdout.flush()


def default_options():
    return dict(source="", output="", kinds=["pdf", "word", "excel", "powerpoint"], analyse_only=False, deep=False,
                use_office=True, use_lo=True, use_gs=True, use_vss=True, use_disk=True, disk_search=512,
                office_check_legacy=False, resume=True, allow_same_volume=False, workers=3, timeout=900,
                disk_workers=3)


def same_volume():
    return os.environ.get("FAKE_RD_SAME_VOLUME") == "1"


class Etat:
    def __init__(self, path, job):
        opts = job.get("options") or {}
        self.path = path
        self.s = {"running": True, "pid": os.getpid(), "type": job.get("type", "traitement"), "started": time.time(),
                  "heartbeat": time.time(), "phase": None, "progress": None, "scan": None, "disk": None, "done": None,
                  "error": None, "logs": [], "seq": 0, "output": opts.get("output") or job.get("output"),
                  "source": opts.get("source"), "analyse_only": bool(opts.get("analyse_only")), "journal": None}
        self.flush()

    def log(self, line, level="info"):
        self.s["seq"] += 1
        self.s["logs"].append({"id": self.s["seq"], "line": time.strftime("%H:%M:%S") + "  " + line, "level": level})
        self.flush()

    def set(self, **kw):
        self.s.update(kw)
        self.flush()

    def flush(self):
        self.s["heartbeat"] = time.time()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.s, fh, ensure_ascii=False)
        os.replace(tmp, self.path)

    def close(self):
        self.set(running=False)


def journal(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS fichiers (path TEXT PRIMARY KEY, rel TEXT, ext TEXT, kind TEXT, "
               "size INTEGER, mtime REAL, status TEXT, diag TEXT, cause TEXT, method TEXT, output TEXT, "
               "extras TEXT, details TEXT, head TEXT, duration REAL, ts REAL)")
    return db


def put(db, r):
    db.execute("INSERT OR REPLACE INTO fichiers (%s) VALUES (%s)" % (",".join(COLS), ",".join("?" * len(COLS))),
               [r.get(c, "") for c in COLS])
    db.commit()


def run(opts, st, stop):
    src, output = os.path.abspath(opts["source"]), os.path.abspath(opts["output"])
    if not os.path.isdir(src):
        raise ValueError(f"Dossier introuvable : {src}")
    if same_volume() and not opts.get("analyse_only") and not opts.get("allow_same_volume"):
        raise ValueError("Le dossier de sortie est sur le même disque que les fichiers à réparer. Cochez « Réparer "
                         "sur ce même disque » pour l'autoriser (en ligne de commande : option --autoriser-meme-disque).")
    os.makedirs(output, exist_ok=True)
    st.log(f"=== RepareDocs {VERSION} ===")
    st.set(phase={"index": 0, "label": PHASES[0]})
    files = []
    for dp, _, names in os.walk(src):
        for f in names:
            if KINDS.get(os.path.splitext(f)[1].lower()) in opts["kinds"]:
                files.append(os.path.join(dp, f))
    files.sort()
    st.set(scan={"found": len(files), "dir": src},
           disk={"enabled": False, "reason": "lecture directe du volume impossible"})
    db = journal(os.path.join(output, JOURNAL))
    st.set(journal=os.path.join(output, JOURNAL))
    known = {r[0]: (r[1], r[2], r[3]) for r in db.execute("SELECT path, size, mtime, status FROM fichiers")} \
        if opts.get("resume") else {}
    counts = {}
    delay = float(os.environ.get("FAKE_RD_DELAY", "0.05"))
    st.log(f"{len(files)} fichier(s) trouvé(s)")
    st.set(phase={"index": 1, "label": PHASES[1]})
    stopped = False
    for i, path in enumerate(files):
        if os.path.exists(stop):
            os.remove(stop)
            st.log("Arrêt demandé…", "warn")
            stopped = True
            break
        rel = os.path.relpath(path, src)
        info = os.stat(path)
        k = known.get(path)
        if k and k[0] == info.st_size and abs((k[1] or 0) - info.st_mtime) < 2:
            counts[k[2]] = counts.get(k[2], 0) + 1
            continue
        data = open(path, "rb").read()
        name = os.path.basename(path).lower()
        ext = os.path.splitext(path)[1].lower()
        r = {"path": path, "rel": rel, "ext": ext, "kind": KINDS.get(ext), "size": info.st_size,
             "mtime": info.st_mtime, "status": "OK", "diag": "", "cause": "", "method": "", "output": "",
             "extras": "", "details": "", "head": data[:16].hex(), "duration": 0.01, "ts": time.time()}
        target = None
        if "zeros" in name or "zéros" in name or "chiffr" in name:
            r.update(status="IRRECUPERABLE", method="aucune méthode n'a abouti",
                     cause="Contenu ENTIÈREMENT remplacé par des zéros : les données ont été perdues")
        elif "perdu" in name:
            r.update(status="IRRECUPERABLE", method="aucune méthode n'a abouti", cause="Structure absente")
        elif "coup" in name:
            r.update(status="ENDOMMAGE", cause="Fin de fichier manquante")
            if not opts["analyse_only"]:
                r.update(status="REPARE_PARTIEL", method="reconstruction de l'archive")
                target = ("2-REPARES_PARTIELLEMENT", b"PARTIEL:")
        elif "abime" in name or "tronqu" in name:
            r.update(status="ENDOMMAGE", cause="Archive illisible")
            if not opts["analyse_only"]:
                r.update(status="REPARE", method="réécriture")
                target = ("1-REPARES", b"REPARE:")
        if target:
            r["output"] = os.path.join(target[0], rel)
            os.makedirs(os.path.dirname(os.path.join(output, r["output"])), exist_ok=True)
            with open(os.path.join(output, r["output"]), "wb") as fh:
                fh.write(target[1] + data)
            keep = os.path.join(output, "3-ORIGINAUX_ENDOMMAGES", rel)
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy2(path, keep)
        put(db, r)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] != "OK":
            st.log(f"[{r['status']}] {rel}" + (f" → {r['method']}" if r["method"] else ""),
                   {"REPARE": "ok", "REPARE_PARTIEL": "warn"}.get(r["status"], "bad"))
        st.set(phase={"index": 3, "label": PHASES[3]},
               progress={"done": i + 1, "total": len(files), "bytes": 0, "total_bytes": 0, "counts": dict(counts),
                         "eta": None, "current": [rel]})
        time.sleep(delay)
    st.set(phase={"index": 4, "label": PHASES[4]})
    rows = [dict(zip(COLS, row)) for row in db.execute("SELECT %s FROM fichiers ORDER BY rel" % ",".join(COLS))]
    db.close()
    report = os.path.join(output, "RAPPORT.html")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("<html><body><h1>Rapport</h1>" + "".join(f"<p>{r['rel']} : {r['status']}</p>" for r in rows)
                 + "<script>alert(1)</script></body></html>")
    summary = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    st.log(f"Rapport : {report}")
    st.set(done={"summary": summary, "report": report, "output": output, "stopped": stopped})


def restore(output, log):
    db = journal(os.path.join(output, JOURNAL))
    done = skipped = errors = 0
    rows = [dict(zip(COLS, row)) for row in db.execute("SELECT %s FROM fichiers" % ",".join(COLS))]
    for r in rows:
        if r["status"] != "REPARE" or not r["output"] or "remis en place" in (r["method"] or ""):
            continue
        st = os.stat(r["path"])
        if st.st_size != r["size"] or abs(st.st_mtime - r["mtime"]) > 2:
            skipped += 1
            log(f"Ignoré (l'original a changé depuis l'analyse) : {r['rel']}")
            continue
        backup = os.path.join(output, "3-ORIGINAUX_ENDOMMAGES", r["rel"])
        if not os.path.exists(backup):
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copy2(r["path"], backup)
        shutil.copy2(os.path.join(output, r["output"]), r["path"] + ".rd_tmp")
        os.replace(r["path"] + ".rd_tmp", r["path"])
        st2 = os.stat(r["path"])
        put(db, dict(r, method=r["method"] + " — remis en place", size=st2.st_size, mtime=st2.st_mtime))
        done += 1
        log(f"Remis en place : {r['rel']}")
    db.close()
    return done, skipped, errors


def cmd_tache(job):
    st = Etat(job["etat"], job)
    stop = job.get("arret")
    if stop and os.path.exists(stop):
        os.remove(stop)
    try:
        if job.get("type") == "restaurer":
            done, skipped, errors = restore(job["output"], st.log)
            st.log(f"Remise en place terminée : {done} remplacé(s), {skipped} ignoré(s), {errors} erreur(s).",
                   "ok" if not errors else "warn")
            return 0
        opts = default_options()
        for k in opts:
            if (job.get("options") or {}).get(k) is not None:
                opts[k] = job["options"][k]
        run(opts, st, stop)
        return 0
    except ValueError as e:
        st.set(error=str(e))
        st.log("ERREUR : " + str(e), "bad")
        return 1
    finally:
        st.close()


def main(args):
    cmd, path = args[0], args[1]
    q = json.load(open(path, encoding="utf-8-sig"))
    if cmd == "tache":
        return cmd_tache(q)
    if cmd == "info":
        return _reply({"version": VERSION, "admin": False, "session0": False, "user": "test", "phases": PHASES,
                       "defaults": default_options(), "cpu": os.cpu_count(), "python": sys.executable,
                       "tools": {"office": {"word": False, "excel": False, "powerpoint": False},
                                 "libreoffice": None, "ghostscript": False}})
    if cmd == "chemins":
        src, out = (q.get("source") or "").strip(), (q.get("output") or "").strip()
        res = {"source_ok": bool(src) and os.path.isdir(src)}
        if res["source_ok"]:
            root = os.environ.get("FAKE_RD_SUGGEST_ROOT")
            res.update(copies=None, volume=os.path.splitdrive(src)[0] or "/", disk_reason=None,
                       suggestion=os.path.join(root, "RepareDocs-sorties", os.path.basename(src)) if root else None,
                       previous=bool(out) and os.path.exists(os.path.join(out, JOURNAL)))
        if src and out:
            res["same_volume"] = same_volume() if res["source_ok"] else False
            res["output_inside"] = os.path.abspath(out).startswith(os.path.abspath(src) + os.sep)
        return _reply(res)
    if cmd == "lancer":
        with open(q.get("console") or os.path.join(os.path.dirname(path), "console.log"), "ab") as log:
            p = subprocess.Popen([sys.executable, SCRIPT, "--web", "tache", os.path.abspath(path)],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, cwd=os.path.dirname(SCRIPT))
        return _reply({"ok": True, "pid": p.pid})
    if cmd == "resultats":
        return _reply({"total": 0, "rows": [], "counts": {}})
    return _reply({"ok": False, "error": f"commande inconnue : {cmd}"})
