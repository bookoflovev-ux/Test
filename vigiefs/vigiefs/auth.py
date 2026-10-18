"""Utilisateurs, sessions et clés d'API."""

import hashlib
import hmac
import secrets
import time

from .config import ROLE_LEVEL

PBKDF2_ITERATIONS = 240_000


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def _sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_password_strength(password):
    if len(password) < 10:
        return "Le mot de passe doit contenir au moins 10 caractères."
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        return "Le mot de passe doit mélanger au moins 3 types : minuscules, majuscules, chiffres, symboles."
    return None


def has_role(role, required):
    return ROLE_LEVEL.get(role, -1) >= ROLE_LEVEL[required]


class AuthService:
    def __init__(self, db):
        self.db = db

    # --- Utilisateurs ---
    def user_count(self):
        return self.db.query_one("SELECT COUNT(*) AS n FROM users")["n"]

    def create_user(self, username, password, role="viewer", full_name=""):
        return self.db.execute(
            "INSERT INTO users(username, password_hash, role, full_name, created_at) VALUES(?,?,?,?,?)",
            (username.strip(), hash_password(password), role, full_name, time.time()),
        )

    def authenticate(self, username, password):
        user = self.db.query_one("SELECT * FROM users WHERE username = ?", (username.strip(),))
        if not user or not verify_password(password, user["password_hash"]):
            return None
        self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user["id"]))
        return user

    # --- Sessions ---
    def create_session(self, user_id, hours):
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
            (_sha256(token), user_id, now, now + hours * 3600),
        )
        return token

    def session_user(self, token):
        if not token:
            return None
        row = self.db.query_one(
            "SELECT u.id, u.username, u.role, u.full_name, s.expires_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (_sha256(token),),
        )
        if not row or row["expires_at"] < time.time():
            return None
        return row

    def delete_session(self, token):
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))

    def purge_sessions(self):
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    # --- Clés d'API (intégration supervision / Datto RMM) ---
    def create_api_key(self, name, role="viewer"):
        raw = "vfs_" + secrets.token_urlsafe(32)
        key_id = self.db.execute(
            "INSERT INTO api_keys(name, prefix, key_hash, role, created_at) VALUES(?,?,?,?,?)",
            (name, raw[:10], _sha256(raw), role, time.time()),
        )
        return key_id, raw

    def api_key_identity(self, raw):
        if not raw:
            return None
        row = self.db.query_one("SELECT id, name, role FROM api_keys WHERE key_hash = ?", (_sha256(raw),))
        if not row:
            return None
        self.db.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (time.time(), row["id"]))
        return {"id": None, "username": f"api:{row['name']}", "role": row["role"], "full_name": row["name"]}
