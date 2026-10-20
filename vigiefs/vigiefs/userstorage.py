"""Occupation du stockage par utilisateur.

Pendant l'analyse d'un dossier supervisé, chaque fichier est attribué :
- à l'utilisateur dont c'est le dossier personnel, si le fichier se trouve sous une « racine de dossiers
  personnels » du dossier supervisé (ex. « Utilisateurs » : Utilisateurs/alice/… → alice). Utile quand les
  propriétaires NTFS ont été perdus (migration par copie : tout appartient alors aux Administrateurs) ;
- sinon à son propriétaire (NTFS via l'API Windows, POSIX via l'uid).
Résultat : volume, nombre de fichiers, données anciennes, dossiers et types de fichiers par utilisateur,
historique quotidien (croissance) et contrôle de la capacité allouée à chacun.
"""

import json
import os
import time
from collections import Counter, defaultdict

from .activity import user_key

try:
    import pwd
except ImportError:          # Windows
    pwd = None


class OwnerResolver:
    """Propriétaire d'un fichier → (clé, nom affiché). Les SID et uid sont mis en cache."""

    def __init__(self):
        self.cache = {}
        self.win = None
        if os.name == "nt":
            try:
                self.win = _WinOwner()
            except (OSError, AttributeError):
                self.win = None

    def owner(self, path, st):
        if self.win is not None:
            return self.win.owner(path, self.cache)
        uid = getattr(st, "st_uid", None)
        if uid is None:
            return None
        if uid not in self.cache:
            try:
                name = pwd.getpwuid(uid).pw_name if pwd else str(uid)
            except KeyError:
                name = str(uid)
            self.cache[uid] = (user_key(name), name)
        return self.cache[uid]


class _WinOwner:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32")
        P = ctypes.POINTER(ctypes.c_void_p)
        self.get = adv.GetNamedSecurityInfoW
        self.get.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, P, P, P, P, P]
        self.get.restype = wintypes.DWORD
        self.to_str = adv.ConvertSidToStringSidW
        self.to_str.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        self.to_str.restype = wintypes.BOOL
        self.lookup = adv.LookupAccountSidW
        self.lookup.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        self.lookup.restype = wintypes.BOOL
        self.free = self.kernel.LocalFree
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = ctypes.c_void_p

    def owner(self, path, cache):
        ct, wt = self.ct, self.wt
        psid, psd = ct.c_void_p(), ct.c_void_p()
        if self.get(path, 1, 1, ct.byref(psid), None, None, None, ct.byref(psd)) != 0:   # SE_FILE_OBJECT, OWNER
            return None
        try:
            s = wt.LPWSTR()
            if not self.to_str(psid, ct.byref(s)):
                return None
            sid = s.value
            self.free(s)
            if sid not in cache:
                name, dom = ct.create_unicode_buffer(256), ct.create_unicode_buffer(256)
                n, d, use = wt.DWORD(256), wt.DWORD(256), wt.DWORD()
                if self.lookup(None, psid, name, ct.byref(n), dom, ct.byref(d), ct.byref(use)) and name.value:
                    full = f"{dom.value}\\{name.value}" if dom.value else name.value
                    cache[sid] = (user_key(full), full)
                else:
                    cache[sid] = (sid.lower(), f"{sid} (compte supprimé)")
            return cache[sid]
        finally:
            self.free(psd)


class OwnerCollector:
    """Agrège l'occupation par utilisateur pendant scan_tree."""

    def __init__(self, share, resolver=None):
        self.homes = [r.strip().strip("/\\").replace("\\", "/") for r in (share.get("home_roots") or "").split(",")
                      if r.strip().strip("/\\")]
        self.resolver = resolver or OwnerResolver()
        self.users = defaultdict(lambda: {"files": 0, "size": 0, "stale": 0, "newest": 0, "oldest": None,
                                          "folders": Counter(), "exts": Counter(), "via": "owner", "display": None})
        self.unresolved = 0

    def _home(self, rel):
        for root in self.homes:
            if rel.startswith(root + "/"):
                rest = rel[len(root) + 1:]
                if "/" in rest:
                    name = rest.split("/", 1)[0]
                    return user_key(name), name
        return None

    def file(self, rel, path, st, size, mtime, stale):
        who = self._home(rel)
        via = "home" if who else "owner"
        if not who:
            try:
                who = self.resolver.owner(path, st)
            except OSError:
                who = None
        if not who:
            self.unresolved += 1
            return
        u = self.users[who[0]]
        u["display"] = u["display"] or who[1]
        u["via"] = via if u["files"] == 0 or u["via"] == via else "mixte"
        u["files"] += 1
        u["size"] += size
        if stale:
            u["stale"] += size
        u["newest"] = max(u["newest"], mtime)
        u["oldest"] = mtime if u["oldest"] is None else min(u["oldest"], mtime)
        u["folders"][rel.split("/", 1)[0] if "/" in rel else "(racine)"] += size
        u["exts"][os.path.splitext(rel)[1].lower() or "(sans)"] += size

    def report(self):
        users = [{"user": k, "display": v["display"], "files": v["files"], "size": v["size"], "stale_size": v["stale"],
                  "newest": v["newest"] or None, "oldest": v["oldest"], "via": v["via"],
                  "folders": [{"name": n, "size": s} for n, s in v["folders"].most_common(12)],
                  "exts": [{"ext": e, "size": s} for e, s in v["exts"].most_common(8)]}
                 for k, v in self.users.items()]
        users.sort(key=lambda u: u["size"], reverse=True)
        return {"users": users, "unresolved": self.unresolved}


