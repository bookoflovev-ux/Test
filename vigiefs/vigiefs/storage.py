"""Corbeille VigieFS (suppression réversible) et archivage à froid (déplacement vérifié + restauration)."""

import fnmatch
import hashlib
import os
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path

from .config import merged_setting
from .rules import normalize_extensions, relpath, safe_join

STUB_SUFFIX = " [archivé].url"


def _unique(path):
    """Ajoute un suffixe horodaté si `path` existe déjà."""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = f"{base} (restauré {stamp}){ext}"
    n = 2
    while os.path.lexists(candidate):
        candidate = f"{base} (restauré {stamp}-{n}){ext}"
        n += 1
    return candidate


def _safe_component(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "_"


def is_within(path, root):
    try:
        p, r = os.path.realpath(path), os.path.realpath(root)
        return os.path.commonpath([p, r]) == r
    except ValueError:
        return False


def tree_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- corbeille
class RecycleBin:
    def __init__(self, db, data_dir):
        self.db = db
        self.data_dir = data_dir

    def root(self):
        return merged_setting(self.db, "recycle").get("dir") or os.path.join(self.data_dir, "corbeille")

    def discard(self, share, rel, reason, actor, job_id=None):
        """Déplace un fichier / dossier du partage vers la corbeille. Renvoie l'id de l'élément."""
        src = safe_join(share["path"], rel)
        if src == os.path.realpath(share["path"]):
            raise ValueError("Impossible de supprimer la racine du dossier supervisé")
        if not os.path.lexists(src):
            raise FileNotFoundError(rel)
        is_dir = os.path.isdir(src) and not os.path.islink(src)
        st = os.lstat(src)
        size = tree_size(src) if is_dir else st.st_size
        empty_dir = is_dir and not os.listdir(src)
        item_id = self.db.execute(
            "INSERT INTO recycle_items(share_id, share_root, rel_path, is_dir, size, mtime, reason, job_id, "
            "deleted_at, deleted_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (share["id"], share["path"], relpath(share["path"], src), int(is_dir), size, st.st_mtime, reason,
             job_id, time.time(), actor))
        try:
            if empty_dir:
                os.rmdir(src)
                stored = None
            else:
                stored = os.path.join(self.root(), str(share["id"]), str(item_id), os.path.basename(src))
                os.makedirs(os.path.dirname(stored), exist_ok=True)
                shutil.move(src, stored)
        except Exception:
            self.db.execute("DELETE FROM recycle_items WHERE id=?", (item_id,))
            raise
        self.db.execute("UPDATE recycle_items SET stored_path=? WHERE id=?", (stored, item_id))
        return item_id

    def restore(self, item_id, actor):
        item = self.db.query_one("SELECT * FROM recycle_items WHERE id=?", (item_id,))
        if not item or item["status"] != "stored":
            raise ValueError("Élément introuvable ou déjà restauré/purgé")
        target = _unique(safe_join(item["share_root"], item["rel_path"]))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if item["stored_path"] is None:
            os.makedirs(target, exist_ok=True)
        else:
            if not os.path.lexists(item["stored_path"]):
                raise FileNotFoundError("Le contenu de la corbeille a disparu du disque")
            shutil.move(item["stored_path"], target)
            shutil.rmtree(os.path.dirname(item["stored_path"]), ignore_errors=True)
        self.db.execute("UPDATE recycle_items SET status='restored', restored_at=?, restored_path=? WHERE id=?",
                        (time.time(), target, item_id))
        self.db.audit(actor, "restauration corbeille", target)
        return target

    def purge(self, item_id, actor="système"):
        item = self.db.query_one("SELECT * FROM recycle_items WHERE id=?", (item_id,))
        if not item or item["status"] != "stored":
            return False
        if item["stored_path"]:
            container = os.path.dirname(item["stored_path"])
            shutil.rmtree(container, ignore_errors=True)
        self.db.execute("UPDATE recycle_items SET status='purged', restored_at=? WHERE id=?", (time.time(), item_id))
        if actor != "système":
            self.db.audit(actor, "purge corbeille", item["rel_path"])
        return True

    def purge_expired(self):
        days = int(merged_setting(self.db, "recycle").get("retention_days", 30))
        if days <= 0:
            return 0
        rows = self.db.query("SELECT id FROM recycle_items WHERE status='stored' AND deleted_at < ?",
                             (time.time() - days * 86400,))
        for r in rows:
            self.purge(r["id"])
        return len(rows)


