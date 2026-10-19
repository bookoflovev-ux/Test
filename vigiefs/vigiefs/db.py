"""Couche de persistance SQLite (mode WAL, une connexion par opération)."""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact_name TEXT DEFAULT '',
    contact_email TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    description TEXT DEFAULT '',
    quota_bytes INTEGER DEFAULT 0,
    quota_warn_pct INTEGER DEFAULT 80,
    quota_crit_pct INTEGER DEFAULT 95,
    screening_enabled INTEGER DEFAULT 1,
    screening_action TEXT DEFAULT 'alert',
    blocked_extensions TEXT DEFAULT '',
    monitor_enabled INTEGER DEFAULT 1,
    scan_interval_min INTEGER DEFAULT 360,
    stale_days INTEGER DEFAULT 365,
    disk_free_warn_pct INTEGER DEFAULT 10,
    created_at REAL NOT NULL,
    last_scan_at REAL,
    last_size INTEGER,
    last_file_count INTEGER,
    last_scan_status TEXT,
    UNIQUE(client_id, name)
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    total_size INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    dir_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    stale_count INTEGER DEFAULT 0,
    stale_size INTEGER DEFAULT 0,
    report TEXT DEFAULT '{}',
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_share ON scans(share_id, started_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    path TEXT NOT NULL,
    dest_path TEXT,
    size INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_share_ts ON events(share_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER REFERENCES shares(id) ON DELETE CASCADE,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    details TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open',
    count INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    ack_by TEXT,
    ack_at REAL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, last_seen);
CREATE INDEX IF NOT EXISTS idx_alerts_fp ON alerts(fingerprint, status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    full_name TEXT DEFAULT '',
    created_at REAL NOT NULL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at REAL NOT NULL,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    details TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    share_id INTEGER REFERENCES shares(id) ON DELETE SET NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    params TEXT DEFAULT '{}',
    progress TEXT DEFAULT '{}',
    result TEXT,
    error TEXT,
    actor TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS recycle_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER REFERENCES shares(id) ON DELETE SET NULL,
    share_root TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    stored_path TEXT,
    is_dir INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    mtime REAL,
    reason TEXT NOT NULL,
    job_id INTEGER,
    deleted_at REAL NOT NULL,
    deleted_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'stored',
    restored_at REAL,
    restored_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_recycle_status ON recycle_items(status, deleted_at);

CREATE TABLE IF NOT EXISTS archive_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER REFERENCES shares(id) ON DELETE SET NULL,
    share_root TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    stub_path TEXT,
    size INTEGER DEFAULT 0,
    mtime REAL,
    sha256 TEXT,
    job_id INTEGER,
    archived_at REAL NOT NULL,
    archived_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'archived',
    restored_at REAL
);
CREATE INDEX IF NOT EXISTS idx_archive_share ON archive_items(share_id, status);

CREATE TABLE IF NOT EXISTS repairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    share_id INTEGER REFERENCES shares(id) ON DELETE SET NULL,
    rel_path TEXT,
    original_name TEXT NOT NULL,
    input_path TEXT,
    format TEXT,
    status TEXT NOT NULL,
    diagnosis TEXT DEFAULT '{}',
    actions TEXT DEFAULT '[]',
    output_path TEXT,
    created_at REAL NOT NULL,
    actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    platform TEXT NOT NULL,
    depth INTEGER NOT NULL,
    summary TEXT DEFAULT '{}',
    findings TEXT DEFAULT '[]',
    data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acl_share ON acl_snapshots(share_id, created_at);

CREATE TABLE IF NOT EXISTS matrices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'proposal',
    status TEXT NOT NULL DEFAULT 'draft',
    data TEXT NOT NULL,
    baseline_snapshot INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    created_by TEXT NOT NULL,
    applied_at REAL,
    applied_by TEXT,
    backup_file TEXT
);

CREATE TABLE IF NOT EXISTS folder_stats (
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    depth INTEGER NOT NULL,
    size INTEGER DEFAULT 0,
    files INTEGER DEFAULT 0,
    subdirs INTEGER DEFAULT 0,
    direct_items INTEGER DEFAULT 0,
    max_path_len INTEGER DEFAULT 0,
    newest_mtime REAL,
    PRIMARY KEY(share_id, rel_path)
);

CREATE TABLE IF NOT EXISTS reparedocs_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    share_id INTEGER REFERENCES shares(id) ON DELETE SET NULL,
    source TEXT,
    output TEXT,
    options TEXT DEFAULT '{}',
    origin TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'running',
    task_dir TEXT,
    summary TEXT DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL,
    finished_at REAL,
    actor TEXT
);

