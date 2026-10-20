"""API : réorganisation d'arborescence (proposition, sélection, préparation, déplacement, retour arrière)."""

import json
import os
import shutil
import time
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import restructure as rs
from . import smb, system
from .config import merged_setting

EDITABLE = ("draft", "prepared")


class OpEdit(BaseModel):
    id: str
    selected: Optional[bool] = None
    name: Optional[str] = Field(None, max_length=255)


class PlanEdit(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    ops: List[OpEdit] = Field(default_factory=list)
    rules: Dict[str, bool] = Field(default_factory=dict)      # (dé)sélection d'une règle entière
    options: Dict = Field(default_factory=dict)


class ConfirmIn(BaseModel):
    confirm: str = ""


def register(app, c):
    db, engine, jobs, alerts = c["db"], c["engine"], c["jobs"], c["alerts"]
    viewer, operator, admin, get_share, data_dir = c["viewer"], c["operator"], c["admin"], c["get_share"], c["data_dir"]
    base = os.path.join(data_dir, "reorganisation")

    def work(pid):
        return os.path.join(base, str(pid))

    def load(pid):
        row = db.query_one("SELECT * FROM restructure_plans WHERE id=?", (pid,))
        if not row:
            raise HTTPException(404, "Plan de réorganisation introuvable")
        row["data"] = json.loads(row["data"])
        row["result"] = json.loads(row["result"] or "{}")
        return row

    def save(pid, data, **cols):
        sets = ", ".join(["data=?", "updated_at=?"] + [f"{k}=?" for k in cols])
        db.execute(f"UPDATE restructure_plans SET {sets} WHERE id=?",
                   (json.dumps(data, ensure_ascii=False), time.time(), *cols.values(), pid))

    def refresh(data, pid):
        index = rs.load_index(os.path.join(work(pid), "index.json.gz"))
        data["metrics"] = rs.metrics(data, index) if index else data.get("metrics")
        data["problems"] = rs.validate(data, index)
        return data

    def summary(row):
        ops = row["data"]["ops"]
        return {"id": row["id"], "name": row["name"], "status": row["status"], "created_at": row["created_at"],
                "created_by": row["created_by"], "prepared_at": row["prepared_at"], "executed_at": row["executed_at"],
                "ops": len(ops), "selected": sum(1 for o in ops if o.get("selected")),
                "metrics": row["data"].get("metrics")}

    # ------------------------------------------------------------ proposition
    @app.post("/api/shares/{share_id}/restructure/propose")
    def propose(share_id: int, user=Depends(operator)):
        share = get_share(share_id)

        def run(ctx):
            ctx.progress(force=True, phase="Inventaire de l'arborescence")
            plan, dirs = rs.propose(share, merged_setting(db, "restructure"), merged_setting(db, "paths"),
                                    engine.skip_roots() + engine.protected_roots(), cancel=lambda: ctx.cancelled)
            pid = db.execute("INSERT INTO restructure_plans(share_id, name, status, data, created_at, created_by) "
                             "VALUES(?,?, 'draft', '{}', ?, ?)",
                             (share["id"], time.strftime("Proposition du %d/%m/%Y %H:%M"), time.time(), ctx.actor))
            rs.save_index(os.path.join(work(pid), "index.json.gz"), dirs)
            save(pid, refresh(plan, pid))
            return {"plan_id": pid, "ops": len(plan["ops"])}

        return {"job_id": jobs.submit("restructure_propose", run, user["username"], share=share)}

    @app.get("/api/shares/{share_id}/restructure")
    def plans(share_id: int, user=Depends(viewer)):
        get_share(share_id)
        rows = db.query("SELECT * FROM restructure_plans WHERE share_id=? ORDER BY id DESC", (share_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return [summary(r) for r in rows]

    @app.get("/api/restructure/{pid}")
    def plan(pid: int, user=Depends(viewer)):
        row = load(pid)
        share = get_share(row["share_id"])
        return {**summary(row), "share_id": share["id"], "share_name": share["name"], "data": row["data"],
                "result": row["result"], "rules": {k: v[0] for k, v in rs.RULES.items()}, "op_labels": rs.OP_LABEL}

    @app.put("/api/restructure/{pid}")
    def edit(pid: int, body: PlanEdit, user=Depends(operator)):
        row = load(pid)
        if row["status"] not in EDITABLE:
            raise HTTPException(409, "Ce plan a déjà été appliqué : créez une nouvelle proposition")
        data = row["data"]
        by_id = {o["id"]: o for o in data["ops"]}
        for rule, flag in body.rules.items():
            for o in data["ops"]:
                if o["rule"] == rule:
                    o["selected"] = bool(flag)
        for e in body.ops:
            o = by_id.get(e.id)
            if not o:
                continue
            if e.selected is not None:
                o["selected"] = e.selected
            if e.name is not None and o["type"] in ("rename_dir", "rename_file"):
                o["name"] = e.name.strip()
        for k in ("verify_hash", "vss_snapshot", "backup_mode"):
            if k in body.options:
                data["options"][k] = bool(body.options[k])
        if "threads" in body.options:
            data["options"]["threads"] = max(1, min(64, int(body.options["threads"])))
        status = row["status"]
        if status == "prepared" and rs.fingerprint(data) != (row["result"].get("prepare") or {}).get("fingerprint"):
            status = "draft"          # sélection modifiée : l'étape 1 doit être refaite
        save(pid, refresh(data, pid), status=status, name=body.name or row["name"])
        return plan(pid, user)

    @app.delete("/api/restructure/{pid}")
    def delete(pid: int, user=Depends(operator)):
        row = load(pid)
        if row["status"] in ("executing", "done", "partial"):
            raise HTTPException(409, "Plan appliqué : conservé pour permettre le retour arrière")
        if row["status"] == "prepared":
            rs.unprepare((row["result"].get("prepare") or {}).get("created", []), get_share(row["share_id"])["path"])
        db.execute("DELETE FROM restructure_plans WHERE id=?", (pid,))
        shutil.rmtree(work(pid), ignore_errors=True)
        return {"ok": True}

    @app.get("/api/restructure/{pid}/script")
    def script(pid: int, user=Depends(operator)):
        row = load(pid)
        share = get_share(row["share_id"])
        text = rs.script(row["data"], share["path"])
        return Response(("﻿" + text).encode("utf-8"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="reorganisation-{pid}.ps1"'})

    @app.get("/api/restructure/{pid}/tree")
    def tree(pid: int, depth: int = 3, user=Depends(viewer)):
        """Arborescence avant / après (premiers niveaux) : fichiers et volume cumulés par dossier."""
        row = load(pid)
        index = rs.load_index(os.path.join(work(pid), "index.json.gz"))
        m = rs.Mapping(row["data"])
        sides = {"before": {}, "after": {}}
        for rel, n in index.items():
            removed = any(rel == r or rel.startswith(r + "/") for r in m.remove)
            for side, path in (("before", rel), ("after", None if removed else m.dir(rel))):
                if path is None:
                    continue
                parts = path.split("/") if path else []
                for i in range(1, min(len(parts), depth) + 1):
                    key = "/".join(parts[:i])
                    node = sides[side].setdefault(key, {"path": key, "depth": i, "files": 0, "size": 0})
                    node["files"] += n["files"]
                    node["size"] += n["size"]
        before = sides["before"]
        for key, node in sides["after"].items():
            old = before.get(key)
            node["changed"] = old is None or old["files"] != node["files"]
        for key, node in before.items():
            new = sides["after"].get(key)
            node["changed"] = new is None or new["files"] != node["files"]
        return {side: sorted(v.values(), key=lambda x: x["path"].lower()) for side, v in sides.items()}

    # ------------------------------------------------------------ étape 1 : création des dossiers
    @app.post("/api/restructure/{pid}/prepare")
    def prepare(pid: int, user=Depends(admin)):
        row = load(pid)
        if row["status"] not in EDITABLE:
            raise HTTPException(409, "Plan déjà appliqué")
        share = get_share(row["share_id"])
        data = row["data"]
        if not any(o.get("selected") for o in data["ops"]):
            raise HTTPException(400, "Aucune opération sélectionnée")

        def run(ctx):
            ctx.progress(force=True, phase="Contrôles")
            opened = []
            if system.IS_WINDOWS:
                try:
                    opened = [f["path"] for f in smb.open_files()]
                except Exception:  # noqa: BLE001
                    opened = []
            try:
                free = shutil.disk_usage(share["path"]).free
            except OSError:
                free = None
            problems = rs.preflight(data, share["path"], opened, free)
            errors = [p for p in problems if p["level"] == "error"]
            if errors:
                result = {**row["result"], "prepare": {"problems": problems, "at": time.time(), "blocked": True}}
                db.execute("UPDATE restructure_plans SET result=? WHERE id=?", (json.dumps(result), pid))
                raise RuntimeError(f"{len(errors)} contrôle(s) bloquant(s) : " + " ; ".join(e["message"] for e in errors[:3]))
            ctx.progress(force=True, phase="Création de l'arborescence cible")
            res = rs.prepare(data, share["path"], cancel=lambda: ctx.cancelled)
            res["problems"] = problems
            db.execute("UPDATE restructure_plans SET status='prepared', prepared_at=?, result=? WHERE id=?",
                       (time.time(), json.dumps({**row["result"], "prepare": res}), pid))
            db.audit(ctx.actor, "réorganisation : création des dossiers", share["path"], f"{len(res['created'])} dossier(s)")
            return {"created": len(res["created"]), "existing": len(res["existing"]), "warnings": len(problems)}

        return {"job_id": jobs.submit("restructure_prepare", run, user["username"], share=share)}

    @app.post("/api/restructure/{pid}/unprepare")
    def unprepare(pid: int, user=Depends(admin)):
        row = load(pid)
        if row["status"] != "prepared":
            raise HTTPException(409, "Le plan n'est pas préparé")
        removed = rs.unprepare((row["result"].get("prepare") or {}).get("created", []), get_share(row["share_id"])["path"])
        db.execute("UPDATE restructure_plans SET status='draft' WHERE id=?", (pid,))
        return {"removed": removed}

    # ------------------------------------------------------------ étape 2 : déplacement vérifié
    @app.post("/api/restructure/{pid}/execute")
    def execute(pid: int, body: ConfirmIn, user=Depends(admin)):
        row = load(pid)
        if row["status"] != "prepared":
            raise HTTPException(409, "Faites d'abord l'étape 1 (création des dossiers)")
        if rs.fingerprint(row["data"]) != (row["result"].get("prepare") or {}).get("fingerprint"):
            raise HTTPException(409, "La sélection a changé depuis l'étape 1 : refaites l'étape 1")
        if body.confirm.strip().upper().replace("É", "E") != "DEPLACER":
            raise HTTPException(400, "Confirmation requise : saisissez DÉPLACER")
        share = get_share(row["share_id"])
        data = row["data"]

        def run(ctx):
            cancel = lambda: ctx.cancelled  # noqa: E731
            db.execute("UPDATE restructure_plans SET status='executing' WHERE id=?", (pid,))
            engine.monitor.quiet.add(share["id"])
            report = {"started_at": time.time()}
            completed = False
            try:
                if data["options"].get("vss_snapshot"):
                    ctx.progress(force=True, phase="Cliché instantané du volume")
                    report["vss"] = rs.vss_snapshot(share["path"])
                ctx.progress(force=True, phase="Sauvegarde des droits")
                try:
                    report["acl_backup"] = engine.rights.backup(share, "", "avant-reorganisation")
                except Exception as exc:  # noqa: BLE001
                    report["acl_backup_error"] = str(exc)
                ctx.progress(force=True, phase="Inventaire avant déplacement")
                report["inventory_before"] = rs.inventory(share["path"], engine.skip_roots(), cancel)
                ctx.progress(force=True, phase="Déplacement vérifié des fichiers")
                res = rs.execute(data, share["path"], work(pid), cancel,
                                 progress=lambda **kw: ctx.progress(phase="Déplacement vérifié des fichiers", **kw))
                report.update(res)
                ctx.progress(force=True, phase="Inventaire après déplacement")
                report["inventory_after"] = rs.inventory(share["path"], engine.skip_roots(), cancel)
                completed = True
            except InterruptedError:
                report["interrupted"] = True
                raise
            except Exception as exc:  # noqa: BLE001
                report["error"] = str(exc)
                raise
            finally:
                engine.monitor.quiet.discard(share["id"])
                report["finished_at"] = time.time()
                b, a = report.get("inventory_before"), report.get("inventory_after")
                report["inventory_ok"] = bool(b and a and b["files"] == a["files"] and b["size"] == a["size"])
                kept = (report.get("totals") or {}).get("kept", 0)
                failed = any(r["status"] == "failed" for r in (report.get("ops") or {}).values())
                manifest = os.path.join(work(pid), "manifeste.jsonl")
                moved_any = os.path.isfile(manifest) and os.path.getsize(manifest) > 0
                if "ops" not in report and not moved_any:
                    status = "prepared"       # rien n'a été déplacé : l'étape 2 peut être relancée
                else:
                    status = "done" if completed and not kept and not failed else "partial"
                db.execute("UPDATE restructure_plans SET status=?, executed_at=?, result=? WHERE id=?",
                           (status, time.time(), json.dumps({**row["result"], "execute": report}, default=str), pid))
                db.audit(ctx.actor, "réorganisation : déplacement", share["path"],
                         f"{(report.get('totals') or {}).get('moved', 0)} fichier(s), {kept} laissé(s) en place")
                if b and a and not report["inventory_ok"]:
                    alerts.raise_alert(
                        f"restructure:{pid}", "warning", "paths", "Réorganisation : inventaire différent",
                        f"Avant : {b['files']} fichiers ; après : {a['files']} fichiers sur « {share['name']} ». "
                        "Aucun fichier n'est supprimé sans vérification : l'écart vient en général d'une activité "
                        "des utilisateurs pendant le déplacement. Vérifiez le détail du plan.", share=share,
                        details={"plan_id": pid, "before": b, "after": a})
                engine.request_scan(share["id"])
                engine.start_acl_scan(share, "réorganisation")
            return {"moved": report["totals"]["moved"], "kept": report["totals"]["kept"],
                    "inventory_ok": report["inventory_ok"], "status": status}

        return {"job_id": jobs.submit("restructure_execute", run, user["username"], share=share)}

    @app.post("/api/restructure/{pid}/rollback")
    def rollback(pid: int, body: ConfirmIn, user=Depends(admin)):
        row = load(pid)
        if row["status"] not in ("done", "partial"):
            raise HTTPException(409, "Retour arrière possible après un déplacement")
        if body.confirm.strip().upper() != "ANNULER":
            raise HTTPException(400, "Confirmation requise : saisissez ANNULER")
        share = get_share(row["share_id"])

        def run(ctx):
            engine.monitor.quiet.add(share["id"])
            try:
                res = rs.rollback(share["path"], work(pid), row["data"].get("options"), lambda: ctx.cancelled,
                                  progress=lambda **kw: ctx.progress(**kw))
                res["removed_dirs"] = rs.unprepare((row["result"].get("prepare") or {}).get("created", []), share["path"])
            finally:
                engine.monitor.quiet.discard(share["id"])
            db.execute("UPDATE restructure_plans SET status='rolled_back', result=? WHERE id=?",
                       (json.dumps({**row["result"], "rollback": {**res, "at": time.time()}}, default=str), pid))
            db.audit(ctx.actor, "réorganisation : retour arrière", share["path"], f"{res['moved']} fichier(s)")
            engine.request_scan(share["id"])
            return {"moved": res["moved"], "kept": len(res["kept"]), "renamed": res["renamed"]}

        return {"job_id": jobs.submit("restructure_rollback", run, user["username"], share=share)}
