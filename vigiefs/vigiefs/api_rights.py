"""API : droits d'accès, matrice, architecture, quotas, partages SMB, santé du serveur, données sensibles."""

import json
import os
import tempfile
import time
from typing import List, Optional

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import acl, health, matrix as mx, sensitive, smb, structure, system
from .config import merged_setting


class AclOp(BaseModel):
    op: str = Field(pattern="^(grant|revoke|remove_deny|protect|inherit|owner|reset_children)$")
    path: str = ""
    principal: Optional[str] = None
    level: Optional[str] = Field(None, pattern="^(list|read|write|modify|full)$")
    strip: bool = False
    recursive: bool = False


class AclOpsIn(BaseModel):
    ops: List[AclOp]


class RestoreIn(BaseModel):
    name: str


class MatrixCreateIn(BaseModel):
    kind: str = Field("proposal", pattern="^(proposal|current|empty)$")
    name: str = ""
    depth: Optional[int] = Field(None, ge=0, le=6)


class MatrixSaveIn(BaseModel):
    name: Optional[str] = None
    data: Optional[dict] = None


class MatrixApplyIn(BaseModel):
    confirm: bool = False


class QuotaIn(BaseModel):
    share_id: int
    rel_path: str = ""
    limit_bytes: int = Field(gt=0)
    warn_pct: int = Field(85, ge=1, le=100)
    crit_pct: int = Field(95, ge=1, le=100)
    mode: str = Field("hard", pattern="^(soft|hard)$")
    enforcement: str = Field("auto", pattern="^(auto|fsrm|vigiefs|none)$")
    auto_apply: bool = False
    template: str = ""
    notes: str = ""


class AbeIn(BaseModel):
    enabled: bool


