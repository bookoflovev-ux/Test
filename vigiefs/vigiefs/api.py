"""API REST (FastAPI) + service de l'interface web."""

import csv
import io
import json
import os
import platform
import shutil
import socket
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .alerts import AlertManager, send_email, send_webhook
from .auth import AuthService, has_role, hash_password, validate_password_strength
from .config import DEFAULT_SETTINGS, merged_setting
from .db import Database
from .engine import Engine
from .permissions import read_permissions
from .rules import normalize_extensions, quota_state, safe_join
from . import docrepair
from .integrity import STATUS_LABEL, integrity_scan, repair_in_share, repair_upload
from .jobs import JOB_LABELS
from .optimize import CATEGORY_KEYS, EMPTY_DIRS, JUNK_CATEGORIES, find_duplicates, resolve_duplicates, run_cleanup
from .storage import share_policy

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SESSION_COOKIE = "vigiefs_session"
SECRET_FIELDS = {"smtp": ["password"], "webhook": ["secret"]}
MASK = "********"


# ---------------------------------------------------------------- modèles
class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str
    full_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact_name: str = ""
    contact_email: str = ""
    notes: str = ""


class ShareIn(BaseModel):
    client_id: int
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1)
    description: str = ""
    quota_bytes: int = Field(0, ge=0)
    quota_warn_pct: int = Field(80, ge=1, le=100)
    quota_crit_pct: int = Field(95, ge=1, le=200)
    screening_enabled: bool = True
    screening_action: str = Field("alert", pattern="^(alert|quarantine)$")
    blocked_extensions: str = ""
    monitor_enabled: bool = True
    scan_interval_min: int = Field(360, ge=0)
    stale_days: int = Field(365, ge=0)
    disk_free_warn_pct: int = Field(10, ge=0, le=99)
    archive_enabled: bool = False
    archive_age_days: int = Field(730, ge=1)
    archive_min_size_kb: int = Field(0, ge=0)
    archive_extensions: str = ""
    archive_exclude: str = ""
    archive_criterion: str = Field("mtime", pattern="^(mtime|atime)$")
    archive_stub: bool = True
    archive_interval_days: int = Field(30, ge=1)


SHARE_FIELDS = ["client_id", "name", "path", "description", "quota_bytes", "quota_warn_pct", "quota_crit_pct",
                "screening_enabled", "screening_action", "blocked_extensions", "monitor_enabled",
                "scan_interval_min", "stale_days", "disk_free_warn_pct", "archive_enabled", "archive_age_days",
                "archive_min_size_kb", "archive_extensions", "archive_exclude", "archive_criterion",
                "archive_stub", "archive_interval_days"]


class CleanupIn(BaseModel):
    categories: List[str]


class DuplicatesIn(BaseModel):
    min_size_mb: float = Field(1.0, ge=0)


class ResolveIn(BaseModel):
    strategy: str = Field("oldest", pattern="^(oldest|newest|shortest)$")
    hashes: Optional[List[str]] = None
    keep: dict = {}


class ArchivePolicyIn(BaseModel):
    age_days: Optional[int] = Field(None, ge=1)
    min_size_kb: Optional[int] = Field(None, ge=0)
    extensions: Optional[str] = None
    exclude: Optional[str] = None
    criterion: Optional[str] = Field(None, pattern="^(mtime|atime)$")
    stub: Optional[bool] = None


class RepairIn(BaseModel):
    path: str = Field(min_length=1)
    mode: str = Field("copy", pattern="^(copy|replace)$")


class RepairBatchIn(BaseModel):
    paths: List[str]
    mode: str = Field("copy", pattern="^(copy|replace)$")


class PathIn(BaseModel):
    path: str = ""


class MkdirIn(BaseModel):
    path: str = ""
    name: str = Field(min_length=1, max_length=255)


class RenameIn(BaseModel):
    path: str = Field(min_length=1)
    new_name: str = Field(min_length=1, max_length=255)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: Optional[str] = None
    role: str = Field("viewer", pattern="^(viewer|operator|admin)$")
    full_name: str = ""


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field("viewer", pattern="^(viewer|operator|admin)$")


class BulkIdsIn(BaseModel):
    ids: List[int]


# ---------------------------------------------------------------- utilitaires
def _valid_name(name):
    bad = set('<>:"/\\|?*') | {chr(i) for i in range(32)}
    name = name.strip()
    if not name or name in (".", "..") or any(c in bad for c in name) or name.endswith((" ", ".")):
        raise HTTPException(400, "Nom invalide (caractères interdits : < > : \" / \\ | ? *)")
    return name


class LoginThrottle:
    """Verrouillage temporaire après échecs répétés (anti force brute)."""

    def __init__(self, max_failures=5, window=900):
        self.max_failures, self.window = max_failures, window
        self.failures = defaultdict(list)
        self.lock = threading.Lock()

    def blocked(self, key):
        with self.lock:
            now = time.time()
            self.failures[key] = [t for t in self.failures[key] if t > now - self.window]
            return len(self.failures[key]) >= self.max_failures

    def fail(self, key):
        with self.lock:
            self.failures[key].append(time.time())

    def reset(self, key):
        with self.lock:
            self.failures.pop(key, None)


