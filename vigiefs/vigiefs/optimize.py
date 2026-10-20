"""Optimisation du stockage : fichiers inutiles, nettoyage vers la corbeille, doublons."""

import fnmatch
import hashlib
import os
import time
from collections import defaultdict

from .config import merged_setting
from .rules import is_vm_file, long_path, relpath
from .storage import STUB_SUFFIX, is_within

# Catégories de fichiers inutiles. `age` = clé du paramètre d'ancienneté minimale (Paramètres › Stockage).
JUNK_CATEGORIES = [
    {
        "key": "temp", "label": "Fichiers temporaires", "age": "temp_days",
        "description": "Fichiers de verrouillage Office orphelins (~$…), .tmp, téléchargements interrompus.",
        "patterns": ["~$*", "~*.tmp", "*.tmp", "*.temp", ".~lock.*#", "*.crdownload", "*.partial", "*.part",
                     "*.download"],
    },
    {
        "key": "system", "label": "Fichiers système parasites", "age": None,
        "description": "Miniatures Windows (Thumbs.db), métadonnées macOS (.DS_Store, ._*).",
        "patterns": ["thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", ".ds_store", "._*"],
    },
    {
        "key": "backup", "label": "Sauvegardes automatiques", "age": "backup_days",
        "description": "Copies de sauvegarde d'applications : .bak, .old, .wbk (Word), .xlk (Excel), .asd…",
        "patterns": ["*.bak", "*.old", "*.wbk", "*.xlk", "*.asd", "*.sik", "*.bkp", "*.~bk"],
    },
    {
        "key": "logs", "label": "Journaux et vidages mémoire", "age": "logs_days",
        "description": "Fichiers .log, .dmp, .etl anciens.",
        "patterns": ["*.log", "*.dmp", "*.mdmp", "*.etl"],
    },
    {
        "key": "empty", "label": "Fichiers vides (0 octet)", "age": "empty_days",
        "description": "Fichiers sans contenu, souvent issus d'erreurs de copie ou de synchronisation.",
        "patterns": [],
    },
]
EMPTY_DIRS = {"key": "empty_dirs", "label": "Dossiers vides", "age": "empty_days",
              "description": "Dossiers ne contenant aucun fichier ni sous-dossier."}
CATEGORY_KEYS = [c["key"] for c in JUNK_CATEGORIES] + [EMPTY_DIRS["key"]]
SAFE_NAMES = {"desktop.ini", ".gitkeep", ".keep"}


class JunkClassifier:
    def __init__(self, ages):
        self.now = time.time()
        self.min_age = {c["key"]: (ages.get(c["age"], 0) if c["age"] else 0) * 86400 for c in JUNK_CATEGORIES}
        self.rules = [(c["key"], [p.lower() for p in c["patterns"]]) for c in JUNK_CATEGORIES if c["patterns"]]

    @classmethod
    def from_settings(cls, db):
        return cls(merged_setting(db, "cleanup"))

    def classify(self, name, size, mtime):
        low = name.lower()
        if low in SAFE_NAMES or name.endswith(STUB_SUFFIX) or is_vm_file(low):
            return None
        age = self.now - mtime
        for key, patterns in self.rules:
            if any(fnmatch.fnmatchcase(low, p) for p in patterns):
                if key == "system" and low.startswith("._") and size > 8192:
                    return None  # fichier AppleDouble improbable : on ne prend pas de risque
                return key if not self.min_age[key] or age >= self.min_age[key] else None
        if size == 0 and age >= self.min_age["empty"]:
            return "empty"
        return None


def run_cleanup(share, categories, recycle, actor, ctx, db, protected=()):
    """Déplace vers la corbeille les fichiers des catégories choisies (conditions réévaluées à l'exécution)."""
    root = os.path.realpath(share["path"])
    classifier = JunkClassifier.from_settings(db)
    skip = [recycle.root(), merged_setting(db, "archive").get("root"), *protected]
    moved = defaultdict(lambda: [0, 0])
    errors = []
    seen = 0
    file_cats = set(categories) - {"empty_dirs"}
    if file_cats:
        for dirpath, dirnames, filenames in os.walk(long_path(root)):
            ctx.check()
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                           and not any(r and is_within(os.path.join(dirpath, d), r) for r in skip)]
            for name in filenames:
                full = os.path.join(dirpath, name)
                seen += 1
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                cat = classifier.classify(name, st.st_size, st.st_mtime)
                if cat not in file_cats:
                    continue
                try:
                    recycle.discard(share, relpath(root, full), f"nettoyage : {cat}", actor, ctx.id)
                    moved[cat][0] += 1
                    moved[cat][1] += st.st_size
                except Exception as exc:  # noqa: BLE001
                    errors.append({"path": relpath(root, full), "error": str(exc)})
                ctx.progress(scanned=seen, moved=sum(v[0] for v in moved.values()),
                             freed=sum(v[1] for v in moved.values()))
    if "empty_dirs" in categories:
        min_age = merged_setting(db, "cleanup").get("empty_days", 7) * 86400
        now = time.time()
        for dirpath, dirnames, filenames in os.walk(long_path(root), topdown=False):
            ctx.check()
            if dirpath == root or any(r and is_within(dirpath, r) for r in skip) or os.path.islink(dirpath):
                continue
            try:
                if os.listdir(dirpath) or now - os.stat(dirpath).st_mtime < min_age:
                    continue
                recycle.discard(share, relpath(root, dirpath), "nettoyage : dossier vide", actor, ctx.id)
                moved["empty_dirs"][0] += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": relpath(root, dirpath), "error": str(exc)})
    total = sum(v[0] for v in moved.values())
    freed = sum(v[1] for v in moved.values())
    db.audit(actor, "nettoyage", share["path"], f"{total} élément(s), {freed} octets → corbeille")
    return {"moved": total, "freed": freed, "by_category": {k: {"count": v[0], "size": v[1]} for k, v in moved.items()},
            "errors": errors[:200], "error_count": len(errors)}


