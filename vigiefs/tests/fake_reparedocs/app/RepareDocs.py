"""Faux moteur RepareDocs pour les tests : même protocole « --web » que le vrai moteur (RepareDocs Web)."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

KINDS = {".pdf": "pdf", ".docx": "word", ".doc": "word", ".xlsx": "excel", ".xls": "excel", ".pptx": "powerpoint"}
PHASES = ["Inventaire", "Analyse", "Récupération disque", "Réparation", "Rapport"]


def out(data):
    sys.stdout.write(json.dumps(data, ensure_ascii=False))


def write_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, path)


def worker(task_file):
    task = json.load(open(task_file, encoding="utf-8"))
    opts = task["options"]
    output = opts.get("output") or task.get("output")
    state = {"running": True, "type": task["type"], "started": time.time(), "heartbeat": time.time(), "logs": [],
             "output": output, "source": opts.get("source"), "journal": os.path.join(output, "etat_analyse_v2.sqlite")}
    os.makedirs(output, exist_ok=True)

    def log(line, level="info"):
        state["logs"].append({"id": len(state["logs"]) + 1, "level": level, "line": line})

    def beat(**kw):
        state.update(kw, heartbeat=time.time())
        write_state(task["etat"], state)

    if task["type"] == "restaurer":
        db = sqlite3.connect(state["journal"])
        n = 0
        for rel, outp in db.execute("SELECT rel, output FROM fichiers WHERE status='REPARE'"):
            dst = os.path.join(opts["source"], rel)
            keep = os.path.join(output, "3-ORIGINAUX_ENDOMMAGES", rel)
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy2(dst, keep)
            shutil.copy2(os.path.join(output, outp), dst)
            n += 1
        log(f"{n} fichier(s) remis en place")
        beat(running=False, done={"stopped": False, "summary": {"REPARE": n}, "restored": n})
        return
    files = []
    for dp, _, fn in os.walk(opts["source"]):
        for f in fn:
            ext = os.path.splitext(f)[1].lower()
            if KINDS.get(ext) in opts.get("kinds", list(KINDS.values())):
                files.append(os.path.relpath(os.path.join(dp, f), opts["source"]))
    log(f"{len(files)} fichier(s) trouvé(s)")
    beat(phase={"index": 1, "label": PHASES[1]}, scan={"found": len(files), "dir": opts["source"]})
    db = sqlite3.connect(state["journal"])
    db.execute("CREATE TABLE IF NOT EXISTS fichiers (rel TEXT PRIMARY KEY, kind TEXT, ext TEXT, size INTEGER, "
               "status TEXT, diag TEXT, cause TEXT, method TEXT, details TEXT, output TEXT, extras TEXT)")
    counts = {}
    delay = float(os.environ.get("FAKE_RD_DELAY", "0.05"))
    for i, rel in enumerate(sorted(files)):
        if os.path.exists(task["arret"]):
            log("Arrêt demandé")
            db.commit()
            beat(running=False, done={"stopped": True, "summary": counts})
            return
        src = os.path.join(opts["source"], rel)
        data = open(src, "rb").read()
        name = os.path.basename(rel).lower()
        status, outp, method = "OK", None, ""
        if "chiffr" in name or "zéros" in name or "zeros" in name:
            status, method = "IRRECUPERABLE", "contenu écrasé : restauration Datto"
        elif "coup" in name:
            status, method = ("ENDOMMAGE", "") if opts.get("analyse_only") else ("REPARE_PARTIEL", "pages récupérées")
            if not opts.get("analyse_only"):
                outp = os.path.join("1-REPARES", rel)
                os.makedirs(os.path.join(output, os.path.dirname(outp)), exist_ok=True)
                with open(os.path.join(output, outp), "wb") as fh:
                    fh.write(b"PARTIEL:" + data)
        elif "abime" in name or "tronqu" in name:
            status, method = ("ENDOMMAGE", "") if opts.get("analyse_only") else ("REPARE", "reconstruction")
            if not opts.get("analyse_only"):
                outp = os.path.join("1-REPARES", rel)
                os.makedirs(os.path.join(output, os.path.dirname(outp)), exist_ok=True)
                with open(os.path.join(output, outp), "wb") as fh:
                    fh.write(b"REPARE:" + data)
        elif "perdu" in name:
            status, method = "IRRECUPERABLE", "aucune"
        counts[status] = counts.get(status, 0) + 1
        db.execute("INSERT OR REPLACE INTO fichiers VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (rel, KINDS.get(os.path.splitext(rel)[1].lower()), os.path.splitext(rel)[1], len(data), status,
                    "", "test", method, "", outp, ""))
        db.commit()
        log(f"{status} {rel}")
        beat(phase={"index": 3, "label": PHASES[3]},
             progress={"done": i + 1, "total": len(files), "bytes": 0, "total_bytes": 0, "counts": counts,
                       "current": [rel]})
        time.sleep(delay)
    with open(os.path.join(output, "RAPPORT.html"), "w", encoding="utf-8") as fh:
        fh.write("<html><body><h1>Rapport</h1>" + "".join(
            f'<a href="1-REPARES/{r}">{r}</a>' for r in files) + "<script>alert(1)</script></body></html>")
    log("Rapport prêt")
    beat(running=False, phase={"index": 4, "label": PHASES[4]}, done={"stopped": False, "summary": counts})


def main():
    if sys.argv[1] == "--worker":
        return worker(sys.argv[2])
    cmd, path = sys.argv[2], sys.argv[3]
    payload = json.load(open(path, encoding="utf-8"))
    if cmd == "info":
        return out({"version": "2.5-test", "admin": False, "session0": False, "phases": PHASES,
                    "tools": {"office": {}, "libreoffice": None, "ghostscript": False},
                    "defaults": {"workers": 4, "disk_workers": 3}})
    if cmd == "chemins":
        ok = os.path.isdir(payload["source"])
        return out({"ok": ok, "error": None if ok else "source introuvable"})
    if cmd == "lancer":
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", path], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return out({"ok": True})
    if cmd == "resultats":
        return out({"total": 0, "rows": [], "counts": {}})
    return out({"ok": False, "error": "commande inconnue"})


if __name__ == "__main__":
    main()