def create_app(data_dir=None, start_engine=True, engine_factory=None):
    data_dir = data_dir or os.environ.get("VIGIEFS_DATA", os.path.join(os.getcwd(), "data"))
    os.makedirs(data_dir, exist_ok=True)
    db = Database(os.path.join(data_dir, "vigiefs.db"))
    alerts = AlertManager(db)
    engine = (engine_factory or Engine)(db, alerts, data_dir=data_dir)
    jobs, recycle, archive = engine.jobs, engine.recycle, engine.archive
    auth = AuthService(db)
    throttle = LoginThrottle()

    @asynccontextmanager
    async def lifespan(_app):
        if start_engine:
            engine.start()
        try:
            yield
        finally:
            if start_engine:
                engine.stop()

    app = FastAPI(title="VigieFS", version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.db, app.state.engine, app.state.alerts, app.state.auth = db, engine, alerts, auth

    frame_ancestors = os.environ.get("VIGIEFS_FRAME_ANCESTORS", "'none'")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            f"frame-ancestors {frame_ancestors}")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------ identité
    def identity(request: Request):
        key = request.headers.get("x-api-key")
        authz = request.headers.get("authorization", "")
        if not key and authz.lower().startswith("bearer "):
            key = authz[7:].strip()
        if key:
            ident = auth.api_key_identity(key)
            if not ident:
                raise HTTPException(401, "Clé d'API invalide")
            return ident
        user = auth.session_user(request.cookies.get(SESSION_COOKIE))
        if not user:
            raise HTTPException(401, "Authentification requise")
        # Protection CSRF : les requêtes modifiantes par cookie doivent porter l'en-tête applicatif.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-vigiefs") != "1":
            raise HTTPException(403, "En-tête X-VigieFS manquant")
        return user

    def require(role):
        def dep(user=Depends(identity)):
            if not has_role(user["role"], role):
                raise HTTPException(403, "Droits insuffisants")
            return user
        return dep

    viewer, operator, admin = require("viewer"), require("operator"), require("admin")

    def get_share_or_404(share_id):
        share = engine.share(share_id)
        if not share:
            raise HTTPException(404, "Dossier inconnu")
        return share

    def share_view(share):
        out = dict(share)
        level, pct = quota_state(share, share["last_size"])
        out["quota_pct"] = round(pct, 2) if pct is not None else None
        out["quota_level"] = level
        out["monitor_status"] = engine.monitor.status.get(share["id"], "inconnue")
        job = engine.running.get(share["id"])
        out["scan_running"] = bool(job)
        out["scan_progress"] = dict(job["progress"]) if job else None
        out["path_available"] = os.path.isdir(share["path"])
        return out

    # ------------------------------------------------------------ authentification
    @app.get("/api/auth/status")
    def auth_status(request: Request):
        user = auth.session_user(request.cookies.get(SESSION_COOKIE))
        return {
            "setup_required": auth.user_count() == 0,
            "user": {k: user[k] for k in ("id", "username", "role", "full_name")} if user else None,
            "version": __version__,
            "company": merged_setting(db, "general").get("company_name"),
        }

    @app.post("/api/auth/setup")
    def auth_setup(body: SetupIn):
        if auth.user_count() > 0:
            raise HTTPException(409, "L'installation est déjà initialisée")
        err = validate_password_strength(body.password)
        if err:
            raise HTTPException(400, err)
        auth.create_user(body.username, body.password, "admin", body.full_name)
        db.audit(body.username, "installation", "premier administrateur créé")
        return {"ok": True}

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        key = f"{request.client.host if request.client else '?'}:{body.username.lower()}"
        if throttle.blocked(key):
            raise HTTPException(429, "Trop de tentatives. Réessayez dans 15 minutes.")
        user = auth.authenticate(body.username, body.password)
        if not user:
            throttle.fail(key)
            db.audit(body.username, "connexion refusée", request.client.host if request.client else "")
            raise HTTPException(401, "Identifiants incorrects")
        throttle.reset(key)
        hours = int(merged_setting(db, "general").get("session_hours", 12))
        token = auth.create_session(user["id"], hours)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", max_age=hours * 3600,
                            secure=request.url.scheme == "https")
        db.audit(user["username"], "connexion", request.client.host if request.client else "")
        return {"ok": True, "user": {k: user[k] for k in ("id", "username", "role", "full_name")}}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        auth.delete_session(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(body: PasswordIn, user=Depends(viewer)):
        if not user.get("id"):
            raise HTTPException(400, "Non applicable à une clé d'API")
        full = db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
        if not auth.authenticate(full["username"], body.current_password):
            raise HTTPException(400, "Mot de passe actuel incorrect")
        err = validate_password_strength(body.new_password)
        if err:
            raise HTTPException(400, err)
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new_password), user["id"]))
        db.audit(user["username"], "changement de mot de passe")
        return {"ok": True}

    # ------------------------------------------------------------ tableau de bord
    @app.get("/api/dashboard")
    def dashboard(user=Depends(viewer)):
        shares = [share_view(s) for s in engine.shares()]
        clients = db.query("SELECT id, name FROM clients ORDER BY name")
        sev = {r["severity"]: r["n"] for r in db.query(
            "SELECT severity, COUNT(*) AS n FROM alerts WHERE status='open' GROUP BY severity")}
        now = time.time()
        hour = 3600
        start = (int(now) // hour - 23) * hour
        buckets = {r["h"]: r["n"] for r in db.query(
            "SELECT CAST(ts / 3600 AS INTEGER) AS h, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY h", (start,))}
        activity = [{"ts": (start // hour + i) * hour, "count": buckets.get(start // hour + i, 0)} for i in range(24)]
        recent = db.query(
            "SELECT a.id, a.severity, a.category, a.title, a.status, a.last_seen, a.count, s.name AS share_name, "
            "c.name AS client_name FROM alerts a LEFT JOIN shares s ON s.id=a.share_id "
            "LEFT JOIN clients c ON c.id=a.client_id WHERE a.status != 'resolved' "
            "ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, a.last_seen DESC "
            "LIMIT 8")
        by_client = defaultdict(lambda: {"size": 0, "files": 0, "shares": 0})
        names = {c["id"]: c["name"] for c in clients}
        for s in shares:
            agg = by_client[s["client_id"]]
            agg["size"] += s["last_size"] or 0
            agg["files"] += s["last_file_count"] or 0
            agg["shares"] += 1
        return {
            "counts": {
                "clients": len(clients),
                "shares": len(shares),
                "total_size": sum(s["last_size"] or 0 for s in shares),
                "total_files": sum(s["last_file_count"] or 0 for s in shares),
                "monitored": sum(1 for s in shares if s["monitor_status"] == "active"),
                "unavailable": sum(1 for s in shares if not s["path_available"]),
                "scans_running": len(engine.running),
                "events_24h": sum(b["count"] for b in activity),
            },
            "alerts": {"critical": sev.get("critical", 0), "warning": sev.get("warning", 0),
                       "info": sev.get("info", 0)},
            "recent_alerts": recent,
            "activity": activity,
            "quota_watch": sorted(
                [s for s in shares if s["quota_pct"] is not None],
                key=lambda s: s["quota_pct"], reverse=True)[:8],
            "clients": sorted(
                [{"id": cid, "name": names.get(cid, "?"), **v} for cid, v in by_client.items()],
                key=lambda c: c["size"], reverse=True),
        }

    # ------------------------------------------------------------ clients
    @app.get("/api/clients")
    def list_clients(user=Depends(viewer)):
        rows = db.query(
            "SELECT c.*, COUNT(s.id) AS share_count, COALESCE(SUM(s.last_size),0) AS total_size, "
            "COALESCE(SUM(s.last_file_count),0) AS total_files, "
            "(SELECT COUNT(*) FROM alerts a WHERE a.client_id=c.id AND a.status='open') AS open_alerts, "
            "(SELECT COUNT(*) FROM alerts a WHERE a.client_id=c.id AND a.status='open' "
            " AND a.severity='critical') AS critical_alerts "
            "FROM clients c LEFT JOIN shares s ON s.client_id=c.id GROUP BY c.id ORDER BY c.name")
        return rows

    @app.post("/api/clients")
    def create_client(body: ClientIn, user=Depends(operator)):
        if db.query_one("SELECT id FROM clients WHERE name=?", (body.name.strip(),)):
            raise HTTPException(409, "Un client porte déjà ce nom")
        cid = db.execute(
            "INSERT INTO clients(name, contact_name, contact_email, notes, created_at) VALUES(?,?,?,?,?)",
            (body.name.strip(), body.contact_name, body.contact_email, body.notes, time.time()))
        db.audit(user["username"], "création client", body.name)
        return {"id": cid}

    @app.put("/api/clients/{client_id}")
    def update_client(client_id: int, body: ClientIn, user=Depends(operator)):
        if not db.query_one("SELECT id FROM clients WHERE id=?", (client_id,)):
            raise HTTPException(404, "Client inconnu")
        db.execute("UPDATE clients SET name=?, contact_name=?, contact_email=?, notes=? WHERE id=?",
                   (body.name.strip(), body.contact_name, body.contact_email, body.notes, client_id))
        db.audit(user["username"], "modification client", body.name)
        return {"ok": True}

    @app.delete("/api/clients/{client_id}")
    def delete_client(client_id: int, user=Depends(admin)):
        client = db.query_one("SELECT * FROM clients WHERE id=?", (client_id,))
        if not client:
            raise HTTPException(404, "Client inconnu")
        for s in db.query("SELECT id FROM shares WHERE client_id=?", (client_id,)):
            engine.cancel_scan(s["id"])
            engine.monitor.unwatch(s["id"])
        db.execute("DELETE FROM clients WHERE id=?", (client_id,))
        db.audit(user["username"], "suppression client", client["name"], "fichiers non touchés")
        return {"ok": True}

    # ------------------------------------------------------------ dossiers supervisés
    def _share_values(body: ShareIn):
        if not db.query_one("SELECT id FROM clients WHERE id=?", (body.client_id,)):
            raise HTTPException(400, "Client inconnu")
        path = body.path.strip()
        if not (os.path.isabs(path) or path.startswith("\\\\")):
            raise HTTPException(400, "Le chemin doit être absolu (ex. D:\\Partages\\Client ou \\\\serveur\\partage)")
        if body.quota_crit_pct < body.quota_warn_pct:
            raise HTTPException(400, "Le seuil critique doit être supérieur ou égal au seuil d'avertissement")
        values = body.model_dump()
        values.update(name=body.name.strip(), path=path,
                      blocked_extensions=", ".join(normalize_extensions(body.blocked_extensions)),
                      archive_extensions=", ".join(normalize_extensions(body.archive_extensions)))
        for k in ("screening_enabled", "monitor_enabled", "archive_enabled", "archive_stub"):
            values[k] = int(values[k])
        return tuple(values[f] for f in SHARE_FIELDS)

    @app.get("/api/shares")
    def list_shares(client_id: Optional[int] = None, user=Depends(viewer)):
        sql = ("SELECT s.*, c.name AS client_name, "
               "(SELECT COUNT(*) FROM alerts a WHERE a.share_id=s.id AND a.status='open') AS open_alerts "
               "FROM shares s JOIN clients c ON c.id=s.client_id")
        params = ()
        if client_id:
            sql += " WHERE s.client_id=?"
            params = (client_id,)
        return [share_view(s) for s in db.query(sql + " ORDER BY c.name, s.name", params)]

    @app.post("/api/shares")
    def create_share(body: ShareIn, user=Depends(operator)):
        values = _share_values(body)
        try:
            sid = db.execute(
                f"INSERT INTO shares({', '.join(SHARE_FIELDS)}, created_at) "
                f"VALUES({', '.join('?' * (len(SHARE_FIELDS) + 1))})", values + (time.time(),))
        except Exception as exc:  # noqa: BLE001
            if "UNIQUE" in str(exc):
                raise HTTPException(409, "Ce client possède déjà un dossier portant ce nom")
            raise
        share = engine.share(sid)
        engine.monitor.watch(share)
        engine.check_availability(share)
        if share["scan_interval_min"]:
            engine.request_scan(sid)
        db.audit(user["username"], "ajout dossier supervisé", body.path)
        return {"id": sid}

    @app.get("/api/shares/{share_id}")
    def get_share(share_id: int, user=Depends(viewer)):
        share = get_share_or_404(share_id)
        out = share_view(share)
        out["client_name"] = db.query_one("SELECT name FROM clients WHERE id=?", (share["client_id"],))["name"]
        out["growth_per_day"] = engine.growth(share)
        out["forecast_days"] = engine.forecast_days(share)
        out["history"] = db.query(
            "SELECT id, started_at, finished_at, status, total_size, file_count, error_count "
            "FROM scans WHERE share_id=? ORDER BY started_at DESC LIMIT 60", (share_id,))
        try:
            du = shutil.disk_usage(share["path"])
            out["disk"] = {"total": du.total, "used": du.used, "free": du.free}
        except OSError:
            out["disk"] = None
        return out

    @app.put("/api/shares/{share_id}")
    def update_share(share_id: int, body: ShareIn, user=Depends(operator)):
        get_share_or_404(share_id)
        values = _share_values(body)
        db.execute(f"UPDATE shares SET {', '.join(f + '=?' for f in SHARE_FIELDS)} WHERE id=?", values + (share_id,))
        share = engine.share(share_id)
        engine.monitor.watch(share)
        db.audit(user["username"], "modification dossier supervisé", share["path"])
        return {"ok": True}

    @app.delete("/api/shares/{share_id}")
    def delete_share(share_id: int, user=Depends(admin)):
        share = get_share_or_404(share_id)
        engine.cancel_scan(share_id)
        engine.monitor.unwatch(share_id)
        db.execute("DELETE FROM shares WHERE id=?", (share_id,))
        db.audit(user["username"], "retrait supervision", share["path"], "fichiers non touchés")
        return {"ok": True}

    @app.post("/api/shares/{share_id}/scan")
    def scan_now(share_id: int, user=Depends(operator)):
        get_share_or_404(share_id)
        started = engine.request_scan(share_id)
        if started:
            db.audit(user["username"], "analyse manuelle", share_id)
        return {"started": started}

    @app.post("/api/shares/{share_id}/scan/cancel")
    def scan_cancel(share_id: int, user=Depends(operator)):
        return {"cancelled": engine.cancel_scan(share_id)}

    @app.get("/api/shares/{share_id}/report")
    def latest_report(share_id: int, scan_id: Optional[int] = None, user=Depends(viewer)):
        get_share_or_404(share_id)
        if scan_id:
            row = db.query_one("SELECT * FROM scans WHERE id=? AND share_id=?", (scan_id, share_id))
        else:
            row = db.query_one("SELECT * FROM scans WHERE share_id=? AND status='done' "
                               "ORDER BY finished_at DESC LIMIT 1", (share_id,))
        if not row:
            return {"scan": None}
        row["report"] = json.loads(row["report"] or "{}")
        return {"scan": row}

    # ------------------------------------------------------------ explorateur
    def resolve(share, rel):
        try:
            return safe_join(share["path"], rel)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/shares/{share_id}/browse")
    def browse(share_id: int, path: str = "", user=Depends(viewer)):
        share = get_share_or_404(share_id)
        target = resolve(share, path)
        if not os.path.isdir(target):
            raise HTTPException(404, "Dossier introuvable")
        entries, truncated = [], False
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if len(entries) >= 5000:
                        truncated = True
                        break
                    try:
                        st = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        entries.append({"name": entry.name, "is_dir": False, "error": True})
                        continue
                    entries.append({"name": entry.name, "is_dir": is_dir, "size": None if is_dir else st.st_size,
                                    "mtime": st.st_mtime, "link": entry.is_symlink()})
        except PermissionError:
            raise HTTPException(403, "Accès refusé par le système de fichiers")
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        rel = os.path.relpath(target, os.path.realpath(share["path"]))
        return {"path": "" if rel == "." else rel.replace("\\", "/"), "entries": entries, "truncated": truncated}

    @app.get("/api/shares/{share_id}/permissions")
    def permissions(share_id: int, path: str = "", user=Depends(viewer)):
        share = get_share_or_404(share_id)
        target = resolve(share, path)
        if not os.path.exists(target):
            raise HTTPException(404, "Élément introuvable")
        return read_permissions(target)

    @app.get("/api/shares/{share_id}/download")
    def download(share_id: int, path: str, user=Depends(operator)):
        share = get_share_or_404(share_id)
        target = resolve(share, path)
        if not os.path.isfile(target):
            raise HTTPException(404, "Fichier introuvable")
        db.audit(user["username"], "téléchargement", target)
        return FileResponse(target, filename=os.path.basename(target))

    @app.post("/api/shares/{share_id}/mkdir")
    def mkdir(share_id: int, body: MkdirIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        parent = resolve(share, body.path)
        target = os.path.join(parent, _valid_name(body.name))
        if os.path.exists(target):
            raise HTTPException(409, "Un élément porte déjà ce nom")
        os.makedirs(target)
        db.audit(user["username"], "création dossier", target)
        return {"ok": True}

    @app.post("/api/shares/{share_id}/rename")
    def rename(share_id: int, body: RenameIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        src = resolve(share, body.path)
        if src == os.path.realpath(share["path"]):
            raise HTTPException(400, "Impossible de renommer la racine du dossier supervisé")
        dest = os.path.join(os.path.dirname(src), _valid_name(body.new_name))
        if os.path.exists(dest):
            raise HTTPException(409, "Un élément porte déjà ce nom")
        os.rename(src, dest)
        db.audit(user["username"], "renommage", src, dest)
        return {"ok": True}

    @app.post("/api/shares/{share_id}/delete")
    def delete_item(share_id: int, body: PathIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        target = resolve(share, body.path)
        if target == os.path.realpath(share["path"]):
            raise HTTPException(400, "Impossible de supprimer la racine du dossier supervisé")
        if not os.path.lexists(target):
            raise HTTPException(404, "Élément introuvable")
        item_id = recycle.discard(share, body.path, "suppression manuelle", user["username"])
        db.audit(user["username"], "suppression (corbeille)", target)
        return {"ok": True, "recycle_id": item_id}

    # ------------------------------------------------------------ journal d'activité
    def _events_query(share_id, client_id, type_, q, since, until):
        sql = ("SELECT e.*, s.name AS share_name, c.name AS client_name FROM events e "
               "JOIN shares s ON s.id=e.share_id JOIN clients c ON c.id=s.client_id WHERE 1=1")
        params = []
        if share_id:
            sql += " AND e.share_id=?"
            params.append(share_id)
        if client_id:
            sql += " AND s.client_id=?"
            params.append(client_id)
        if type_:
            sql += " AND e.type=?"
            params.append(type_)
        if q:
            sql += " AND (e.path LIKE ? OR e.dest_path LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if since:
            sql += " AND e.ts>=?"
            params.append(since)
        if until:
            sql += " AND e.ts<=?"
            params.append(until)
        return sql, params

    @app.get("/api/events")
    def list_events(share_id: Optional[int] = None, client_id: Optional[int] = None,
                    type: Optional[str] = None, q: Optional[str] = None, since: Optional[float] = None,
                    until: Optional[float] = None, limit: int = Query(200, le=2000), offset: int = 0,
                    user=Depends(viewer)):
        sql, params = _events_query(share_id, client_id, type, q, since, until)
        return db.query(sql + " ORDER BY e.ts DESC, e.id DESC LIMIT ? OFFSET ?", params + [limit, offset])

    @app.get("/api/events/export.csv")
    def export_events(share_id: Optional[int] = None, client_id: Optional[int] = None,
                      type: Optional[str] = None, q: Optional[str] = None, since: Optional[float] = None,
                      until: Optional[float] = None, user=Depends(viewer)):
        sql, params = _events_query(share_id, client_id, type, q, since, until)
        rows = db.query(sql + " ORDER BY e.ts DESC LIMIT 100000", params)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["date", "client", "dossier", "type", "chemin", "destination", "taille"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["client_name"],
                        r["share_name"], r["type"], r["path"], r["dest_path"] or "", r["size"] or ""])
        return _csv_response(buf.getvalue(), "journal-activite.csv")

    # ------------------------------------------------------------ alertes
    @app.get("/api/alerts")
    def list_alerts(status: Optional[str] = None, severity: Optional[str] = None,
                    client_id: Optional[int] = None, share_id: Optional[int] = None,
                    category: Optional[str] = None, limit: int = Query(300, le=2000), user=Depends(viewer)):
        sql = ("SELECT a.*, s.name AS share_name, s.path AS share_path, c.name AS client_name FROM alerts a "
               "LEFT JOIN shares s ON s.id=a.share_id LEFT JOIN clients c ON c.id=a.client_id WHERE 1=1")
        params = []
        if status == "active":
            sql += " AND a.status IN ('open','acknowledged')"
        elif status:
            sql += " AND a.status=?"
            params.append(status)
        for col, val in (("a.severity", severity), ("a.client_id", client_id), ("a.share_id", share_id),
                         ("a.category", category)):
            if val:
                sql += f" AND {col}=?"
                params.append(val)
        sql += (" ORDER BY CASE a.status WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END, "
                "CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, a.last_seen DESC LIMIT ?")
        rows = db.query(sql, params + [limit])
        for r in rows:
            r["details"] = json.loads(r["details"] or "{}")
        return rows

    @app.post("/api/alerts/{alert_id}/ack")
    def ack_alert(alert_id: int, user=Depends(operator)):
        alerts.acknowledge(alert_id, user["username"])
        db.audit(user["username"], "acquittement alerte", alert_id)
        return {"ok": True}

    @app.post("/api/alerts/{alert_id}/close")
    def close_alert(alert_id: int, user=Depends(operator)):
        alerts.close(alert_id, user["username"])
        db.audit(user["username"], "clôture alerte", alert_id)
        return {"ok": True}

    @app.post("/api/alerts/bulk-ack")
    def bulk_ack(body: BulkIdsIn, user=Depends(operator)):
        for aid in body.ids:
            alerts.acknowledge(aid, user["username"])
        db.audit(user["username"], "acquittement groupé", f"{len(body.ids)} alertes")
        return {"ok": True}

    # ------------------------------------------------------------ rapports
    @app.get("/api/reports/usage.csv")
    def usage_report(user=Depends(viewer)):
        rows = db.query(
            "SELECT c.name AS client, s.name AS share, s.path, s.last_size, s.last_file_count, s.quota_bytes, "
            "s.last_scan_at FROM shares s JOIN clients c ON c.id=s.client_id ORDER BY c.name, s.name")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["client", "dossier", "chemin", "taille_octets", "taille_go", "fichiers", "quota_go",
                    "utilisation_pct", "derniere_analyse"])
        for r in rows:
            size = r["last_size"] or 0
            quota = r["quota_bytes"] or 0
            w.writerow([r["client"], r["share"], r["path"], size, f"{size / 1024 ** 3:.2f}".replace(".", ","),
                        r["last_file_count"] or 0, f"{quota / 1024 ** 3:.2f}".replace(".", ",") if quota else "",
                        f"{size * 100 / quota:.1f}".replace(".", ",") if quota else "",
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_scan_at"])) if r["last_scan_at"] else ""])
        return _csv_response(buf.getvalue(), "consommation-clients.csv")

    @app.get("/api/reports/alerts.csv")
    def alerts_report(user=Depends(viewer)):
        rows = list_alerts(limit=2000, user=user)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "gravite", "categorie", "statut", "client", "dossier", "titre", "message",
                    "occurrences", "premiere", "derniere", "acquittee_par"])
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""  # noqa: E731
        for r in rows:
            w.writerow([r["id"], r["severity"], r["category"], r["status"], r["client_name"] or "",
                        r["share_name"] or "", r["title"], r["message"], r["count"], fmt(r["first_seen"]),
                        fmt(r["last_seen"]), r["ack_by"] or ""])
        return _csv_response(buf.getvalue(), "alertes.csv")

    def _csv_response(text, filename):
        return Response("\ufeff" + text, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    # ------------------------------------------------------------ paramètres
    @app.get("/api/settings")
    def get_settings(user=Depends(admin)):
        out = {}
        for key in DEFAULT_SETTINGS:
            val = merged_setting(db, key)
            for field in SECRET_FIELDS.get(key, []):
                if val.get(field):
                    val[field] = MASK
            out[key] = val
        return out

    @app.put("/api/settings/{key}")
    def put_setting(key: str, body: dict, user=Depends(admin)):
        if key not in DEFAULT_SETTINGS:
            raise HTTPException(404, "Paramètre inconnu")
        current = merged_setting(db, key)
        allowed = set(DEFAULT_SETTINGS[key])
        new = {k: v for k, v in body.items() if k in allowed}
        for field in SECRET_FIELDS.get(key, []):
            if new.get(field) == MASK:
                new[field] = current.get(field, "")
        if key == "screening" and "default_blocked_extensions" in new:
            new["default_blocked_extensions"] = normalize_extensions(new["default_blocked_extensions"])
        if key == "ransomware" and "extensions" in new:
            new["extensions"] = normalize_extensions(new["extensions"])
        current.update(new)
        db.set_setting(key, current)
        db.audit(user["username"], "modification paramètres", key)
        return {"ok": True}

    @app.post("/api/settings/test-email")
    def test_email(user=Depends(admin)):
        smtp = merged_setting(db, "smtp")
        fake = {"id": 0, "severity": "info", "category": "system", "title": "Test de notification",
                "message": "Ceci est un message de test envoyé depuis VigieFS.", "last_seen": time.time(),
                "client_name": None, "share_name": None, "share_path": None, "details": {}}
        try:
            send_email(smtp, fake, merged_setting(db, "general"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Échec : {exc}")
        return {"ok": True}

    @app.post("/api/settings/test-webhook")
    def test_webhook(user=Depends(admin)):
        hook = merged_setting(db, "webhook")
        if not hook.get("url"):
            raise HTTPException(400, "URL du webhook non configurée")
        try:
            status = send_webhook(hook, {"source": "vigiefs", "event": "test", "message": "Test VigieFS",
                                         "ts": time.time()})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Échec : {exc}")
        return {"ok": True, "status": status}

    # ------------------------------------------------------------ utilisateurs & clés
    @app.get("/api/users")
    def list_users(user=Depends(admin)):
        return db.query("SELECT id, username, role, full_name, created_at, last_login FROM users ORDER BY username")

    @app.post("/api/users")
    def create_user(body: UserIn, user=Depends(admin)):
        if not body.password:
            raise HTTPException(400, "Mot de passe requis")
        err = validate_password_strength(body.password)
        if err:
            raise HTTPException(400, err)
        if db.query_one("SELECT id FROM users WHERE username=?", (body.username.strip(),)):
            raise HTTPException(409, "Identifiant déjà utilisé")
        uid = auth.create_user(body.username, body.password, body.role, body.full_name)
        db.audit(user["username"], "création utilisateur", body.username, body.role)
        return {"id": uid}

    @app.put("/api/users/{user_id}")
    def update_user(user_id: int, body: UserIn, user=Depends(admin)):
        target = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
        if not target:
            raise HTTPException(404, "Utilisateur inconnu")
        if target["role"] == "admin" and body.role != "admin" and \
                db.query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")["n"] <= 1:
            raise HTTPException(400, "Il doit rester au moins un administrateur")
        db.execute("UPDATE users SET role=?, full_name=? WHERE id=?", (body.role, body.full_name, user_id))
        if body.password:
            err = validate_password_strength(body.password)
            if err:
                raise HTTPException(400, err)
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.password), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        db.audit(user["username"], "modification utilisateur", target["username"], body.role)
        return {"ok": True}

    @app.delete("/api/users/{user_id}")
    def delete_user(user_id: int, user=Depends(admin)):
        target = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
        if not target:
            raise HTTPException(404, "Utilisateur inconnu")
        if target["id"] == user.get("id"):
            raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte")
        if target["role"] == "admin" and \
                db.query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")["n"] <= 1:
            raise HTTPException(400, "Il doit rester au moins un administrateur")
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        db.audit(user["username"], "suppression utilisateur", target["username"])
        return {"ok": True}

    @app.get("/api/apikeys")
    def list_keys(user=Depends(admin)):
        return db.query("SELECT id, name, prefix, role, created_at, last_used FROM api_keys ORDER BY created_at DESC")

    @app.post("/api/apikeys")
    def create_key(body: ApiKeyIn, user=Depends(admin)):
        key_id, raw = auth.create_api_key(body.name, body.role)
        db.audit(user["username"], "création clé API", body.name, body.role)
        return {"id": key_id, "key": raw}

    @app.delete("/api/apikeys/{key_id}")
    def delete_key(key_id: int, user=Depends(admin)):
        db.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        db.audit(user["username"], "révocation clé API", key_id)
        return {"ok": True}

    @app.get("/api/audit")
    def audit_log(limit: int = Query(300, le=5000), q: Optional[str] = None, user=Depends(admin)):
        if q:
            return db.query("SELECT * FROM audit_log WHERE actor LIKE ? OR action LIKE ? OR target LIKE ? "
                            "ORDER BY ts DESC LIMIT ?", (f"%{q}%", f"%{q}%", f"%{q}%", limit))
        return db.query("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))


    # ------------------------------------------------------------ tâches de fond
    def job_view(row):
        row = dict(row)
        for k in ("params", "progress"):
            row[k] = json.loads(row[k] or "{}") if isinstance(row.get(k), str) else row.get(k)
        row.pop("result", None)
        return row

    @app.get("/api/jobs")
    def list_jobs(share_id: Optional[int] = None, client_id: Optional[int] = None, type: Optional[str] = None,
                  status: Optional[str] = None, no_share: bool = False, limit: int = Query(100, le=500),
                  user=Depends(viewer)):
        sql = ("SELECT j.id, j.type, j.title, j.share_id, j.client_id, j.status, j.params, j.progress, j.error, "
               "j.actor, j.created_at, j.started_at, j.finished_at, s.name AS share_name, c.name AS client_name "
               "FROM jobs j LEFT JOIN shares s ON s.id=j.share_id LEFT JOIN clients c ON c.id=j.client_id WHERE 1=1")
        params = []
        if no_share:
            sql += " AND j.share_id IS NULL"
        for col, val in (("j.share_id", share_id), ("j.client_id", client_id), ("j.type", type)):
            if val:
                sql += f" AND {col}=?"
                params.append(val)
        if status == "active":
            sql += " AND j.status IN ('queued','running')"
        elif status:
            sql += " AND j.status=?"
            params.append(status)
        return [job_view(r) for r in db.query(sql + " ORDER BY j.id DESC LIMIT ?", params + [limit])]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int, user=Depends(viewer)):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Tâche inconnue")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, user=Depends(operator)):
        return {"cancelled": jobs.cancel(job_id)}

    # ------------------------------------------------------------ optimisation du stockage
    def latest_report_of(share_id):
        row = db.query_one("SELECT report, finished_at FROM scans WHERE share_id=? AND status='done' "
                           "ORDER BY finished_at DESC LIMIT 1", (share_id,))
        return (json.loads(row["report"] or "{}"), row["finished_at"]) if row else ({}, None)

    @app.get("/api/optimization")
    def optimization_overview(client_id: Optional[int] = None, user=Depends(viewer)):
        shares = db.query("SELECT s.*, c.name AS client_name FROM shares s JOIN clients c ON c.id=s.client_id"
                          + (" WHERE s.client_id=?" if client_id else "") + " ORDER BY c.name, s.name",
                          (client_id,) if client_id else ())
        rows = []
        for s in shares:
            rep_, at = latest_report_of(s["id"])
            dup = jobs.latest("duplicates", s["id"])
            junk = rep_.get("junk_total", {"count": 0, "size": 0})
            archived = db.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS size FROM archive_items "
                                    "WHERE share_id=? AND status='archived'", (s["id"],))
            rows.append({
                "id": s["id"], "name": s["name"], "client_id": s["client_id"], "client_name": s["client_name"],
                "path": s["path"], "size": s["last_size"], "scanned_at": at,
                "junk_count": junk["count"], "junk_size": junk["size"],
                "empty_dirs": rep_.get("empty_dirs", {}).get("count", 0),
                "stale_count": rep_.get("stale_count", 0), "stale_size": rep_.get("stale_size", 0),
                "stale_days": s["stale_days"],
                "dup_wasted": dup["result"]["wasted_total"] if dup and dup.get("result") else None,
                "dup_groups": dup["result"]["group_count"] if dup and dup.get("result") else None,
                "dup_at": dup["finished_at"] if dup else None, "dup_job": dup["id"] if dup else None,
                "archive_enabled": bool(s["archive_enabled"]), "archived_count": archived["n"],
                "archived_size": archived["size"],
            })
        recycled = db.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS size FROM recycle_items "
                                "WHERE status='stored'")
        return {
            "shares": rows,
            "totals": {
                "junk_size": sum(r["junk_size"] for r in rows), "junk_count": sum(r["junk_count"] for r in rows),
                "stale_size": sum(r["stale_size"] for r in rows), "stale_count": sum(r["stale_count"] for r in rows),
                "dup_wasted": sum(r["dup_wasted"] or 0 for r in rows),
                "archived_size": sum(r["archived_size"] for r in rows),
                "recycle_size": recycled["size"], "recycle_count": recycled["n"],
                "total_size": sum(r["size"] or 0 for r in rows),
            },
            "categories": [{k: c[k] for k in ("key", "label", "description")} for c in JUNK_CATEGORIES]
                          + [{k: EMPTY_DIRS[k] for k in ("key", "label", "description")}],
            "archive_root": archive.settings().get("root") or "",
        }

    @app.get("/api/shares/{share_id}/junk")
    def share_junk(share_id: int, user=Depends(viewer)):
        get_share_or_404(share_id)
        rep_, at = latest_report_of(share_id)
        cats = []
        for c in JUNK_CATEGORIES:
            j = rep_.get("junk", {}).get(c["key"], {"count": 0, "size": 0, "samples": []})
            cats.append({"key": c["key"], "label": c["label"], "description": c["description"], **j})
        ed = rep_.get("empty_dirs", {"count": 0, "samples": []})
        cats.append({"key": "empty_dirs", "label": EMPTY_DIRS["label"], "description": EMPTY_DIRS["description"],
                     "count": ed["count"], "size": 0, "samples": ed["samples"]})
        return {"scanned_at": at, "categories": cats}

    @app.post("/api/shares/{share_id}/cleanup")
    def start_cleanup(share_id: int, body: CleanupIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        cats = [c for c in body.categories if c in CATEGORY_KEYS]
        if not cats:
            raise HTTPException(400, "Aucune catégorie sélectionnée")
        actor = user["username"]
        jid = jobs.submit("cleanup", lambda ctx: run_cleanup(share, cats, recycle, actor, ctx, db), actor,
                          share=share, params={"categories": cats})
        return {"job_id": jid}

    def _start_duplicates(shares, actor, min_size_mb, client_id=None, title=None):
        min_size = int(min_size_mb * 1024 * 1024)
        share = shares[0] if len(shares) == 1 else None
        return jobs.submit("duplicates", lambda ctx: find_duplicates(shares, min_size, ctx, engine.skip_roots()),
                           actor, share=share, client_id=client_id, title=title,
                           params={"min_size_mb": min_size_mb, "share_ids": [s["id"] for s in shares]})

    @app.post("/api/shares/{share_id}/duplicates")
    def share_duplicates(share_id: int, body: DuplicatesIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        return {"job_id": _start_duplicates([share], user["username"], body.min_size_mb)}

    @app.post("/api/clients/{client_id}/duplicates")
    def client_duplicates(client_id: int, body: DuplicatesIn, user=Depends(operator)):
        client = db.query_one("SELECT * FROM clients WHERE id=?", (client_id,))
        shares = db.query("SELECT * FROM shares WHERE client_id=?", (client_id,))
        if not client or not shares:
            raise HTTPException(404, "Client inconnu ou sans dossier supervisé")
        return {"job_id": _start_duplicates(shares, user["username"], body.min_size_mb, client_id,
                                            f"Recherche de doublons — tous les dossiers de {client['name']}")}

    @app.post("/api/jobs/{job_id}/resolve-duplicates")
    def resolve_dups(job_id: int, body: ResolveIn, user=Depends(operator)):
        job = jobs.get(job_id)
        if not job or job["type"] != "duplicates" or job["status"] != "done":
            raise HTTPException(400, "Recherche de doublons introuvable ou non terminée")
        groups = job["result"]["groups"]
        if body.hashes is not None:
            wanted = set(body.hashes)
            groups = [g for g in groups if g["hash"] in wanted]
        if not groups:
            raise HTTPException(400, "Aucun groupe sélectionné")
        shares = {s["id"]: s for s in engine.shares()}
        actor = user["username"]
        share = shares.get(job["share_id"]) if job["share_id"] else None
        jid = jobs.submit("duplicates_resolve",
                          lambda ctx: resolve_duplicates(groups, body.strategy, body.keep, shares, recycle, actor, ctx),
                          actor, share=share, client_id=job["client_id"], exclusive=False,
                          params={"source_job": job_id, "groups": len(groups), "strategy": body.strategy})
        return {"job_id": jid}

    # ------------------------------------------------------------ archivage à froid
    @app.post("/api/shares/{share_id}/archive/simulate")
    def archive_simulate(share_id: int, body: ArchivePolicyIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        policy = share_policy(share, body.model_dump())
        jid = jobs.submit("archive_simulate", lambda ctx: archive.simulate(share, policy, ctx), user["username"],
                          share=share, params={"policy": policy})
        return {"job_id": jid}

    @app.post("/api/shares/{share_id}/archive/run")
    def archive_run(share_id: int, body: ArchivePolicyIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        if not archive.settings().get("root"):
            raise HTTPException(400, "Configurez d'abord le stockage d'archive (Paramètres › Stockage).")
        return {"job_id": engine.start_archive(share, share_policy(share, body.model_dump()), user["username"])}

    @app.get("/api/archive/items")
    def archive_items(share_id: Optional[int] = None, client_id: Optional[int] = None, q: Optional[str] = None,
                      status: str = "archived", limit: int = Query(500, le=5000), user=Depends(viewer)):
        sql = ("SELECT a.*, s.name AS share_name, c.name AS client_name FROM archive_items a "
               "LEFT JOIN shares s ON s.id=a.share_id LEFT JOIN clients c ON c.id=s.client_id WHERE a.status=?")
        params = [status]
        if share_id:
            sql += " AND a.share_id=?"
            params.append(share_id)
        if client_id:
            sql += " AND s.client_id=?"
            params.append(client_id)
        if q:
            sql += " AND a.rel_path LIKE ?"
            params.append(f"%{q}%")
        return db.query(sql + " ORDER BY a.archived_at DESC LIMIT ?", params + [limit])

    @app.post("/api/archive/restore")
    def archive_restore(body: BulkIdsIn, user=Depends(operator)):
        if not body.ids:
            raise HTTPException(400, "Aucun fichier sélectionné")
        actor = user["username"]
        jid = jobs.submit("archive_restore", lambda ctx: archive.restore(body.ids, actor, ctx), actor,
                          exclusive=False, params={"count": len(body.ids)})
        return {"job_id": jid}

    # ------------------------------------------------------------ corbeille
    @app.get("/api/recycle")
    def recycle_list(share_id: Optional[int] = None, status: str = "stored", q: Optional[str] = None,
                     limit: int = Query(500, le=5000), user=Depends(viewer)):
        sql = ("SELECT r.*, s.name AS share_name, c.name AS client_name FROM recycle_items r "
               "LEFT JOIN shares s ON s.id=r.share_id LEFT JOIN clients c ON c.id=s.client_id WHERE r.status=?")
        params = [status]
        if share_id:
            sql += " AND r.share_id=?"
            params.append(share_id)
        if q:
            sql += " AND (r.rel_path LIKE ? OR r.reason LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        rows = db.query(sql + " ORDER BY r.deleted_at DESC LIMIT ?", params + [limit])
        days = int(merged_setting(db, "recycle").get("retention_days", 30))
        for r in rows:
            r["expires_at"] = r["deleted_at"] + days * 86400 if days > 0 else None
            r.pop("stored_path", None)
        return {"items": rows, "retention_days": days, "root": recycle.root() if has_role(user["role"], "admin") else None}

    @app.post("/api/recycle/restore")
    def recycle_restore(body: BulkIdsIn, user=Depends(operator)):
        restored, errors = [], []
        for i in body.ids:
            try:
                restored.append(recycle.restore(i, user["username"]))
            except Exception as exc:  # noqa: BLE001
                errors.append({"id": i, "error": str(exc)})
        return {"restored": len(restored), "errors": errors}

    @app.post("/api/recycle/purge")
    def recycle_purge(body: BulkIdsIn, user=Depends(admin)):
        n = sum(1 for i in body.ids if recycle.purge(i, user["username"]))
        return {"purged": n}

    # ------------------------------------------------------------ intégrité & réparation de documents
    @app.post("/api/shares/{share_id}/integrity")
    def start_integrity(share_id: int, user=Depends(operator)):
        share = get_share_or_404(share_id)
        jid = jobs.submit("integrity", lambda ctx: integrity_scan(share, ctx, db, engine.skip_roots()),
                          user["username"], share=share)
        return {"job_id": jid}

    @app.get("/api/shares/{share_id}/integrity")
    def last_integrity(share_id: int, user=Depends(viewer)):
        get_share_or_404(share_id)
        running = db.query_one("SELECT id FROM jobs WHERE type='integrity' AND share_id=? AND status IN "
                               "('queued','running') ORDER BY id DESC LIMIT 1", (share_id,))
        return {"running": jobs.get(running["id"]) if running else None,
                "last": jobs.latest("integrity", share_id), "labels": STATUS_LABEL}

    @app.get("/api/shares/{share_id}/diagnose")
    def diagnose_file(share_id: int, path: str, user=Depends(viewer)):
        share = get_share_or_404(share_id)
        target = resolve(share, path)
        if not os.path.isfile(target):
            raise HTTPException(404, "Fichier introuvable")
        return docrepair.diagnose(target)

    @app.post("/api/shares/{share_id}/repair")
    def repair_file(share_id: int, body: RepairIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        resolve(share, body.path)
        try:
            return repair_in_share(db, recycle, share, body.path, body.mode, user["username"])
        except FileNotFoundError:
            raise HTTPException(404, "Fichier introuvable")

    @app.post("/api/shares/{share_id}/repair-batch")
    def repair_batch(share_id: int, body: RepairBatchIn, user=Depends(operator)):
        share = get_share_or_404(share_id)
        for p in body.paths:
            resolve(share, p)
        actor = user["username"]

        def run(ctx):
            summary = {"repaired": 0, "partial": 0, "text_salvaged": 0, "failed": 0, "not_needed": 0, "items": []}
            for n, p in enumerate(body.paths):
                ctx.check()
                ctx.progress(done=n, total=len(body.paths), current=p)
                try:
                    r = repair_in_share(db, recycle, share, p, body.mode, actor)
                    summary[r["status"]] = summary.get(r["status"], 0) + 1
                    summary["items"].append({"path": p, "status": r["status"], "output": r.get("output_rel"),
                                             "actions": r.get("actions", [])})
                except Exception as exc:  # noqa: BLE001
                    summary["failed"] += 1
                    summary["items"].append({"path": p, "status": "failed", "error": str(exc)})
            return summary

        return {"job_id": jobs.submit("repair_batch", run, actor, share=share, exclusive=False,
                                      params={"count": len(body.paths), "mode": body.mode})}

    @app.post("/api/repair/upload")
    async def repair_upload_ep(request: Request, filename: str, user=Depends(operator)):
        content = await request.body()
        max_mb = int(merged_setting(db, "repair").get("max_size_mb", 300))
        if not content:
            raise HTTPException(400, "Fichier vide")
        if len(content) > max_mb * 1024 * 1024:
            raise HTTPException(413, f"Fichier trop volumineux (max {max_mb} Mo)")
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(repair_upload, db, data_dir, filename, content, user["username"])

    @app.get("/api/repairs")
    def list_repairs(limit: int = Query(200, le=2000), user=Depends(viewer)):
        rows = db.query("SELECT r.id, r.source, r.share_id, r.rel_path, r.original_name, r.format, r.status, "
                        "r.output_path, r.created_at, r.actor, r.actions, r.diagnosis, s.name AS share_name "
                        "FROM repairs r LEFT JOIN shares s ON s.id=r.share_id WHERE r.status != 'pending' "
                        "ORDER BY r.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["actions"] = json.loads(r["actions"] or "[]")
            diag = json.loads(r["diagnosis"] or "{}")
            r["issues"] = [i["message"] for i in (diag.get("before") or {}).get("issues", [])][:6]
            r["message"] = diag.get("message")
            r["after_status"] = (diag.get("after") or {}).get("status")
            r["output_name"] = os.path.basename(r["output_path"]) if r["output_path"] else None
            r["downloadable"] = bool(r["output_path"] and os.path.isfile(r["output_path"]))
            del r["diagnosis"], r["output_path"]
        return rows

    @app.get("/api/repairs/{repair_id}/download")
    def download_repair(repair_id: int, user=Depends(operator)):
        row = db.query_one("SELECT * FROM repairs WHERE id=?", (repair_id,))
        if not row or not row["output_path"] or not os.path.isfile(row["output_path"]):
            raise HTTPException(404, "Fichier réparé indisponible")
        db.audit(user["username"], "téléchargement fichier réparé", row["original_name"])
        return FileResponse(row["output_path"], filename=os.path.basename(row["output_path"]))

    @app.get("/api/repair/capabilities")
    def repair_caps(user=Depends(viewer)):
        lo = docrepair.find_libreoffice(merged_setting(db, "repair").get("libreoffice_path") or None)
        return {"libreoffice": lo, "pdf": docrepair.pikepdf is not None, "xml": docrepair.etree is not None,
                "ole": docrepair.olefile is not None, "formats": sorted(docrepair.SUPPORTED)}

    @app.get("/api/system")
    def system_info(user=Depends(viewer)):
        return {
            "version": __version__,
            "hostname": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "data_dir": os.path.abspath(data_dir) if has_role(user["role"], "admin") else None,
            "uptime_sec": time.time() - engine.started_at,
            "events_recorded": engine.monitor.events_total,
            "scans_running": len(engine.running),
            "queue": engine.monitor.queue.qsize(),
        }

    # ------------------------------------------------------------ intégration supervision (v1)
    @app.get("/api/v1/status")
    def integration_status(user=Depends(viewer)):
        """Synthèse stable destinée au centre de supervision / Datto RMM."""
        shares = engine.shares()
        clients = {c["id"]: c["name"] for c in db.query("SELECT id, name FROM clients")}
        open_alerts = db.query("SELECT share_id, client_id, severity, category, title FROM alerts "
                               "WHERE status='open'")
        worst = "ok"
        for a in open_alerts:
            if a["severity"] == "critical":
                worst = "critical"
            elif a["severity"] == "warning" and worst == "ok":
                worst = "warning"
        per_share = []
        for s in shares:
            sa = [a for a in open_alerts if a["share_id"] == s["id"]]
            st = "critical" if any(a["severity"] == "critical" for a in sa) else \
                 "warning" if any(a["severity"] == "warning" for a in sa) else "ok"
            _, pct = quota_state(s, s["last_size"])
            per_share.append({
                "id": s["id"], "client": clients.get(s["client_id"]), "name": s["name"], "path": s["path"],
                "status": st, "size": s["last_size"], "files": s["last_file_count"],
                "quota": s["quota_bytes"] or None, "quota_pct": round(pct, 2) if pct is not None else None,
                "last_scan_at": s["last_scan_at"], "monitoring": engine.monitor.status.get(s["id"]),
                "alerts": [{"severity": a["severity"], "category": a["category"], "title": a["title"]} for a in sa],
            })
        return {
            "status": worst,
            "generated_at": time.time(),
            "hostname": socket.gethostname(),
            "version": __version__,
            "alerts": {sev: sum(1 for a in open_alerts if a["severity"] == sev)
                       for sev in ("critical", "warning", "info")},
            "shares": per_share,
        }

    @app.get("/metrics")
    def metrics(user=Depends(viewer)):
        lines = [
            "# HELP vigiefs_share_size_bytes Taille du dossier supervisé (dernière analyse)",
            "# TYPE vigiefs_share_size_bytes gauge",
        ]

        def lbl(s, cname):
            esc = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            return f'client="{esc(cname)}",share="{esc(s["name"])}"'

        clients = {c["id"]: c["name"] for c in db.query("SELECT id, name FROM clients")}
        shares = engine.shares()
        for s in shares:
            lines.append(f"vigiefs_share_size_bytes{{{lbl(s, clients.get(s['client_id']))}}} {s['last_size'] or 0}")
        lines += ["# TYPE vigiefs_share_files gauge"]
        for s in shares:
            lines.append(f"vigiefs_share_files{{{lbl(s, clients.get(s['client_id']))}}} {s['last_file_count'] or 0}")
        lines += ["# TYPE vigiefs_share_quota_bytes gauge"]
        for s in shares:
            lines.append(f"vigiefs_share_quota_bytes{{{lbl(s, clients.get(s['client_id']))}}} {s['quota_bytes'] or 0}")
        lines += ["# TYPE vigiefs_share_available gauge"]
        for s in shares:
            lines.append(f"vigiefs_share_available{{{lbl(s, clients.get(s['client_id']))}}} "
                         f"{1 if os.path.isdir(s['path']) else 0}")
        lines += ["# TYPE vigiefs_alerts_open gauge"]
        for r in db.query("SELECT severity, COUNT(*) AS n FROM alerts WHERE status='open' GROUP BY severity"):
            lines.append(f'vigiefs_alerts_open{{severity="{r["severity"]}"}} {r["n"]}')
        lines += ["# TYPE vigiefs_events_recorded_total counter",
                  f"vigiefs_events_recorded_total {engine.monitor.events_total}"]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------ interface web
    @app.exception_handler(OSError)
    async def os_error_handler(request: Request, exc: OSError):
        return JSONResponse({"detail": f"Erreur système : {exc.strerror or exc}"}, status_code=400)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app