# ---------------------------------------------------------------- doublons
def _digest(path, limit=None):
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if limit and read >= limit:
                break
    return h.hexdigest()


def find_duplicates(shares, min_size=1024 * 1024, ctx=None, skip_roots=(), max_groups=2000):
    """Doublons sur un ou plusieurs dossiers : taille → empreinte partielle → SHA-256 complet."""
    by_size = defaultdict(list)
    scanned = 0
    for share in shares:
        root = os.path.realpath(share["path"])
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(long_path(root)):
            if ctx:
                ctx.check()
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                           and not any(r and is_within(os.path.join(dirpath, d), r) for r in skip_roots)]
            for fn in filenames:
                if fn.startswith("~$") or fn.endswith(STUB_SUFFIX) or is_vm_file(fn):
                    continue            # deux disques virtuels identiques ne sont pas des doublons à supprimer
                p = os.path.join(dirpath, fn)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if os.path.islink(p) or st.st_size < min_size:
                    continue
                scanned += 1
                by_size[st.st_size].append((share, p, st.st_mtime))
            if ctx:
                ctx.progress(phase="inventaire", scanned=scanned)

    candidates = [(s, files) for s, files in by_size.items() if len(files) > 1]
    total = sum(len(f) for _, f in candidates)
    hashed = 0
    groups = []
    for size, files in sorted(candidates, key=lambda x: x[0], reverse=True):
        partial = defaultdict(list)
        for item in files:
            if ctx:
                ctx.check()
            try:
                partial[_digest(item[1], 64 * 1024)].append(item)
            except OSError:
                pass
            hashed += 1
            if ctx:
                ctx.progress(phase="comparaison", hashed=hashed, to_hash=total, groups=len(groups))
        for same_head in partial.values():
            if len(same_head) < 2:
                continue
            full = defaultdict(list)
            for item in same_head:
                if ctx:
                    ctx.check()
                try:
                    full[_digest(item[1]) if size > 64 * 1024 else "h"].append(item)
                except OSError:
                    pass
            for h, same in full.items():
                if len(same) < 2:
                    continue
                if h == "h":
                    h = _digest(same[0][1])
                groups.append({
                    "hash": h, "size": size, "count": len(same), "wasted": size * (len(same) - 1),
                    "files": [{"share_id": s["id"], "share": s["name"], "path": relpath(s["path"], p), "mtime": m}
                              for s, p, m in sorted(same, key=lambda x: x[2])],
                })
        if len(groups) >= max_groups:
            break
    groups.sort(key=lambda g: g["wasted"], reverse=True)
    return {"groups": groups, "group_count": len(groups), "files_scanned": scanned,
            "wasted_total": sum(g["wasted"] for g in groups), "min_size": min_size}


def pick_keeper(group, strategy):
    files = group["files"]
    if strategy == "newest":
        return max(files, key=lambda f: f["mtime"])
    if strategy == "shortest":
        return min(files, key=lambda f: (len(f["path"]), f["path"]))
    return min(files, key=lambda f: f["mtime"])  # oldest : l'original présumé


def resolve_duplicates(groups, strategy, keep_map, shares, recycle, actor, ctx):
    """Envoie à la corbeille toutes les copies sauf une, après re-vérification de l'empreinte."""
    removed = freed = 0
    skipped = []
    for n, group in enumerate(groups):
        ctx.check()
        ctx.progress(done=n, total=len(groups), removed=removed, freed=freed)
        key = keep_map.get(group["hash"])
        keeper = next((f for f in group["files"] if f"{f['share_id']}:{f['path']}" == key), None) \
            if key else None
        keeper = keeper or pick_keeper(group, strategy)
        keep_share = shares.get(keeper["share_id"])
        if not keep_share:
            skipped.append({"path": keeper["path"], "reason": "dossier supervisé supprimé"})
            continue
        keep_path = os.path.join(os.path.realpath(keep_share["path"]), keeper["path"])
        try:
            if _digest(keep_path) != group["hash"]:
                skipped.append({"path": keeper["path"], "reason": "l'exemplaire conservé a changé"})
                continue
        except OSError:
            skipped.append({"path": keeper["path"], "reason": "exemplaire conservé introuvable"})
            continue
        for f in group["files"]:
            if f is keeper:
                continue
            share = shares.get(f["share_id"])
            if not share:
                continue
            full = os.path.join(os.path.realpath(share["path"]), f["path"])
            try:
                if _digest(full) != group["hash"]:
                    skipped.append({"path": f["path"], "reason": "contenu modifié depuis l'analyse"})
                    continue
                recycle.discard(share, f["path"], f"doublon de {keep_share['name']}/{keeper['path']}", actor, ctx.id)
                removed += 1
                freed += group["size"]
            except OSError as exc:
                skipped.append({"path": f["path"], "reason": str(exc)})
    ctx.progress(done=len(groups), total=len(groups), removed=removed, freed=freed, force=True)
    return {"removed": removed, "freed": freed, "skipped": skipped[:300], "skipped_count": len(skipped)}
