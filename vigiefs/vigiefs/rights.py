"""Service de gestion des droits : instantanés, audit, dérive, opérations unitaires, sauvegardes."""

import json
import os
import time
import zlib

from . import acl
from .rules import safe_join

KEEP_SNAPSHOTS = 30


def pack(obj):
    return zlib.compress(json.dumps(obj, default=str).encode(), 6)


def unpack(blob):
    return json.loads(zlib.decompress(blob).decode())


class RightsService:
    def __init__(self, db, alerts, data_dir):
        self.db = db
        self.alerts = alerts
        self.backup_dir = os.path.join(data_dir, "sauvegardes-acl")

    # ------------------------------------------------------------ instantanés
    def scan(self, share, ctx=None):
        depth = int(share.get("acl_depth") or 4)
        if ctx:
            ctx.progress(phase="lecture des droits", force=True)
        snap = acl.collect(share["path"], depth, cancel=(lambda: ctx.cancelled) if ctx else None)
        if ctx:
            ctx.check()
            ctx.progress(phase="audit", folders=len(snap["nodes"]), force=True)
        findings = acl.audit(snap, int(share.get("matrix_depth") or 2))
        summary = acl.summarize(snap, findings)
        prev = self.latest(share["id"])
        drift = acl.diff_snapshots(prev, snap) if prev else None
        summary["changes"] = len(drift["changes"]) if drift else 0
        sid = self.db.execute(
            "INSERT INTO acl_snapshots(share_id, created_at, platform, depth, summary, findings, data) VALUES(?,?,?,?,?,?,?)",
            (share["id"], snap["created_at"], snap["platform"], depth, json.dumps(summary),
             json.dumps(findings, default=str), pack(snap)))
        self.db.execute("UPDATE shares SET last_acl_at=? WHERE id=?", (time.time(), share["id"]))
        self.db.execute("DELETE FROM acl_snapshots WHERE share_id=? AND id NOT IN (SELECT id FROM acl_snapshots "
                        "WHERE share_id=? ORDER BY created_at DESC LIMIT ?)", (share["id"], share["id"], KEEP_SNAPSHOTS))
        self._alerts(share, summary, findings, drift)
        return {"snapshot_id": sid, "summary": summary, "drift": drift}

    def _alerts(self, share, summary, findings, drift):
        crit = [f for f in findings if f["severity"] == "critical"]
        fp = f"acl-critical:{share['id']}"
        if crit:
            self.alerts.raise_alert(
                fp, "critical", "permissions", "Droits d'accès dangereux",
                f"{len(crit)} problème(s) critique(s) de droits sur « {share['name']} » "
                f"(ex. {crit[0]['title'].lower()} sur « {crit[0]['path'] or '(racine)'} »).",
                share=share, details={"findings": crit[:50]})
        else:
            self.alerts.resolve(fp)
        if drift and drift["changes"]:
            self.alerts.raise_alert(
                f"acl-drift:{share['id']}", "info", "permissions", "Droits modifiés",
                f"{len(drift['changes'])} modification(s) de droits détectée(s) sur « {share['name']} » depuis "
                "la dernière analyse.", share=share, details={"changes": drift["changes"][:100]}, notify=False)

    def latest(self, share_id, snapshot_id=None):
        row = self.db.query_one(
            "SELECT id, data FROM acl_snapshots WHERE share_id=? " + ("AND id=? " if snapshot_id else "")
            + "ORDER BY created_at DESC LIMIT 1", (share_id, snapshot_id) if snapshot_id else (share_id,))
        if not row:
            return None
        snap = unpack(row["data"])
        snap["id"] = row["id"]
        return snap

    def history(self, share_id):
        rows = self.db.query("SELECT id, created_at, platform, depth, summary FROM acl_snapshots WHERE share_id=? "
                             "ORDER BY created_at DESC", (share_id,))
        for r in rows:
            r["summary"] = json.loads(r["summary"])
        return rows

    def findings(self, snapshot_row_id):
        row = self.db.query_one("SELECT findings FROM acl_snapshots WHERE id=?", (snapshot_row_id,))
        return json.loads(row["findings"]) if row else []

    # ------------------------------------------------------------ opérations
    def backup(self, share, rel="", label="manuel"):
        os.makedirs(self.backup_dir, exist_ok=True)
        target = safe_join(share["path"], rel)
        platform = "windows" if acl.system.IS_WINDOWS else "posix"
        name = f"{share['id']}-{time.strftime('%Y%m%d-%H%M%S')}-{label}.acl"
        path = os.path.join(self.backup_dir, name)
        acl.backup(target, path, platform)
        return path

    def apply_ops(self, share, ops, actor, backup=True, label="operation"):
        """Exécute une liste d'opérations [{op, path, principal, level…}] avec sauvegarde préalable."""
        platform = "windows" if acl.system.IS_WINDOWS else "posix"
        backup_file = self.backup(share, "", label) if backup else None
        results = []
        for op in ops:
            target = safe_join(share["path"], op.get("path", ""))
            try:
                if op["op"] == "mkdir":
                    os.makedirs(target, exist_ok=True)
                else:
                    acl.execute(op, target, platform)
                results.append({**op, "ok": True})
            except Exception as exc:  # noqa: BLE001
                results.append({**op, "ok": False, "error": str(exc)})
        ok = sum(1 for r in results if r["ok"])
        self.db.audit(actor, "modification des droits", share["path"],
                      f"{ok}/{len(results)} opération(s) — sauvegarde : {os.path.basename(backup_file) if backup_file else 'aucune'}")
        return {"results": results, "ok": ok, "failed": len(results) - ok, "backup": backup_file}

    def restore(self, share, backup_file, actor):
        platform = "windows" if acl.system.IS_WINDOWS else "posix"
        real = os.path.realpath(backup_file)
        if os.path.commonpath([real, os.path.realpath(self.backup_dir)]) != os.path.realpath(self.backup_dir):
            raise ValueError("Sauvegarde inconnue")
        acl.restore(share["path"], real, platform)
        self.db.audit(actor, "restauration des droits", share["path"], os.path.basename(real))

    def backups(self, share_id):
        if not os.path.isdir(self.backup_dir):
            return []
        out = []
        for f in sorted(os.listdir(self.backup_dir), reverse=True):
            if f.startswith(f"{share_id}-"):
                p = os.path.join(self.backup_dir, f)
                out.append({"name": f, "size": os.path.getsize(p), "created_at": os.path.getmtime(p)})
        return out
