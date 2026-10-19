"""Quotas avancés : par sous-dossier, modèles, quotas automatiques, application stricte.

Application d'un quota strict, au choix :
- « fsrm »    : quota natif du Gestionnaire de ressources du serveur de fichiers (Windows) — blocage
                immédiat des écritures par le système (New-FsrmQuota / New-FsrmAutoQuota) ;
- « vigiefs » : verrouillage par VigieFS dès le dépassement constaté. Sous Windows, une entrée de refus
                « créer fichiers / ajouter données » est posée pour chaque compte non administrateur ayant
                l'écriture : les utilisateurs peuvent encore lire et SUPPRIMER pour libérer de la place.
                Sous Linux, le masque ACL passe en lecture seule. Le verrou est levé automatiquement
                quand l'occupation repasse sous le seuil de déverrouillage ;
- « auto »    : FSRM s'il est disponible, sinon verrouillage VigieFS.
"""

import json
import os
import time

from . import acl, system
from .config import merged_setting
from .rules import long_path, safe_join


def dir_size(path, cancel=None):
    total, stack = 0, [long_path(path)]
    while stack:
        if cancel and cancel():
            break
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def level_of(pct, q):
    if pct is None:
        return "unknown"
    if pct >= 100:
        return "exceeded"
    if pct >= q["crit_pct"]:
        return "critical"
    if pct >= q["warn_pct"]:
        return "warning"
    return "ok"


def fmt_gb(n):
    return f"{n / 1024 ** 3:.2f} Go".replace(".", ",")