def register(app, c):
    db, engine, jobs, alerts = c["db"], c["engine"], c["jobs"], c["alerts"]
    viewer, operator, admin = c["viewer"], c["operator"], c["admin"]
    get_share, resolve = c["get_share"], c["resolve"]
    rights, quotas = engine.rights, engine.quotas

    def snapshot_or_404(share_id, snap_id=None):
        snap = rights.latest(share_id, snap_id)
        if not snap:
            raise HTTPException(404, "Aucune analyse des droits : lancez d'abord une analyse.")
        return snap

    def platform_name():
        return "windows" if system.IS_WINDOWS else "posix"

    # ================================================================ droits
    @app.get("/api/acl/capabilities")
    def acl_caps(user=Depends(viewer)):
        return {**system.capabilities(), "levels": acl.LEVEL_LABEL, "scopes": acl.SCOPE_LABEL}

    @app.get("/api/acl/overview")
    def acl_overview(user=Depends(viewer)):
        out = []
        for s in engine.shares():
            row = db.query_one("SELECT id, created_at, summary FROM acl_snapshots WHERE share_id=? ORDER BY created_at "
                               "DESC LIMIT 1", (s["id"],))
            m = db.query_one("SELECT id, name, status, applied_at FROM matrices WHERE share_id=? ORDER BY "
                             "(status='applied') DESC, updated_at DESC LIMIT 1", (s["id"],))
            client = db.query_one("SELECT name FROM clients WHERE id=?", (s["client_id"],))
            out.append({"id": s["id"], "name": s["name"], "path": s["path"], "client_name": client["name"] if client else "",
                        "scanned_at": row["created_at"] if row else None,
                        "summary": json.loads(row["summary"]) if row else None, "matrix": m})
        return out

    @app.post("/api/shares/{share_id}/acl/scan")
    def acl_scan(share_id: int, user=Depends(operator)):
        share = get_share(share_id)
        return {"job_id": engine.start_acl_scan(share, user["username"])}

    @app.get("/api/shares/{share_id}/acl")
    def acl_latest(share_id: int, snapshot_id: Optional[int] = None, user=Depends(viewer)):
        share = get_share(share_id)
        history = rights.history(share_id)
        if not history:
            return {"snapshot": None, "history": [], "platform": platform_name()}
        snap = rights.latest(share_id, snapshot_id)
        findings = rights.findings(snap["id"])
        issues = {}
        for f in findings:
            issues.setdefault(f["path"], []).append(f["severity"])
        nodes = [{"path": n["path"], "depth": n["depth"], "owner": n.get("owner"), "protected": n.get("protected"),
                  "error": n.get("error"),
                  "explicit": sum(1 for a in n["aces"] if not a["inherited"]),
                  "issues": len(issues.get(n["path"], [])),
                  "critical": issues.get(n["path"], []).count("critical")} for n in snap["nodes"]]
        summary = next((h["summary"] for h in history if h["id"] == snap["id"]), {})
        principals = sorted(({"name": p, "type": v["type"], "label": acl.display_name(p)}
                             for p, v in snap["principals"].items()), key=lambda x: x["label"].lower())
        return {"snapshot": {"id": snap["id"], "created_at": snap["created_at"], "platform": snap["platform"],
                             "depth": snap["depth"], "root": share["path"]},
                "summary": summary, "findings": findings, "nodes": nodes, "history": history[:30],
                "principals": principals, "platform": snap["platform"]}

    @app.get("/api/shares/{share_id}/acl/node")
    def acl_node(share_id: int, path: str = "", snapshot_id: Optional[int] = None, user=Depends(viewer)):
        snap = snapshot_or_404(share_id, snapshot_id)
        node = next((n for n in snap["nodes"] if n["path"] == path), None)
        if not node:
            raise HTTPException(404, "Dossier absent de l'analyse (profondeur ?)")
        aces = [{**a, "label": acl.display_name(a["principal"]), "level_label": acl.LEVEL_LABEL.get(a["level"]),
                 "scope_label": acl.SCOPE_LABEL.get(a["scope"]), "ptype_label": acl.PTYPE_LABEL.get(a["ptype"]),
                 "broad": acl.is_broad(a["principal"], a.get("sid")), "admin": acl.is_admin(a["principal"], a.get("sid"))}
                for a in node["aces"]]
        eff = acl.effective_levels(node)
        return {"path": path, "owner": node.get("owner"), "protected": node.get("protected"), "aces": aces,
                "effective": [{"principal": p, "label": acl.display_name(p), "level": lv,
                               "level_label": acl.LEVEL_LABEL[lv]} for p, lv in sorted(eff.items(), key=lambda kv: -acl.LEVEL_RANK[kv[1]])],
                "findings": [f for f in rights.findings(snap["id"]) if f["path"] == path]}

    @app.get("/api/shares/{share_id}/acl/diff")
    def acl_diff(share_id: int, a: Optional[int] = None, b: Optional[int] = None, user=Depends(viewer)):
        hist = rights.history(share_id)
        if len(hist) < 2 and not (a and b):
            return {"changes": [], "created_folders": 0, "deleted_folders": 0, "from": None, "to": None}
        new = rights.latest(share_id, b or hist[0]["id"])
        old = rights.latest(share_id, a or hist[1]["id"])
        return {**acl.diff_snapshots(old, new), "from": old["created_at"], "to": new["created_at"]}

    @app.get("/api/shares/{share_id}/acl/principal")
    def acl_principal(share_id: int, name: str, user=Depends(viewer)):
        snap = snapshot_or_404(share_id)
        try:
            groups = acl.groups_of(name)
        except system.CommandError:
            groups = []
        return {"principal": name, "groups": groups, "access": acl.effective_access(snap, name, groups)}

    @app.get("/api/acl/who")
    def acl_who(principal: str, user=Depends(viewer)):
        """Tout ce à quoi une identité a accès, sur tous les dossiers supervisés."""
        try:
            groups = acl.groups_of(principal)
        except system.CommandError:
            groups = []
        out = []
        for s in engine.shares():
            snap = rights.latest(s["id"])
            if not snap:
                continue
            rows = acl.effective_access(snap, principal, groups)
            if rows:
                out.append({"share_id": s["id"], "share": s["name"], "access": rows})
        return {"principal": principal, "groups": groups, "shares": out}

    @app.get("/api/acl/members")
    def acl_members(group: str, user=Depends(viewer)):
        try:
            return {"group": group, "members": acl.members_of(group)}
        except system.CommandError as exc:
            return {"group": group, "members": [], "error": str(exc)}

    @app.get("/api/acl/principals")
    def acl_principals(user=Depends(viewer)):
        seen = {}
        for s in engine.shares():
            snap = rights.latest(s["id"])
            for p, v in (snap or {}).get("principals", {}).items():
                e = seen.setdefault(p, {"name": p, "label": acl.display_name(p), "type": v["type"], "shares": 0})
                e["shares"] += 1
        return sorted(seen.values(), key=lambda x: x["label"].lower())

    @app.post("/api/shares/{share_id}/acl/ops")
    def acl_ops(share_id: int, body: AclOpsIn, user=Depends(admin)):
        share = get_share(share_id)
        ops = [o.model_dump() for o in body.ops]
        for o in ops:
            resolve(share, o["path"])
            if o["op"] in ("grant", "revoke", "remove_deny", "owner") and not o.get("principal"):
                raise HTTPException(400, "Identité manquante")
            if o["op"] == "grant" and not o.get("level"):
                raise HTTPException(400, "Niveau manquant")
        actor = user["username"]

        def run(ctx):
            res = rights.apply_ops(share, ops, actor, backup=True, label="modif")
            engine.acl_scan(share, ctx)
            return res

        return {"job_id": jobs.submit("rights_ops", run, actor, share=share, exclusive=False, params={"ops": ops})}

    @app.get("/api/shares/{share_id}/acl/backups")
    def acl_backups(share_id: int, user=Depends(viewer)):
        get_share(share_id)
        return rights.backups(share_id)

    @app.post("/api/shares/{share_id}/acl/restore")
    def acl_restore(share_id: int, body: RestoreIn, user=Depends(admin)):
        share = get_share(share_id)
        path = os.path.join(rights.backup_dir, os.path.basename(body.name))
        if not os.path.isfile(path) or not body.name.startswith(f"{share_id}-"):
            raise HTTPException(404, "Sauvegarde introuvable")
        actor = user["username"]

        def run(ctx):
            rights.restore(share, path, actor)
            return engine.acl_scan(share, ctx)

        return {"job_id": jobs.submit("acl_restore", run, actor, share=share, exclusive=False)}

    @app.get("/api/shares/{share_id}/acl/export.csv")
    def acl_export(share_id: int, user=Depends(viewer)):
        share = get_share(share_id)
        snap = snapshot_or_404(share_id)
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["dossier", "niveau", "heritage_desactive", "proprietaire", "identite", "type", "autorisation",
                    "droit", "portee", "herite", "droits_bruts"])
        for n in snap["nodes"]:
            for a in n["aces"] or [{}]:
                w.writerow([n["path"] or "(racine)", n["depth"], "oui" if n.get("protected") else "non",
                            acl.display_name(n.get("owner") or ""), acl.display_name(a.get("principal", "")),
                            acl.PTYPE_LABEL.get(a.get("ptype"), ""), "" if not a else ("Autoriser" if a["allow"] else "Refuser"),
                            acl.LEVEL_LABEL.get(a.get("level"), ""), acl.SCOPE_LABEL.get(a.get("scope"), ""),
                            "" if not a else ("oui" if a["inherited"] else "non"), a.get("raw", "")])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="droits-{share["name"]}.csv"'})

    # ================================================================ matrices
    def matrix_row(mid):
        m = db.query_one("SELECT * FROM matrices WHERE id=?", (mid,))
        if not m:
            raise HTTPException(404, "Matrice inconnue")
        m["data"] = json.loads(m["data"])
        return m

    @app.get("/api/shares/{share_id}/matrices")
    def list_matrices(share_id: int, user=Depends(viewer)):
        get_share(share_id)
        return db.query("SELECT id, name, kind, status, created_at, updated_at, created_by, applied_at, applied_by "
                        "FROM matrices WHERE share_id=? ORDER BY updated_at DESC", (share_id,))

    @app.post("/api/shares/{share_id}/matrices")
    def create_matrix(share_id: int, body: MatrixCreateIn, user=Depends(operator)):
        share = get_share(share_id)
        snap = snapshot_or_404(share_id)
        depth = body.depth if body.depth is not None else int(share.get("matrix_depth") or 2)
        depth = min(depth, snap["depth"])
        client = db.query_one("SELECT name FROM clients WHERE id=?", (share["client_id"],))
        if body.kind == "proposal":
            prefix = merged_setting(db, "rights").get("group_prefix") or "GDL"
            data = mx.propose(snap, depth, client["name"] if client else "", share["name"], prefix)
            name = body.name or "Proposition AGDLP"
        else:
            data = mx.build_current(snap, depth)
            if body.kind == "empty":
                data["cells"] = {k: v for k, v in data["cells"].items() if acl.is_admin(k.split(mx.SEP, 1)[1])}
                data["columns"] = [col for col in data["columns"] if col["system"]]
            name = body.name or ("Droits actuels" if body.kind == "current" else "Matrice vierge")
        data["options"]["groups_ou"] = merged_setting(db, "rights").get("groups_ou", "")
        now = time.time()
        mid = db.execute("INSERT INTO matrices(share_id, name, kind, status, data, baseline_snapshot, created_at, updated_at, "
                         "created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                         (share_id, name, body.kind, "draft", json.dumps(data), snap["id"], now, now, user["username"]))
        db.audit(user["username"], "création matrice de droits", share["path"], name)
        return {"id": mid}

    @app.get("/api/matrices/{mid}")
    def get_matrix(mid: int, user=Depends(viewer)):
        m = matrix_row(mid)
        snap = rights.latest(m["share_id"])
        m["plan"] = mx.plan(m["data"], snap) if snap else None
        m["levels"] = acl.LEVEL_LABEL
        m["snapshot_at"] = snap["created_at"] if snap else None
        m["principals"] = sorted(({"name": p, "type": v["type"], "label": acl.display_name(p)}
                                  for p, v in (snap or {}).get("principals", {}).items()), key=lambda x: x["label"].lower())
        return m

    @app.put("/api/matrices/{mid}")
    def save_matrix(mid: int, body: MatrixSaveIn, user=Depends(operator)):
        m = matrix_row(mid)
        data = mx.validate(body.data) if body.data is not None else m["data"]
        db.execute("UPDATE matrices SET name=?, data=?, updated_at=?, status=CASE WHEN status='applied' THEN 'modified' "
                   "ELSE status END WHERE id=?", (body.name or m["name"], json.dumps(data), time.time(), mid))
        return {"ok": True}

    @app.delete("/api/matrices/{mid}")
    def delete_matrix(mid: int, user=Depends(operator)):
        m = matrix_row(mid)
        db.execute("DELETE FROM matrices WHERE id=?", (mid,))
        db.audit(user["username"], "suppression matrice", m["name"])
        return {"ok": True}

    @app.post("/api/matrices/{mid}/duplicate")
    def duplicate_matrix(mid: int, user=Depends(operator)):
        m = matrix_row(mid)
        now = time.time()
        new = db.execute("INSERT INTO matrices(share_id, name, kind, status, data, baseline_snapshot, created_at, updated_at, "
                         "created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                         (m["share_id"], m["name"] + " (copie)", m["kind"], "draft", json.dumps(m["data"]),
                          m["baseline_snapshot"], now, now, user["username"]))
        return {"id": new}

    @app.get("/api/matrices/{mid}/script")
    def matrix_script(mid: int, user=Depends(operator)):
        m = matrix_row(mid)
        share = get_share(m["share_id"])
        snap = snapshot_or_404(m["share_id"])
        text = mx.render_script(m["data"], mx.plan(m["data"], snap), share["path"], share["name"], user["username"])
        ext = "ps1" if m["data"].get("platform") == "windows" else "sh"
        return Response(("﻿" if ext == "ps1" else "") + text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="matrice-{mid}.{ext}"'})

    @app.get("/api/matrices/{mid}/export.csv")
    def matrix_csv(mid: int, user=Depends(viewer)):
        m = matrix_row(mid)
        return Response(mx.export_csv(m["data"]), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="matrice-{mid}.csv"'})

    @app.get("/api/matrices/{mid}/export.xlsx")
    def matrix_xlsx(mid: int, user=Depends(viewer)):
        m = matrix_row(mid)
        share = get_share(m["share_id"])
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        mx.export_xlsx(m["data"], path, share["name"])
        return FileResponse(path, filename=f"matrice-{share['name']}-{mid}.xlsx",
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.post("/api/matrices/{mid}/import")
    async def matrix_import(mid: int, request: Request, user=Depends(operator)):
        m = matrix_row(mid)
        raw = (await request.body()).decode("utf-8-sig", "replace")
        try:
            data = mx.import_csv(raw, m["data"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        db.execute("UPDATE matrices SET data=?, updated_at=? WHERE id=?", (json.dumps(data), time.time(), mid))
        db.audit(user["username"], "import matrice CSV", m["name"])
        return {"ok": True, "rows": len(data["rows"]), "columns": len(data["columns"])}

    @app.post("/api/matrices/{mid}/apply")
    def matrix_apply(mid: int, body: MatrixApplyIn, user=Depends(admin)):
        if not body.confirm:
            raise HTTPException(400, "Confirmation requise")
        m = matrix_row(mid)
        share = get_share(m["share_id"])
        snap = snapshot_or_404(m["share_id"])
        actor = user["username"]

        def run(ctx):
            ctx.progress(phase="sauvegarde et application", force=True)
            data = m["data"]
            created = []
            if data.get("options", {}).get("create_groups"):
                created = create_groups(data)
            p = mx.plan(data, snap)
            res = rights.apply_ops(share, p["ops"], actor, backup=True, label=f"matrice-{mid}")
            db.execute("UPDATE matrices SET status='applied', applied_at=?, applied_by=?, backup_file=? WHERE id=?",
                       (time.time(), actor, res["backup"], mid))
            db.execute("UPDATE matrices SET status='superseded' WHERE share_id=? AND id!=? AND status='applied'",
                       (share["id"], mid))
            ctx.progress(phase="nouvelle analyse des droits", force=True)
            scan = engine.acl_scan(share, ctx)
            return {**res, "groups_created": created, "warnings": p["warnings"], "remaining_gaps": scan.get("matrix_gaps")}

        return {"job_id": jobs.submit("matrix_apply", run, actor, share=share, exclusive=False, params={"matrix": mid})}

    def create_groups(data):
        groups = [g for g in data.get("groups_plan", []) if g.get("create", True)]
        done = []
        if system.IS_WINDOWS:
            ou = data.get("options", {}).get("groups_ou")
            for g in groups:
                name = system.ps_quote(g["group"])
                members = [m.split("\\")[-1] for m in g.get("members", []) if not acl.is_broad(m)]
                script = (f"Import-Module ActiveDirectory; if (-not (Get-ADGroup -Filter \"Name -eq {name}\")) {{ New-ADGroup -Name {name} "
                          f"-GroupScope DomainLocal -GroupCategory Security" + (f" -Path {system.ps_quote(ou)}" if ou else "") + " }; "
                          + (f"Add-ADGroupMember -Identity {name} -Members " + ",".join(system.ps_quote(x) for x in members) + "; " if members else "")
                          + "'{}'")
                try:
                    system.powershell(script, timeout=120)
                    done.append(g["group"])
                except system.CommandError:
                    continue
        else:
            for g in groups:
                name = g["group"].split(":", 1)[-1]
                try:
                    system.run(["groupadd", "-f", name], timeout=30)
                    for m in g.get("members", []):
                        if m.startswith("user:"):
                            system.run(["gpasswd", "-a", m[5:], name], timeout=30, check=False)
                    done.append(g["group"])
                except system.CommandError:
                    continue
        return done

    # ================================================================ architecture
    def latest_report(share_id):
        row = db.query_one("SELECT report FROM scans WHERE share_id=? AND status='done' ORDER BY finished_at DESC LIMIT 1",
                           (share_id,))
        return json.loads(row["report"]) if row else None

    def architecture_of(share):
        report = latest_report(share["id"])
        stats = db.query("SELECT * FROM folder_stats WHERE share_id=?", (share["id"],))
        snap_row = db.query_one("SELECT id, summary, findings FROM acl_snapshots WHERE share_id=? ORDER BY created_at DESC "
                                "LIMIT 1", (share["id"],))
        snap = rights.latest(share["id"]) if snap_row else None
        return structure.analyze(share, report, stats, snap, json.loads(snap_row["summary"]) if snap_row else None,
                                 json.loads(snap_row["findings"]) if snap_row else None), report

    @app.get("/api/shares/{share_id}/architecture")
    def architecture(share_id: int, user=Depends(viewer)):
        share = get_share(share_id)
        res, report = architecture_of(share)
        res["scanned"] = bool(report)
        return res

    @app.get("/api/architecture")
    def architecture_overview(user=Depends(viewer)):
        out = []
        for s in engine.shares():
            res, report = architecture_of(s)
            client = db.query_one("SELECT name FROM clients WHERE id=?", (s["client_id"],))
            out.append({"id": s["id"], "name": s["name"], "client_name": client["name"] if client else "",
                        "scanned": bool(report), "score": res["score"], "grade": res["grade"],
                        "categories": res["categories"], "metrics": res["metrics"],
                        "top": res["recommendations"][:2]})
        return out

    @app.get("/api/shares/{share_id}/paths.csv")
    def paths_csv(share_id: int, user=Depends(viewer)):
        share = get_share(share_id)
        report = latest_report(share_id) or {}
        paths = report.get("paths") or {}
        import csv
        import io
        from .pathcheck import ISSUE_LABEL
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["type", "chemin", "longueur_ou_probleme", "nature"])
        for i in paths.get("longest", []):
            w.writerow(["chemin long", i["path"], i["length"], i["kind"]])
        for i in paths.get("naming", {}).get("samples", []):
            w.writerow(["nommage", i["path"], ISSUE_LABEL.get(i["code"], i["code"]), i["kind"]])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="chemins-{share["name"]}.csv"'})

    # ================================================================ quotas
    @app.get("/api/quotas")
    def list_quotas(share_id: Optional[int] = None, user=Depends(viewer)):
        rows = quotas.list(share_id)
        for q in rows:
            q["effective_enforcement"] = quotas.enforcement_of(q)
            q["locks"] = db.query("SELECT rel_path, locked_at, principals FROM quota_locks WHERE quota_id=? AND "
                                  "unlocked_at IS NULL", (q["id"],))
        return {"quotas": rows, "fsrm": system.IS_WINDOWS and system.capabilities()["fsrm"],
                "platform": platform_name(), "settings": quotas.settings()}

    def _quota_values(body):
        share = get_share(body.share_id)
        rel = body.rel_path.replace("\\", "/").strip("/")
        target = resolve(share, rel)
        if not os.path.isdir(target):
            raise HTTPException(400, "Dossier introuvable dans le dossier supervisé")
        if body.crit_pct < body.warn_pct:
            raise HTTPException(400, "Le seuil critique doit être supérieur ou égal au seuil d'avertissement")
        if body.enforcement == "fsrm" and not (system.IS_WINDOWS and system.capabilities()["fsrm"]):
            raise HTTPException(400, "FSRM n'est pas disponible sur ce serveur : choisissez « auto » ou « VigieFS ».")
        return share, rel

    @app.post("/api/quotas")
    def create_quota(body: QuotaIn, user=Depends(operator)):
        share, rel = _quota_values(body)
        if db.query_one("SELECT id FROM quotas WHERE share_id=? AND rel_path=?", (share["id"], rel)):
            raise HTTPException(409, "Un quota existe déjà sur ce dossier")
        now = time.time()
        qid = db.execute("INSERT INTO quotas(share_id, rel_path, limit_bytes, warn_pct, crit_pct, mode, enforcement, "
                         "auto_apply, template, notes, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (share["id"], rel, body.limit_bytes, body.warn_pct, body.crit_pct, body.mode, body.enforcement,
                          int(body.auto_apply), body.template, body.notes, now, now))
        db.audit(user["username"], "création quota", f"{share['path']}/{rel}", f"{body.limit_bytes} o — {body.mode}")
        q = quotas.get(qid)
        return {"id": qid, "state": _push_and_check(q, user)}

    def _push_and_check(q, user):
        if quotas.enforcement_of(q) == "fsrm" and q["auto_apply"]:
            try:
                quotas.push_fsrm(q)
            except system.CommandError as exc:
                return {"error": str(exc)}
        return quotas.check(q, user["username"])

    @app.put("/api/quotas/{qid}")
    def update_quota(qid: int, body: QuotaIn, user=Depends(operator)):
        q = quotas.get(qid)
        if not q:
            raise HTTPException(404, "Quota inconnu")
        share, rel = _quota_values(body)
        db.execute("UPDATE quotas SET rel_path=?, limit_bytes=?, warn_pct=?, crit_pct=?, mode=?, enforcement=?, auto_apply=?, "
                   "template=?, notes=?, updated_at=? WHERE id=?",
                   (rel, body.limit_bytes, body.warn_pct, body.crit_pct, body.mode, body.enforcement, int(body.auto_apply),
                    body.template, body.notes, time.time(), qid))
        db.audit(user["username"], "modification quota", f"{share['path']}/{rel}")
        return {"ok": True, "state": _push_and_check(quotas.get(qid), user)}

    @app.delete("/api/quotas/{qid}")
    def delete_quota(qid: int, user=Depends(operator)):
        q = quotas.get(qid)
        if not q:
            raise HTTPException(404, "Quota inconnu")
        quotas.remove(q, user["username"])
        db.audit(user["username"], "suppression quota", f"{q['share_path']}/{q['rel_path']}")
        return {"ok": True}

    @app.post("/api/quotas/{qid}/check")
    def check_quota(qid: int, user=Depends(operator)):
        q = quotas.get(qid)
        if not q:
            raise HTTPException(404, "Quota inconnu")
        return quotas.check(q, user["username"])

    @app.post("/api/quotas/{qid}/unlock")
    def unlock_quota(qid: int, user=Depends(admin)):
        n = 0
        for lock in db.query("SELECT * FROM quota_locks WHERE quota_id=? AND unlocked_at IS NULL", (qid,)):
            quotas.unlock(lock, user["username"])
            n += 1
        return {"unlocked": n}

    @app.get("/api/quotas/{qid}/fsrm-script")
    def quota_fsrm_script(qid: int, user=Depends(operator)):
        q = quotas.get(qid)
        if not q:
            raise HTTPException(404, "Quota inconnu")
        return PlainTextResponse(quotas.fsrm_commands(q))

    # ================================================================ SMB
    def _safe(fn, *a):
        try:
            return {"available": True, "items": fn(*a)}
        except system.CommandError as exc:
            return {"available": False, "items": [], "error": str(exc)}

    @app.get("/api/smb/shares")
    def smb_shares(user=Depends(viewer)):
        res = _safe(smb.shares)
        sup = engine.shares()
        for s in res["items"]:
            s["findings"] = smb.share_findings(s)
            p = (s.get("path") or "").rstrip("\\/").lower()
            s["supervised"] = [{"id": x["id"], "name": x["name"]} for x in sup
                               if p and x["path"].rstrip("\\/").lower().startswith(p)]
        return res

    @app.post("/api/smb/shares/{name}/abe")
    def smb_abe(name: str, body: AbeIn, user=Depends(admin)):
        try:
            smb.set_abe(name, body.enabled)
        except system.CommandError as exc:
            raise HTTPException(400, str(exc))
        db.audit(user["username"], "énumération basée sur l'accès", name, "activée" if body.enabled else "désactivée")
        return {"ok": True}

    @app.get("/api/smb/open-files")
    def smb_open(user=Depends(viewer)):
        return {"files": _safe(smb.open_files), "sessions": _safe(smb.sessions)}

    @app.post("/api/smb/open-files/{file_id}/close")
    def smb_close_file(file_id: str, user=Depends(operator)):
        try:
            smb.close_file(file_id)
        except (system.CommandError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        db.audit(user["username"], "fermeture fichier ouvert", file_id)
        return {"ok": True}

    @app.post("/api/smb/sessions/{session_id}/close")
    def smb_close_session(session_id: str, user=Depends(admin)):
        try:
            smb.close_session(session_id)
        except (system.CommandError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        db.audit(user["username"], "fermeture session SMB", session_id)
        return {"ok": True}

    # ================================================================ santé
    health_cache = {}

    @app.get("/api/health/server")
    def server_health(refresh: bool = False, user=Depends(viewer)):
        if refresh or not health_cache or time.time() - health_cache.get("at", 0) > 300:
            health_cache["data"] = health.run_checks(db, engine)
            health_cache["at"] = time.time()
        return health_cache["data"]

    # ================================================================ données sensibles
    @app.post("/api/shares/{share_id}/sensitive")
    def start_sensitive(share_id: int, user=Depends(operator)):
        share = get_share(share_id)
        cfg = merged_setting(db, "sensitive")

        def run(ctx):
            res = sensitive.scan_share(share, cfg, ctx, rights.latest(share_id), engine.skip_roots())
            fp = f"sensitive:{share_id}"
            if res["exposed_high"]:
                alerts.raise_alert(fp, "critical", "sensitive", "Données sensibles exposées",
                                   f"{res['exposed_high']} fichier(s) contenant des données sensibles (NIR, cartes, "
                                   f"identifiants, santé) sont lisibles par tout le monde dans « {share['name']} ».",
                                   share=share, details={"files": [r["path"] for r in res["results"] if r["exposed_to"]][:50]})
            elif res["high"]:
                alerts.raise_alert(fp, "warning", "sensitive", "Données sensibles détectées",
                                   f"{res['high']} fichier(s) contiennent des données sensibles dans « {share['name']} ».",
                                   share=share, details={"files": [r["path"] for r in res["results"]][:50]}, notify=False)
            else:
                alerts.resolve(fp)
            return res

        return {"job_id": jobs.submit("sensitive", run, user["username"], share=share)}

    @app.get("/api/shares/{share_id}/sensitive")
    def last_sensitive(share_id: int, user=Depends(viewer)):
        get_share(share_id)
        running = db.query_one("SELECT id FROM jobs WHERE type='sensitive' AND share_id=? AND status IN ('queued','running') "
                               "ORDER BY id DESC LIMIT 1", (share_id,))
        return {"running": jobs.get(running["id"]) if running else None, "last": jobs.latest("sensitive", share_id)}

    @app.get("/api/sensitive")
    def sensitive_overview(user=Depends(viewer)):
        out = []
        for s in engine.shares():
            job = jobs.latest("sensitive", s["id"])
            r = (job or {}).get("result") or {}
            out.append({"id": s["id"], "name": s["name"], "scanned_at": job["finished_at"] if job else None,
                        "scanned": r.get("scanned"), "flagged": r.get("flagged"), "high": r.get("high"),
                        "exposed_high": r.get("exposed_high"), "totals": r.get("totals", {})})
        return {"shares": out, "labels": {k: v[0] for k, v in sensitive.CATEGORIES.items()}}