CREATE TABLE IF NOT EXISTS restructure_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL,
    prepared_at REAL,
    executed_at REAL,
    result TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS quotas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL DEFAULT '',
    limit_bytes INTEGER NOT NULL,
    warn_pct INTEGER DEFAULT 85,
    crit_pct INTEGER DEFAULT 95,
    mode TEXT NOT NULL DEFAULT 'soft',
    enforcement TEXT NOT NULL DEFAULT 'auto',
    auto_apply INTEGER DEFAULT 0,
    template TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    state TEXT DEFAULT '{}',
    UNIQUE(share_id, rel_path)
);

CREATE TABLE IF NOT EXISTS quota_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quota_id INTEGER REFERENCES quotas(id) ON DELETE SET NULL,
    share_id INTEGER REFERENCES shares(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    principals TEXT NOT NULL,
    platform TEXT NOT NULL,
    locked_at REAL NOT NULL,
    unlocked_at REAL
);
"""

# Colonnes ajoutées après la v1 : migrées automatiquement sur les bases existantes.
SHARE_MIGRATIONS = {
    "archive_enabled": "INTEGER DEFAULT 0",
    "archive_age_days": "INTEGER DEFAULT 730",
    "archive_min_size_kb": "INTEGER DEFAULT 0",
    "archive_extensions": "TEXT DEFAULT ''",
    "archive_exclude": "TEXT DEFAULT ''",
    "archive_criterion": "TEXT DEFAULT 'mtime'",
    "archive_stub": "INTEGER DEFAULT 1",
    "archive_interval_days": "INTEGER DEFAULT 30",
    "last_archive_at": "REAL",
    "access_path": "TEXT DEFAULT ''",
    "acl_depth": "INTEGER DEFAULT 4",
    "acl_interval_h": "INTEGER DEFAULT 24",
    "last_acl_at": "REAL",
    "matrix_depth": "INTEGER DEFAULT 2",
}


class Database:
    def __init__(self, path):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._write_lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(shares)")}
            for col, decl in SHARE_MIGRATIONS.items():
                if col not in cols:
                    conn.execute(f"ALTER TABLE shares ADD COLUMN {col} {decl}")
            # Tâches coupées par un arrêt ou un incident : conservées pour l'alerte de reprise.
            self.interrupted_jobs = [dict(r) for r in conn.execute(
                "SELECT id, type, title, share_id FROM jobs WHERE status IN ('queued','running')")]
            conn.execute("UPDATE jobs SET status='interrupted', finished_at=?, "
                         "error=COALESCE(error, 'Interrompue par un arrêt ou un redémarrage du service') "
                         "WHERE status IN ('queued','running')", (time.time(),))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql, params=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_one(self, sql, params=()):
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql, params=()):
        with self._write_lock, self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid if cur.lastrowid else cur.rowcount

    def executemany(self, sql, seq):
        with self._write_lock, self.connect() as conn:
            conn.executemany(sql, seq)

    # --- Paramètres clé/valeur (JSON) ---
    # --- maintenance (fonctionnement continu) ---
    def checkpoint(self):
        """Reporte le journal WAL dans la base et le tronque (évite un fichier -wal qui grossit)."""
        if self.path == ":memory:":
            return
        with self._write_lock, self.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def quick_check(self):
        with self.connect() as conn:
            rows = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
        return "ok" if rows == ["ok"] else "; ".join(rows[:5])

    def optimize(self):
        with self.connect() as conn:
            conn.execute("PRAGMA optimize")

    def backup_to(self, folder, keep=7):
        """Sauvegarde en ligne cohérente (API de sauvegarde SQLite), rotation sur `keep` copies."""
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, time.strftime("vigiefs-%Y%m%d-%H%M%S.db"))
        src = sqlite3.connect(self.path, timeout=30)
        try:
            out = sqlite3.connect(dest)
            try:
                src.backup(out, pages=4096, sleep=0.05)
            finally:
                out.close()
        finally:
            src.close()
        backups = sorted(f for f in os.listdir(folder) if f.startswith("vigiefs-") and f.endswith(".db"))
        for old in backups[:-max(1, keep)]:
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
        return dest

    def get_setting(self, key, default=None):
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key, value):
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def audit(self, actor, action, target="", details=""):
        self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, details) VALUES(?,?,?,?,?)",
            (time.time(), actor, action, str(target), str(details)),
        )
