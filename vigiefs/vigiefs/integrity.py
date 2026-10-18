"""Contrôle d'intégrité des documents d'un dossier et réparation (sur place ou en atelier)."""

import json
import os
import shutil
import tempfile
import time

from . import docrepair
from .config import merged_setting
from .rules import relpath, safe_join
from .storage import is_within

STATUS_LABEL = {"ok": "Sain", "warning": "Anomalie mineure", "corrupt": "Corrompu",
                "irrecoverable": "Irrécupérable", "protected": "Protégé par mot de passe"}


def integrity_scan(share, ctx, db, skip_roots=()):
    root = os.path.realpath(share["path"])
    max_size = int(merged_setting(db, "repair").get("max_size_mb", 300)) * 1024 * 1024
    counts = {k: 0 for k in STATUS_LABEL}
    by_family = {}
    problems = []
    checked = skipped = 0
    for dirpath, dirnames, filenames in os.walk(root):
        ctx.check()
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                       and not any(r and is_within(os.path.join(dirpath, d), r) for r in skip_roots)]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            if ext not in docrepair.SUPPORTED or name.startswith("~$") or "(réparé)" in name:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_size > max_size:
                skipped += 1
                continue
            try:
                d = docrepair.diagnose(full, quick=True)
            except Exception as exc:  # noqa: BLE001
                d = {"status": "corrupt", "format": ext, "family": docrepair.family_of(ext),
                     "issues": [{"level": "error", "message": f"Analyse impossible : {exc}"}], "repairable": True}
            checked += 1
            status = d["status"] if d["status"] in counts else "ok"
            counts[status] += 1
            fam = d.get("family") or "autre"
            by_family.setdefault(fam, {"checked": 0, "problems": 0})
            by_family[fam]["checked"] += 1
            if status != "ok":
                by_family[fam]["problems"] += 1
                if len(problems) < 2000:
                    problems.append({"path": relpath(root, full), "format": d["format"], "family": fam,
                                     "status": status, "size": st.st_size, "mtime": st.st_mtime,
                                     "repairable": d.get("repairable", False),
                                     "issues": [i["message"] for i in d["issues"]][:4],
                                     "recommendation": d.get("recommendation", "")})
            ctx.progress(checked=checked, problems=len(problems), current=relpath(root, full))
    order = {"irrecoverable": 0, "corrupt": 1, "warning": 2, "protected": 3}
    problems.sort(key=lambda p: (order.get(p["status"], 9), p["path"]))
    return {"checked": checked, "skipped_large": skipped, "counts": counts, "by_family": by_family,
            "problems": problems}


def _record(db, source, share_id, rel, name, input_path, result, actor):
    before = result.get("before") or {}
    return db.execute(
        "INSERT INTO repairs(source, share_id, rel_path, original_name, input_path, format, status, diagnosis, "
        "actions, output_path, created_at, actor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (source, share_id, rel, name, input_path, before.get("format"), result["status"],
         json.dumps({"before": before, "after": result.get("after"), "message": result.get("message")}, default=str),
         json.dumps(result.get("actions", [])), result.get("output"), time.time(), actor))


def repair_in_share(db, recycle, share, rel, mode, actor):
    """mode 'copy' : « nom (réparé).ext » à côté de l'original ; 'replace' : original → corbeille."""
    src = safe_join(share["path"], rel)
    if not os.path.isfile(src):
        raise FileNotFoundError(rel)
    lo = docrepair.find_libreoffice(merged_setting(db, "repair").get("libreoffice_path") or None)
    work = tempfile.mkdtemp(prefix="vigiefs-repair-")
    try:
        result = docrepair.repair(src, work, libreoffice=lo)
        out = result.get("output")
        if out:
            if mode == "replace" and result["status"] == "repaired":
                new_ext = os.path.splitext(out)[1]
                target = os.path.splitext(src)[0] + new_ext
                recycle.discard(share, rel, "remplacé par la version réparée", actor)
                if os.path.exists(target):
                    target = os.path.join(os.path.dirname(src), os.path.basename(out))
                shutil.move(out, target)
            else:
                target = os.path.join(os.path.dirname(src), os.path.basename(out))
                if os.path.exists(target):
                    base, ext = os.path.splitext(target)
                    target = f"{base} {time.strftime('%Y%m%d-%H%M%S')}{ext}"
                shutil.move(out, target)
            result["output"] = target
            result["output_rel"] = relpath(share["path"], target)
        rid = _record(db, "share", share["id"], rel, os.path.basename(src), src, result, actor)
        db.audit(actor, "réparation de document", src, result["status"])
        result["id"] = rid
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def repair_upload(db, data_dir, filename, content, actor):
    """Atelier : fichier envoyé par un technicien (pièce jointe client, clé USB…)."""
    name = os.path.basename(filename.replace("\\", "/")) or "document"
    rid = db.execute("INSERT INTO repairs(source, original_name, status, created_at, actor) VALUES('upload',?,?,?,?)",
                     (name, "pending", time.time(), actor))
    base = os.path.join(data_dir, "atelier", str(rid))
    os.makedirs(os.path.join(base, "original"), exist_ok=True)
    src = os.path.join(base, "original", name)
    with open(src, "wb") as fh:
        fh.write(content)
    lo = docrepair.find_libreoffice(merged_setting(db, "repair").get("libreoffice_path") or None)
    result = docrepair.repair(src, os.path.join(base, "repare"), libreoffice=lo)
    before = result.get("before") or {}
    db.execute(
        "UPDATE repairs SET input_path=?, format=?, status=?, diagnosis=?, actions=?, output_path=? WHERE id=?",
        (src, before.get("format"), result["status"],
         json.dumps({"before": before, "after": result.get("after"), "message": result.get("message")}, default=str),
         json.dumps(result.get("actions", [])), result.get("output"), rid))
    db.audit(actor, "atelier de réparation", name, result["status"])
    result["id"] = rid
    return result