# ---------------------------------------------------------------- archivage à froid
def share_policy(share, overrides=None):
    p = {
        "age_days": share.get("archive_age_days") or 730,
        "min_size_kb": share.get("archive_min_size_kb") or 0,
        "extensions": share.get("archive_extensions") or "",
        "exclude": share.get("archive_exclude") or "",
        "criterion": share.get("archive_criterion") or "mtime",
        "stub": bool(share.get("archive_stub", 1)),
    }
    p.update({k: v for k, v in (overrides or {}).items() if v is not None})
    p["extensions"] = normalize_extensions(p["extensions"])
    if isinstance(p["exclude"], str):
        p["exclude"] = [x.strip() for x in re.split(r"[,;\n]", p["exclude"]) if x.strip()]
    return p


def _excluded(rel, name, patterns):
    low_rel, low_name = rel.lower(), name.lower()
    for pat in patterns:
        pat = pat.lower().replace("\\", "/")
        if fnmatch.fnmatchcase(low_name, pat) or fnmatch.fnmatchcase(low_rel, pat) \
                or low_rel.startswith(pat.rstrip("/*") + "/"):
            return True
    return False


def iter_archive_candidates(share, policy, ctx=None, skip_roots=()):
    root = os.path.realpath(share["path"])
    cutoff = time.time() - int(policy["age_days"]) * 86400
    min_size = int(policy["min_size_kb"]) * 1024
    exts = tuple(policy["extensions"])
    use_atime = policy["criterion"] == "atime"
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if ctx:
            ctx.check()
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))
                       and not any(is_within(os.path.join(dirpath, d), r) for r in skip_roots if r)]
        for name in filenames:
            if name.endswith(STUB_SUFFIX) or name.startswith("~$"):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            scanned += 1
            if ctx and scanned % 1000 == 0:
                ctx.progress(scanned=scanned)
            rel = relpath(root, full)
            if st.st_size < min_size:
                continue
            if exts and not name.lower().endswith(exts):
                continue
            if policy["exclude"] and _excluded(rel, name, policy["exclude"]):
                continue
            ref = max(st.st_atime, st.st_mtime) if use_atime else st.st_mtime
            if ref < cutoff:
                yield rel, st.st_size, st.st_mtime, st.st_atime