class QuotaService:
    def __init__(self, db, alerts, rights):
        self.db = db
        self.alerts = alerts
        self.rights = rights

    # ------------------------------------------------------------ données
    def settings(self):
        return merged_setting(self.db, "quotas")

    def get(self, qid):
        q = self.db.query_one("SELECT q.*, s.name AS share_name, s.path AS share_path, s.client_id FROM quotas q "
                              "JOIN shares s ON s.id=q.share_id WHERE q.id=?", (qid,))
        if q:
            q["state"] = json.loads(q["state"] or "{}")
        return q

    def list(self, share_id=None):
        sql = ("SELECT q.*, s.name AS share_name, s.path AS share_path, s.client_id, c.name AS client_name "
               "FROM quotas q JOIN shares s ON s.id=q.share_id JOIN clients c ON c.id=s.client_id")
        rows = self.db.query(sql + (" WHERE q.share_id=?" if share_id else "") + " ORDER BY c.name, s.name, q.rel_path",
                             (share_id,) if share_id else ())
        for r in rows:
            r["state"] = json.loads(r["state"] or "{}")
        return rows

    def enforcement_of(self, q):
        if q["mode"] != "hard" or q["enforcement"] == "none":
            return "none"
        if q["enforcement"] == "auto":
            return "fsrm" if system.IS_WINDOWS and system.capabilities()["fsrm"] else "vigiefs"
        return q["enforcement"]

    # ------------------------------------------------------------ FSRM
    def _fsrm_script(self, q, path, remove=False):
        soft = "$true" if q["mode"] != "hard" else "$false"
        th = (f"$th = @(); foreach ($pc in @({int(q['warn_pct'])},{int(q['crit_pct'])},100)) {{ "
              "$a = New-FsrmAction -Type Event -EventType Warning -Body \"VigieFS : [Quota Threshold] % du quota "
              "atteint sur [Quota Path] ([Quota Used MB] Mo / [Quota Limit MB] Mo)\"; "
              "$th += New-FsrmQuotaThreshold -Percentage $pc -Action $a }")
        p = system.ps_quote(path)
        if q["auto_apply"]:
            tn = system.ps_quote(f"VigieFS - quota {q['id']}")
            if remove:
                return (f"Remove-FsrmAutoQuota -Path {p} -Confirm:$false -ErrorAction SilentlyContinue; "
                        f"Remove-FsrmQuotaTemplate -Name {tn} -Confirm:$false -ErrorAction SilentlyContinue; '{{}}'")
            return (f"{th}\nif (Get-FsrmQuotaTemplate -Name {tn} -ErrorAction SilentlyContinue) {{ Set-FsrmQuotaTemplate "
                    f"-Name {tn} -Size {int(q['limit_bytes'])} -SoftLimit:{soft} -Threshold $th -UpdateDerived }} else {{ "
                    f"New-FsrmQuotaTemplate -Name {tn} -Size {int(q['limit_bytes'])} -SoftLimit:{soft} -Threshold $th | Out-Null }}\n"
                    f"if (Get-FsrmAutoQuota -Path {p} -ErrorAction SilentlyContinue) {{ Set-FsrmAutoQuota -Path {p} "
                    f"-Template {tn} -UpdateDerived | Out-Null }} else {{ New-FsrmAutoQuota -Path {p} -Template {tn} | Out-Null }}\n"
                    "'{\"ok\":true}'")
        if remove:
            return f"Remove-FsrmQuota -Path {p} -Confirm:$false -ErrorAction SilentlyContinue; '{{}}'"
        return (f"{th}\nif (Get-FsrmQuota -Path {p} -ErrorAction SilentlyContinue) {{ Set-FsrmQuota -Path {p} "
                f"-Size {int(q['limit_bytes'])} -SoftLimit:{soft} -Threshold $th | Out-Null }} else {{ New-FsrmQuota -Path {p} "
                f"-Size {int(q['limit_bytes'])} -SoftLimit:{soft} -Threshold $th -Description 'VigieFS' | Out-Null }}\n"
                f"Get-FsrmQuota -Path {p} | Select-Object Usage, Size, SoftLimit, PeakUsage | ConvertTo-Json -Compress")

    def push_fsrm(self, q, remove=False):
        path = safe_join(q["share_path"], q["rel_path"])
        return system.powershell(self._fsrm_script(q, path, remove), timeout=300)

    def fsrm_commands(self, q):
        """Script affiché à l'administrateur (exécution manuelle ou vérification)."""
        return self._fsrm_script(q, safe_join(q["share_path"], q["rel_path"]))

    # ------------------------------------------------------------ verrouillage VigieFS
    def lock(self, q, rel_path, actor="système"):
        share = {"id": q["share_id"], "path": q["share_path"], "name": q["share_name"]}
        target = safe_join(q["share_path"], rel_path)
        platform = "windows" if system.IS_WINDOWS else "posix"
        principals = []
        if platform == "windows":
            snap = acl.collect(target, 0)
            node = snap["nodes"][0] if snap["nodes"] else {"aces": []}
            for p, lv in acl.effective_levels(node).items():
                if acl.LEVEL_RANK[lv] >= acl.LEVEL_RANK["write"] and not acl.is_admin(p):
                    principals.append(p)
            ops = [{"op": "deny_write", "path": rel_path, "principal": p} for p in principals]
        else:
            ops = [{"op": "deny_write", "path": rel_path}]
        if not ops:
            return False
        res = self.rights.apply_ops(share, ops, actor, backup=False, label="verrou-quota")
        self.db.execute("INSERT INTO quota_locks(quota_id, share_id, rel_path, principals, platform, locked_at) "
                        "VALUES(?,?,?,?,?,?)", (q["id"], q["share_id"], rel_path, json.dumps(principals), platform, time.time()))
        self.db.audit(actor, "verrouillage quota strict", target, ", ".join(principals) or "masque ACL lecture seule")
        return res["ok"] > 0

    def unlock(self, lock_row, actor="système"):
        share = self.db.query_one("SELECT * FROM shares WHERE id=?", (lock_row["share_id"],))
        if not share:
            return False
        principals = json.loads(lock_row["principals"] or "[]")
        ops = ([{"op": "remove_deny", "path": lock_row["rel_path"], "principal": p} for p in principals]
               if lock_row["platform"] == "windows" else [{"op": "remove_deny", "path": lock_row["rel_path"]}])
        self.rights.apply_ops(share, ops, actor, backup=False, label="deverrou-quota")
        self.db.execute("UPDATE quota_locks SET unlocked_at=? WHERE id=?", (time.time(), lock_row["id"]))
        self.db.audit(actor, "déverrouillage quota", safe_join(share["path"], lock_row["rel_path"]))
        return True

    def active_lock(self, qid, rel_path):
        return self.db.query_one("SELECT * FROM quota_locks WHERE quota_id=? AND rel_path=? AND unlocked_at IS NULL",
                                 (qid, rel_path))

    # ------------------------------------------------------------ contrôle
    def targets(self, q):
        """Chemins contrôlés : le dossier, ou chacun de ses sous-dossiers directs (quota automatique)."""
        if not q["auto_apply"]:
            return [q["rel_path"]]
        base = safe_join(q["share_path"], q["rel_path"])
        try:
            with os.scandir(base) as it:
                return sorted((q["rel_path"] + "/" if q["rel_path"] else "") + e.name
                              for e in it if e.is_dir(follow_symlinks=False))
        except OSError:
            return []

    def check(self, q, actor="système"):
        enforcement = self.enforcement_of(q)
        unlock_pct = float(self.settings().get("unlock_below_pct", 98))
        share = {"id": q["share_id"], "client_id": q["client_id"], "name": q["share_name"], "path": q["share_path"]}
        results = []
        fsrm = None
        if enforcement == "fsrm" and not q["auto_apply"]:
            try:
                fsrm = self.push_fsrm(q)
            except system.CommandError as exc:
                fsrm = {"error": str(exc)}
        for rel in self.targets(q):
            path = safe_join(q["share_path"], rel)
            if not os.path.isdir(path):
                results.append({"path": rel, "usage": None, "pct": None, "level": "unknown", "locked": False})
                continue
            usage = fsrm.get("Usage") if isinstance(fsrm, dict) and fsrm.get("Usage") is not None else dir_size(path)
            pct = usage * 100.0 / q["limit_bytes"] if q["limit_bytes"] else None
            level = level_of(pct, q)
            lock = self.active_lock(q["id"], rel)
            if enforcement == "vigiefs":
                if level == "exceeded" and not lock:
                    self.lock(q, rel, actor)
                    lock = True
                elif lock and pct is not None and pct < unlock_pct:
                    self.unlock(lock, actor)
                    lock = None
            results.append({"path": rel, "usage": usage, "pct": round(pct, 2) if pct is not None else None,
                            "level": level, "locked": bool(lock)})
            self._alert(q, share, rel, level, usage, pct, bool(lock), enforcement)
        worst = max(results, key=lambda r: r["pct"] or 0) if results else None
        state = {"checked_at": time.time(), "enforcement": enforcement, "items": results,
                 "usage": worst["usage"] if worst else None, "pct": worst["pct"] if worst else None,
                 "level": worst["level"] if worst else "unknown", "locked": any(r["locked"] for r in results),
                 "fsrm": fsrm}
        self.db.execute("UPDATE quotas SET state=? WHERE id=?", (json.dumps(state, default=str), q["id"]))
        return state

    def _alert(self, q, share, rel, level, usage, pct, locked, enforcement):
        fp = f"quota-adv:{q['id']}:{rel}"
        where = f"« {share['name']} / {rel or '(racine)'} »"
        if level in ("warning", "critical", "exceeded"):
            sev = "critical" if level in ("critical", "exceeded") else "warning"
            if level == "exceeded" and q["mode"] == "hard":
                title = "Quota strict atteint — écriture bloquée" if (locked or enforcement == "fsrm") else "Quota strict dépassé"
            else:
                title = "Quota dépassé" if level == "exceeded" else "Seuil de quota atteint"
            self.alerts.raise_alert(
                fp, sev, "quota", title,
                f"{where} utilise {fmt_gb(usage)} sur {fmt_gb(q['limit_bytes'])} ({f'{pct:.1f}'.replace('.', ',')} %)."
                + (" Les utilisateurs peuvent supprimer des fichiers mais plus en ajouter." if locked else ""),
                share=share, details={"quota_id": q["id"], "path": rel, "usage": usage, "limit": q["limit_bytes"],
                                      "mode": q["mode"], "enforcement": enforcement, "locked": locked})
        else:
            self.alerts.resolve(fp)

    def check_all(self, hard_only=False, actor="système"):
        out = []
        for q in self.list():
            if hard_only and q["mode"] != "hard":
                continue
            try:
                out.append(self.check(self.get(q["id"]), actor))
            except Exception as exc:  # noqa: BLE001
                out.append({"error": str(exc), "quota": q["id"]})
        return out

    def remove(self, q, actor):
        if self.enforcement_of(q) == "fsrm":
            try:
                self.push_fsrm(q, remove=True)
            except system.CommandError:
                pass
        for lock in self.db.query("SELECT * FROM quota_locks WHERE quota_id=? AND unlocked_at IS NULL", (q["id"],)):
            self.unlock(lock, actor)
        self.db.execute("DELETE FROM quotas WHERE id=?", (q["id"],))
        for item in (q.get("state") or {}).get("items", []):
            self.alerts.resolve(f"quota-adv:{q['id']}:{item['path']}")