def store(db, share_id, report, resolve=None, now=None):
    """Remplace l'occupation par utilisateur du dossier ; renvoie {utilisateur: taille précédente}."""
    now = now or time.time()
    prev = {r["user"]: r["size"] for r in db.query("SELECT user, size FROM user_storage WHERE share_id=?", (share_id,))}
    rows = []
    merged = {}
    for u in report.get("users", []):
        key = resolve(u["user"]) if resolve else u["user"]
        if key in merged:                       # même personne, propriétaire et dossier personnel
            m = merged[key]
            m["files"] += u["files"]
            m["size"] += u["size"]
            m["stale_size"] += u["stale_size"]
            m["via"] = "mixte"
            continue
        merged[key] = dict(u)
    for key, u in merged.items():
        rows.append((share_id, key, u["display"], u["files"], u["size"], u["stale_size"], u["newest"], u["oldest"],
                     json.dumps(u["folders"], ensure_ascii=False), json.dumps(u["exts"]), u["via"], now))
    db.execute("DELETE FROM user_storage WHERE share_id=?", (share_id,))
    db.executemany("INSERT INTO user_storage(share_id, user, display, files, size, stale_size, newest, oldest, folders, "
                   "exts, via, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    db.executemany("INSERT INTO user_storage_history(share_id, user, day, size, files) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(share_id, user, day) DO UPDATE SET size=excluded.size, files=excluded.files",
                   [(share_id, k, day, u["size"], u["files"]) for k, u in merged.items()])
    return prev, merged


def totals(db):
    """{utilisateur: {size, files, shares}} tous dossiers confondus."""
    out = {}
    for r in db.query("SELECT user, SUM(size) AS size, SUM(files) AS files, COUNT(*) AS shares, MAX(display) AS display "
                      "FROM user_storage GROUP BY user"):
        out[r["user"]] = r
    return out


def capacity_of(profile, cfg):
    cap = int((profile or {}).get("capacity_bytes") or 0)
    if cap:
        return cap
    return int(float(cfg.get("default_capacity_gb", 0) or 0) * 1024 ** 3)


def after_scan(engine, share, report, now=None):
    """Enregistre l'occupation, détecte les croissances brutales et contrôle les capacités."""
    from .config import merged_setting
    from .engine import fmt_bytes
    owners = report.pop("_owners", None)
    if owners is None:
        return
    security = getattr(engine, "security", None)
    resolve = security.store.resolve if security else None
    prev, cur = store(engine.db, share["id"], owners, resolve, now)
    report["owners"] = {"users": len(cur), "unresolved": owners.get("unresolved", 0),
                        "top": [{"user": k, "display": u["display"], "size": u["size"]}
                                for k, u in sorted(cur.items(), key=lambda kv: -kv[1]["size"])[:10]]}
    cfg = merged_setting(engine.db, "security")
    growth = float((cfg.get("thresholds") or {}).get("storage_growth_gb", 5) or 0) * 1024 ** 3
    if security and security.analyzer and growth and prev:
        day = time.strftime("%Y%m%d", time.localtime(now or time.time()))
        found = []
        for user, u in cur.items():
            delta = u["size"] - prev.get(user, u["size"])
            if delta >= growth:
                found.append(security.analyzer.finding(
                    user, "storage_growth", f"{share['id']}:{day}", "warning", 10, "Croissance brutale du volume stocké",
                    f"{u['display'] or user} occupe {fmt_bytes(u['size'])} dans « {share['name']} », soit "
                    f"{fmt_bytes(delta)} de plus qu'à l'analyse précédente.",
                    {"before": prev.get(user), "after": u["size"], "share": share["name"]}, share["id"]))
        if found:
            security.analyzer.cfg = cfg
            security.analyzer.record(found)
    check_capacity(engine, cfg)


def check_capacity(engine, cfg=None):
    from .config import merged_setting
    from .engine import fmt_bytes
    cfg = cfg or merged_setting(engine.db, "security")
    profiles = {r["user"]: r for r in engine.db.query("SELECT user, capacity_bytes FROM user_profiles")}
    warn = float(cfg.get("capacity_warn_pct", 90))
    over = []
    for user, t in totals(engine.db).items():
        cap = capacity_of(profiles.get(user), cfg)
        fp = f"ucap:{user}"
        if not cap:
            engine.alerts.resolve(fp)
            continue
        pct = t["size"] * 100.0 / cap
        if pct >= warn:
            over.append(user)
            engine.alerts.raise_alert(
                fp, "critical" if pct >= 100 else "warning", "quota",
                f"Capacité {'dépassée' if pct >= 100 else 'presque atteinte'} — {t['display'] or user}",
                f"{t['display'] or user} occupe {fmt_bytes(t['size'])} sur les {fmt_bytes(cap)} qui lui sont alloués "
                f"({pct:.0f} %), dans {t['shares']} dossier(s) supervisé(s).",
                details={"user": user, "size": t["size"], "capacity": cap, "pct": round(pct, 1)})
        else:
            engine.alerts.resolve(fp)
    return over