class ArchiveService:
    def __init__(self, db, recycle):
        self.db = db
        self.recycle = recycle

    def settings(self):
        return merged_setting(self.db, "archive")

    def _skip_roots(self):
        return [self.settings().get("root"), self.recycle.root()]

    def simulate(self, share, policy, ctx=None):
        count = size = 0
        by_ext = defaultdict(lambda: [0, 0])
        by_folder = defaultdict(lambda: [0, 0])
        samples = []
        oldest = None
        for rel, sz, mtime, _ in iter_archive_candidates(share, policy, ctx, self._skip_roots()):
            count += 1
            size += sz
            ext = os.path.splitext(rel)[1].lower() or "(sans)"
            by_ext[ext][0] += 1
            by_ext[ext][1] += sz
            folder = rel.split("/", 1)[0] if "/" in rel else "(racine)"
            by_folder[folder][0] += 1
            by_folder[folder][1] += sz
            oldest = mtime if oldest is None else min(oldest, mtime)
            samples.append((sz, rel, mtime))
            if len(samples) > 400:
                samples.sort(reverse=True)
                del samples[200:]
            if ctx:
                ctx.progress(candidates=count, size=size)
        samples.sort(reverse=True)

        def top(d):
            return [{"name": k, "count": v[0], "size": v[1]}
                    for k, v in sorted(d.items(), key=lambda kv: kv[1][1], reverse=True)[:15]]

        return {"count": count, "size": size, "oldest": oldest, "policy": policy,
                "by_extension": top(by_ext), "by_folder": top(by_folder),
                "samples": [{"path": p, "size": s, "mtime": m} for s, p, m in samples[:200]]}

    def run(self, share, policy, ctx, actor, job_id=None):
        settings = self.settings()
        root = settings.get("root")
        if not root:
            raise ValueError("Aucun stockage d'archive configuré (Paramètres › Stockage)")
        if is_within(root, share["path"]):
            raise ValueError("Le stockage d'archive ne doit pas se trouver dans le dossier supervisé")
        client = self.db.query_one("SELECT name FROM clients WHERE id=?", (share["client_id"],))
        base = os.path.join(root, _safe_component(client["name"] if client else "client"),
                            _safe_component(share["name"]))
        verify = bool(settings.get("verify_hash", True))
        done = size = 0
        failed = []
        candidates = list(iter_archive_candidates(share, policy, ctx, self._skip_roots()))
        ctx and ctx.progress(total=len(candidates), done=0, force=True)
        for rel, sz, mtime, _ in candidates:
            if ctx:
                ctx.check()
            src = os.path.join(os.path.realpath(share["path"]), rel)
            dest = os.path.join(base, *rel.split("/"))
            try:
                dest = archive_copy(src, dest, verify)
                stub = None
                if policy.get("stub"):
                    stub = src + STUB_SUFFIX
                    write_stub(stub, dest)
                os.remove(src)
                self.db.execute(
                    "INSERT INTO archive_items(share_id, share_root, rel_path, archive_path, stub_path, size, mtime, "
                    "job_id, archived_at, archived_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (share["id"], share["path"], rel, dest, stub, sz, mtime, job_id, time.time(), actor))
                done += 1
                size += sz
            except Exception as exc:  # noqa: BLE001
                failed.append({"path": rel, "error": str(exc)})
            if ctx:
                ctx.progress(done=done, size=size, failed=len(failed))
        self.db.execute("UPDATE shares SET last_archive_at=? WHERE id=?", (time.time(), share["id"]))
        self.db.audit(actor, "archivage à froid", share["path"], f"{done} fichiers, {size} octets")
        return {"archived": done, "size": size, "failed": failed[:200], "failed_count": len(failed),
                "destination": base}

    def restore(self, item_ids, actor, ctx=None):
        restored, failed = 0, []
        for n, item_id in enumerate(item_ids):
            if ctx:
                ctx.check()
                ctx.progress(done=n, total=len(item_ids))
            item = self.db.query_one("SELECT * FROM archive_items WHERE id=?", (item_id,))
            if not item or item["status"] != "archived":
                continue
            try:
                target = safe_join(item["share_root"], item["rel_path"])
                if item["stub_path"] and os.path.exists(item["stub_path"]):
                    os.remove(item["stub_path"])
                target = _unique(target)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                archive_copy(item["archive_path"], target, verify=True)
                os.remove(item["archive_path"])
                self.db.execute("UPDATE archive_items SET status='restored', restored_at=? WHERE id=?",
                                (time.time(), item_id))
                restored += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"path": item["rel_path"], "error": str(exc)})
        self.db.audit(actor, "restauration d'archives", f"{restored} fichier(s)")
        return {"restored": restored, "failed": failed}


def _copy_hash(src, dest):
    h = hashlib.sha256()
    with open(src, "rb") as fi, open(dest, "wb") as fo:
        while True:
            chunk = fi.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            fo.write(chunk)
    return h.hexdigest()


def _hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_copy(src, dest, verify=True):
    """Copie vérifiée (taille + SHA-256) qui conserve les horodatages ; renvoie le chemin final."""
    if os.path.exists(dest):
        base, ext = os.path.splitext(dest)
        dest = f"{base}.{int(time.time())}{ext}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".vigiefs-tmp"
    digest = _copy_hash(src, tmp)
    shutil.copystat(src, tmp)
    if os.path.getsize(tmp) != os.path.getsize(src) or (verify and _hash(tmp) != digest):
        os.remove(tmp)
        raise IOError("Vérification de la copie échouée")
    os.replace(tmp, dest)
    return dest


def write_stub(stub_path, target):
    """Raccourci Windows (.url) pointant vers le fichier archivé."""
    with open(stub_path, "w", encoding="utf-8") as fh:
        fh.write("[InternetShortcut]\r\nURL=" + Path(target).as_uri() + "\r\n")
