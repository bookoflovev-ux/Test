"""API : sécurité et utilisateurs (activité, anomalies, occupation, analyse des journaux, audit, réponses)."""

import csv
import io
import json
import time
from collections import Counter, defaultdict
from typing import List, Optional

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import auditsetup, response, system
from .activity import ACTION_LABEL, COUNTERS, user_key
from .config import merged_setting
from .ueba import DETECTOR_LABEL, risk_level
from .userstorage import capacity_of, check_capacity, totals

MAX_IMPORT = 300 * 1024 * 1024


class ProfileIn(BaseModel):
    watch: Optional[bool] = None
    service: Optional[bool] = None
    capacity_gb: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = Field(None, max_length=2000)


class ActionIn(BaseModel):
    action: str = Field(pattern="^(close_sessions|block|unblock|disable|enable)$")
    share_ids: List[int] = Field(default_factory=list)
    confirm: str = ""


class KeysIn(BaseModel):
    keys: List[str]


def register(app, c):
    db, engine, jobs = c["db"], c["engine"], c["jobs"]
    viewer, operator, admin = c["viewer"], c["operator"], c["admin"]
    sec = engine.security
    store = sec.store

    def shares_by_id():
        return {s["id"]: s for s in engine.shares()}

    def profile_or_404(u):
        key = user_key(u)
        prof = db.query_one("SELECT * FROM user_profiles WHERE user=?", (key,))
        if not prof and not db.query_one("SELECT 1 AS x FROM user_storage WHERE user=? LIMIT 1", (key,)):
            raise HTTPException(404, "Utilisateur inconnu")
        return key, prof or {"user": key, "display": key, "clients": "{}", "folders": "{}", "risk": 0, "watch": 0,
                             "service": 0, "capacity_bytes": 0, "notes": ""}

    def activity_sums(since_hour):
        rows = db.query(f"SELECT user, {', '.join(f'SUM({c}) AS {c}' for c in COUNTERS)}, SUM(total) AS total "
                        "FROM user_activity WHERE hour >= ? GROUP BY user", (since_hour,))
        return {r["user"]: r for r in rows}

    # ------------------------------------------------------------ vue d'ensemble
    @app.get("/api/security/overview")
    def overview(user=Depends(viewer)):
        now = time.time()
        h24, h48 = int((now - 86400) // 3600), int((now - 48 * 3600) // 3600)
        day = db.query_one(f"SELECT COUNT(DISTINCT user) AS users, SUM(total) AS total, "
                           f"{', '.join(f'SUM({c}) AS {c}' for c in COUNTERS)} FROM user_activity WHERE hour >= ?",
                           (h24,)) or {}
        hours = {r["hour"]: r for r in db.query(
            "SELECT hour, SUM(reads) AS reads, SUM(writes + creates + renames) AS writes, SUM(deletes) AS deletes, "
            "SUM(denied) AS denied, SUM(logon_fails) AS logon_fails, SUM(perms) AS perms, SUM(total) AS total "
            "FROM user_activity WHERE hour >= ? GROUP BY hour", (h48,))}
        timeline = [{"ts": h * 3600, **{k: (hours.get(h) or {}).get(k) or 0 for k in
                                        ("reads", "writes", "deletes", "denied", "logon_fails", "perms", "total")}}
                    for h in range(h48 + 1, int(now // 3600) + 1)]
        risky = db.query("SELECT user, display, risk, last_seen, watch FROM user_profiles WHERE risk > 0 "
                         "ORDER BY risk DESC LIMIT 12")
        for r in risky:
            last = db.query_one("SELECT title, severity, ts FROM user_findings WHERE user=? ORDER BY ts DESC LIMIT 1",
                                (r["user"],))
            r.update(level=risk_level(r["risk"]), last_finding=last)
        sums = activity_sums(h24)
        top = sorted(sums.values(), key=lambda r: -(r["total"] or 0))[:10]
        displays = {r["user"]: r["display"] for r in db.query("SELECT user, display FROM user_profiles")}
        for r in top:
            r["display"] = displays.get(r["user"], r["user"])
        findings = db.query("SELECT f.*, s.name AS share_name FROM user_findings f LEFT JOIN shares s ON s.id=f.share_id "
                            "ORDER BY f.ts DESC LIMIT 25")
        for f in findings:
            f["details"] = json.loads(f["details"] or "{}")
            f["label"] = DETECTOR_LABEL.get(f["detector"], f["detector"])
        sev7 = {r["severity"]: r["n"] for r in db.query(
            "SELECT severity, COUNT(*) AS n FROM user_findings WHERE ts > ? GROUP BY severity", (now - 7 * 86400,))}
        alerts_open = db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE status='open' AND category IN "
                                   "('behavior','security')")["n"]
        sources = db.query("SELECT * FROM log_sources ORDER BY name")
        ever = db.query_one("SELECT COUNT(*) AS n FROM user_events")["n"]
        storage = sorted(totals(db).values(), key=lambda r: -(r["size"] or 0))[:10]
        return {"day": day, "timeline": timeline, "risky": risky, "top_active": top, "findings": findings,
                "findings_7d": sev7, "alerts_open": alerts_open, "sources": sources, "events_total": ever,
                "platform": "windows" if system.IS_WINDOWS else "posix", "storage_top": storage,
                "at_risk": db.query_one("SELECT COUNT(*) AS n FROM user_profiles WHERE risk >= 40")["n"],
                "collector": {"alive": sec.alive(), "last_cycle": sec.last_cycle}}

    # ------------------------------------------------------------ utilisateurs
    @app.get("/api/security/users")
    def users_list(q: str = "", user=Depends(viewer)):
        now = time.time()
        d1, d7 = activity_sums(int((now - 86400) // 3600)), activity_sums(int((now - 7 * 86400) // 3600))
        storage = totals(db)
        cfg = merged_setting(db, "security")
        finds = {r["user"]: r["n"] for r in db.query(
            "SELECT user, COUNT(*) AS n FROM user_findings WHERE ts > ? GROUP BY user", (now - 30 * 86400,))}
        profiles = {r["user"]: r for r in db.query("SELECT * FROM user_profiles")}
        out = []
        for key in set(profiles) | set(storage):
            p = profiles.get(key) or {}
            st = storage.get(key) or {}
            display = p.get("display") or st.get("display") or key
            if q and q.lower() not in f"{key} {display}".lower():
                continue
            cap = capacity_of(p, cfg)
            out.append({"user": key, "display": display, "risk": p.get("risk") or 0,
                        "level": risk_level(p.get("risk") or 0), "first_seen": p.get("first_seen"),
                        "last_seen": p.get("last_seen"), "watch": bool(p.get("watch")), "service": bool(p.get("service")),
                        "clients": len(json.loads(p.get("clients") or "{}")), "day": d1.get(key), "week": d7.get(key),
                        "size": st.get("size") or 0, "files": st.get("files") or 0, "shares": st.get("shares") or 0,
                        "capacity": cap, "capacity_pct": round((st.get("size") or 0) * 100 / cap, 1) if cap else None,
                        "findings_30d": finds.get(key, 0)})
        out.sort(key=lambda r: (-r["risk"], -((r["week"] or {}).get("total") or 0), -r["size"]))
        return out

    @app.get("/api/security/user")
    def user_detail(u: str, user=Depends(viewer)):
        key, prof = profile_or_404(u)
        now = time.time()
        cfg = merged_setting(db, "security")
        shares = shares_by_id()
        storage = db.query("SELECT * FROM user_storage WHERE user=? ORDER BY size DESC", (key,))
        for s in storage:
            s["folders"], s["exts"] = json.loads(s["folders"] or "[]"), json.loads(s["exts"] or "[]")
            sh = shares.get(s["share_id"]) or {}
            s["share_name"], s["client_id"] = sh.get("name"), sh.get("client_id")
            s["share_total"] = sh.get("last_size")
        history = db.query("SELECT day, SUM(size) AS size, SUM(files) AS files FROM user_storage_history WHERE user=? "
                           "GROUP BY day ORDER BY day DESC LIMIT 120", (key,))[::-1]
        h0 = int((now - 7 * 86400) // 3600)
        hourly = {r["hour"]: r for r in db.query(
            f"SELECT hour, {', '.join(f'SUM({c}) AS {c}' for c in COUNTERS)}, SUM(total) AS total FROM user_activity "
            "WHERE user=? AND hour > ? GROUP BY hour", (key, h0))}
        week = [{"ts": h * 3600, **{k: (hourly.get(h) or {}).get(k) or 0 for k in COUNTERS + ["total"]}}
                for h in range(h0 + 1, int(now // 3600) + 1)]
        heat = [[0] * 24 for _ in range(7)]
        days = defaultdict(Counter)
        for r in db.query("SELECT hour, SUM(total) AS t, SUM(reads) AS r, SUM(writes + creates + renames) AS w, "
                          "SUM(deletes) AS d, SUM(denied) AS x FROM user_activity WHERE user=? AND hour > ? GROUP BY hour",
                          (key, int((now - 28 * 86400) // 3600))):
            lt = time.localtime(r["hour"] * 3600)
            heat[lt.tm_wday][lt.tm_hour] += r["t"]
            dk = time.strftime("%Y-%m-%d", lt)
            days[dk].update({"total": r["t"], "reads": r["r"], "writes": r["w"], "deletes": r["d"], "denied": r["x"]})
        daily = [{"day": k, **v} for k, v in sorted(days.items())]
        since30 = now - 30 * 86400
        folders = db.query(
            "SELECT share_id, CASE WHEN instr(path, '/') > 0 THEN substr(path, 1, instr(path, '/') - 1) ELSE '' END AS "
            "folder, COUNT(*) AS n, SUM(action IN ('read','open')) AS reads, SUM(action IN ('write','create','rename')) AS "
            "writes, SUM(action='delete') AS deletes, SUM(action='denied') AS denied, MAX(ts) AS last FROM user_events "
            "WHERE user=? AND ts > ? AND share_id IS NOT NULL GROUP BY share_id, folder ORDER BY n DESC LIMIT 25",
            (key, since30))
        for f in folders:
            f["share_name"] = (shares.get(f["share_id"]) or {}).get("name")
        files = db.query("SELECT share_id, path, COUNT(*) AS n, MAX(ts) AS last FROM user_events WHERE user=? AND ts > ? "
                         "AND action IN ('read','open','write') AND share_id IS NOT NULL GROUP BY share_id, path "
                         "ORDER BY n DESC LIMIT 15", (key, since30))
        for f in files:
            f["share_name"] = (shares.get(f["share_id"]) or {}).get("name")
        clients = sorted(({"client": k, "first": v[0], "last": v[1]} for k, v in
                          json.loads(prof.get("clients") or "{}").items()), key=lambda r: -r["last"])
        findings = db.query("SELECT * FROM user_findings WHERE user=? ORDER BY ts DESC LIMIT 60", (key,))
        for f in findings:
            f["details"] = json.loads(f["details"] or "{}")
            f["label"] = DETECTOR_LABEL.get(f["detector"], f["detector"])
        sessions = db.query("SELECT * FROM smb_sessions WHERE user=? ORDER BY last_seen DESC LIMIT 20", (key,))
        cap = capacity_of(prof, cfg)
        total = sum(s["size"] for s in storage)
        return {"profile": {**{k: v for k, v in prof.items() if k not in ("clients", "folders")},
                            "level": risk_level(prof.get("risk") or 0)},
                "storage": storage, "storage_total": total, "storage_files": sum(s["files"] for s in storage),
                "capacity": cap, "capacity_pct": round(total * 100 / cap, 1) if cap else None, "history": history,
                "week": week, "daily": daily, "heatmap": heat, "folders": folders, "files": files, "clients": clients,
                "findings": findings, "sessions": sessions,
                "business_hours": cfg.get("business_hours"),
                "events": db.query("SELECT e.*, s.name AS share_name FROM user_events e LEFT JOIN shares s "
                                   "ON s.id=e.share_id WHERE e.user=? ORDER BY e.ts DESC LIMIT 100", (key,))}

    @app.get("/api/security/user/live")
    def user_live(u: str, user=Depends(viewer)):
        key, _ = profile_or_404(u)
        out = {"sessions": [], "open_files": [], "blocked": [], "ntfs_quotas": [], "errors": []}
        for name, fn in (("sessions", response.user_sessions), ("open_files", response.user_open_files),
                         ("blocked", response.blocked)):
            try:
                out[name] = fn(key)
            except (system.CommandError, OSError) as exc:
                out["errors"].append(f"{name} : {exc}")
        try:
            out["ntfs_quotas"] = [q for q in response.ntfs_quotas() if q["user"] == key]
        except (system.CommandError, OSError) as exc:
            out["errors"].append(f"quotas NTFS : {exc}")
        return out

    @app.put("/api/security/user")
    def user_update(u: str, body: ProfileIn, user=Depends(operator)):
        key, prof = profile_or_404(u)
        if not db.query_one("SELECT 1 AS x FROM user_profiles WHERE user=?", (key,)):
            db.execute("INSERT INTO user_profiles(user, display) VALUES(?,?)", (key, prof.get("display") or key))
        sets = {}
        if body.watch is not None:
            sets["watch"] = int(body.watch)
        if body.service is not None:
            sets["service"] = int(body.service)
        if body.capacity_gb is not None:
            sets["capacity_bytes"] = int(body.capacity_gb * 1024 ** 3)
        if body.notes is not None:
            sets["notes"] = body.notes
        if sets:
            db.execute(f"UPDATE user_profiles SET {', '.join(f'{k}=?' for k in sets)} WHERE user=?", (*sets.values(), key))
            db.audit(user["username"], "profil utilisateur modifié", key, json.dumps(sets, ensure_ascii=False))
        if "capacity_bytes" in sets:
            check_capacity(engine)
        return {"ok": True}

    @app.post("/api/security/user/action")
    def user_action(u: str, body: ActionIn, user=Depends(operator)):
        key, _ = profile_or_404(u)
        if body.action in ("block", "unblock", "disable", "enable") and not c["has_role"](user["role"], "admin"):
            raise HTTPException(403, "Action réservée aux administrateurs")
        if body.action == "disable" and body.confirm.strip().upper().replace("É", "E") != "DESACTIVER":
            raise HTTPException(400, "Saisissez DÉSACTIVER pour confirmer")
        shares = [s for sid, s in shares_by_id().items() if sid in body.share_ids]
        try:
            if body.action == "close_sessions":
                n = response.close_sessions(key)
                msg = f"{n} session(s) fermée(s)"
            elif body.action in ("block", "unblock"):
                if not shares:
                    raise HTTPException(400, "Choisissez au moins un dossier")
                names = (response.block if body.action == "block" else response.unblock)(key, shares)
                msg = ("Accès bloqué" if body.action == "block" else "Accès rétabli") + " : " + ", ".join(names)
            else:
                where = response.set_account(key, body.action == "enable")
                msg = f"Compte {'réactivé' if body.action == 'enable' else 'désactivé'} ({where})"
        except system.CommandError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.audit(user["username"], f"sécurité : {body.action}", key, msg)
        return {"ok": True, "message": msg}

    # ------------------------------------------------------------ analyse des journaux
    def events_where(user_q, share_id, action, result, client, q, since, until, source):
        sql, params = " WHERE 1=1", []
        if user_q:
            sql += " AND (e.user LIKE ? OR e.user = ?)"
            params += [f"%{user_q.lower()}%", user_key(user_q)]
        for col, val in (("e.share_id", share_id), ("e.result", result), ("e.source", source)):
            if val not in (None, ""):
                sql += f" AND {col}=?"
                params.append(val)
        if action:
            acts = [a for a in action.split(",") if a in ACTION_LABEL]
            if acts:
                sql += f" AND e.action IN ({','.join('?' * len(acts))})"
                params += acts
        if client:
            sql += " AND e.client LIKE ?"
            params.append(f"%{client}%")
        if q:
            sql += " AND (e.path LIKE ? OR e.dest_path LIKE ? OR e.details LIKE ?)"
            params += [f"%{q}%"] * 3
        if since:
            sql += " AND e.ts >= ?"
            params.append(since)
        if until:
            sql += " AND e.ts <= ?"
            params.append(until)
        return sql, params

    @app.get("/api/security/events")
    def events(u: str = "", share_id: Optional[int] = None, action: str = "", result: str = "", client: str = "",
               q: str = "", since: Optional[float] = None, until: Optional[float] = None, source: str = "",
               limit: int = Query(200, le=2000), offset: int = Query(0, ge=0), user=Depends(viewer)):
        where, params = events_where(u, share_id, action, result, client, q, since, until, source)
        rows = db.query("SELECT e.*, s.name AS share_name FROM user_events e LEFT JOIN shares s ON s.id=e.share_id"
                        + where + " ORDER BY e.ts DESC, e.id DESC LIMIT ? OFFSET ?", params + [limit, offset])
        total = db.query_one("SELECT COUNT(*) AS n FROM user_events e" + where, params)["n"]
        return {"total": total, "rows": rows}

    @app.get("/api/security/stats")
    def stats(u: str = "", share_id: Optional[int] = None, action: str = "", result: str = "", client: str = "",
              q: str = "", since: Optional[float] = None, until: Optional[float] = None, source: str = "",
              user=Depends(viewer)):
        where, params = events_where(u, share_id, action, result, client, q, since, until, source)
        base = "FROM user_events e" + where
        span = db.query_one(f"SELECT MIN(e.ts) AS a, MAX(e.ts) AS b, COUNT(*) AS n {base}", params)
        step = 3600
        if span["a"] and span["b"]:
            width = max(1, span["b"] - span["a"])
            step = next((s for s in (300, 900, 3600, 3 * 3600, 6 * 3600, 86400, 7 * 86400) if width / s <= 60), 30 * 86400)
        hist = db.query(f"SELECT CAST(e.ts / {step} AS INTEGER) * {step} AS t, COUNT(*) AS n, "
                        f"SUM(e.action='denied' OR e.result='fail') AS fails {base} GROUP BY 1 ORDER BY 1", params)
        top = lambda col, n=12: db.query(f"SELECT {col} AS k, COUNT(*) AS n {base} GROUP BY 1 ORDER BY n DESC LIMIT {n}",  # noqa: E731
                                         params)
        by_share = top("e.share_id")
        names = shares_by_id()
        for r in by_share:
            r["name"] = (names.get(r["k"]) or {}).get("name") or "hors dossiers supervisés"
        return {"total": span["n"], "start": span["a"], "end": span["b"], "step": step, "histogram": hist,
                "by_action": top("e.action", 20), "by_user": top("e.user", 15), "by_share": by_share,
                "by_client": [r for r in top("e.client", 15) if r["k"]], "by_source": top("e.source", 10)}

    @app.get("/api/security/events.csv")
    def events_csv(u: str = "", share_id: Optional[int] = None, action: str = "", result: str = "", client: str = "",
                   q: str = "", since: Optional[float] = None, until: Optional[float] = None, source: str = "",
                   user=Depends(viewer)):
        where, params = events_where(u, share_id, action, result, client, q, since, until, source)
        rows = db.query("SELECT e.*, s.name AS share_name FROM user_events e LEFT JOIN shares s ON s.id=e.share_id"
                        + where + " ORDER BY e.ts DESC LIMIT 200000", params)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["date", "utilisateur", "action", "résultat", "dossier", "chemin", "destination", "poste", "source",
                    "événement", "détails"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["user"],
                        ACTION_LABEL.get(r["action"], r["action"]), "échec" if r["result"] == "fail" else "succès",
                        r["share_name"] or "", r["path"] or "", r["dest_path"] or "", r["client"] or "", r["source"],
                        r["event_id"] or "", r["details"] or ""])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="analyse-acces.csv"'})

    @app.get("/api/security/findings")
    def findings(u: str = "", detector: str = "", since: Optional[float] = None,
                 limit: int = Query(200, le=2000), user=Depends(viewer)):
        sql, params = "SELECT f.*, s.name AS share_name FROM user_findings f LEFT JOIN shares s ON s.id=f.share_id WHERE 1=1", []
        if u:
            sql += " AND f.user=?"
            params.append(user_key(u))
        if detector:
            sql += " AND f.detector=?"
            params.append(detector)
        if since:
            sql += " AND f.ts>=?"
            params.append(since)
        rows = db.query(sql + " ORDER BY f.ts DESC LIMIT ?", params + [limit])
        for f in rows:
            f["details"] = json.loads(f["details"] or "{}")
            f["label"] = DETECTOR_LABEL.get(f["detector"], f["detector"])
        return rows

    @app.get("/api/security/labels")
    def labels(user=Depends(viewer)):
        return {"actions": ACTION_LABEL, "detectors": DETECTOR_LABEL}

    @app.post("/api/security/collect")
    def collect_now(user=Depends(operator)):
        n = sec.run_once()
        return {"ok": True, "events": n}

    @app.post("/api/security/analyze")
    def analyze(days: float = Query(1, gt=0, le=400), user=Depends(operator)):
        now = time.time()
        if sec.analyzer is None:
            from .ueba import Analyzer
            sec.analyzer = Analyzer(engine, store)
        new = sec.analyzer.run(now - days * 86400, now)
        db.audit(user["username"], "analyse comportementale", f"{days:g} jour(s)", f"{len(new)} constat(s)")
        return {"ok": True, "findings": len(new)}

    @app.post("/api/security/import")
    async def import_log(request: Request, filename: str = Query(..., min_length=1, max_length=255),
                         user=Depends(admin)):
        data = await request.body()
        if not data:
            raise HTTPException(400, "Fichier vide")
        if len(data) > MAX_IMPORT:
            raise HTTPException(413, "Fichier trop volumineux (300 Mo au plus)")

        def run(ctx):
            ctx.progress(step="analyse du journal")
            try:
                return sec.import_file(filename, data, user["username"])
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        return {"job_id": jobs.submit("security_import", run, user["username"], params={"file": filename})}

    # ------------------------------------------------------------ préparation de l'audit
    @app.get("/api/security/audit")
    def audit_status(user=Depends(viewer)):
        cfg = merged_setting(db, "security")
        try:
            return auditsetup.check(engine.shares(), cfg.get("include_reads", True))
        except system.CommandError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/security/audit/apply")
    def audit_apply(body: KeysIn, user=Depends(admin)):
        cfg = merged_setting(db, "security")
        try:
            res = auditsetup.apply(body.keys, engine.shares(), cfg.get("include_reads", True))
        except system.CommandError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.audit(user["username"], "préparation de l'audit", ", ".join(body.keys),
                 f"{sum(r['ok'] for r in res)} réglage(s) appliqué(s)")
        return {"results": res}

    @app.get("/api/security/audit/script.ps1")
    def audit_script(user=Depends(viewer)):
        cfg = merged_setting(db, "security")
        text = auditsetup.script(engine.shares(), cfg.get("include_reads", True))
        return Response(("﻿" + text).encode("utf-8"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="VigieFS-preparer-audit.ps1"'})
