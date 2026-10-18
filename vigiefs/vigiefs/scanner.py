"""Analyse complète d'un dossier supervisé : volumétrie, typologie, âge, filtrage, rançongiciel."""

import hashlib
import heapq
import os
import shutil
import time
from collections import defaultdict

from .rules import Matcher, relpath

AGE_BUCKETS = [
    ("< 30 jours", 30),
    ("30 à 180 jours", 180),
    ("6 mois à 1 an", 365),
    ("1 à 3 ans", 365 * 3),
    ("> 3 ans", None),
]
TOP_N = 50
MAX_LISTED = 500


class ScanCancelled(Exception):
    pass


def _is_junction(entry):
    fn = getattr(entry, "is_junction", None)
    try:
        return bool(fn and fn())
    except OSError:
        return False


def scan_tree(root, stale_days=365, screening=None, ransom_ext=None, ransom_notes=None,
              progress=None, cancel=None):
    """Parcourt `root` (sans suivre les liens) et renvoie un rapport agrégé (dict)."""
    screening = screening or Matcher()
    ransom_ext = ransom_ext or Matcher()
    ransom_notes = ransom_notes or Matcher()
    now = time.time()
    stale_limit = now - stale_days * 86400 if stale_days else None
    root_real = os.path.realpath(root)

    total_size = file_count = dir_count = error_count = 0
    stale_count = stale_size = 0
    errors = []
    by_ext = defaultdict(lambda: [0, 0])
    ages = [[0, 0] for _ in AGE_BUCKETS]
    largest, stale_largest = [], []
    top_dirs = defaultdict(lambda: [0, 0])
    screened, ransom = [], []
    newest_mtime = 0

    stack = [root_real]
    while stack:
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError as exc:
            error_count += 1
            if len(errors) < 20:
                errors.append({"path": relpath(root_real, current), "error": exc.strerror or str(exc)})
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_symlink() or _is_junction(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dir_count += 1
                        stack.append(entry.path)
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    error_count += 1
                    if len(errors) < 20:
                        errors.append({"path": relpath(root_real, entry.path), "error": exc.strerror or str(exc)})
                    continue

                size, mtime = st.st_size, st.st_mtime
                file_count += 1
                total_size += size
                newest_mtime = max(newest_mtime, mtime)
                name = entry.name
                ext = os.path.splitext(name)[1].lower() or "(sans)"
                by_ext[ext][0] += 1
                by_ext[ext][1] += size

                age_days = (now - mtime) / 86400
                for i, (_, limit) in enumerate(AGE_BUCKETS):
                    if limit is None or age_days < limit:
                        ages[i][0] += 1
                        ages[i][1] += size
                        break

                rel = relpath(root_real, entry.path)
                first = rel.split("/", 1)[0] if "/" in rel else "(racine)"
                top_dirs[first][0] += 1
                top_dirs[first][1] += size

                item = (size, rel, mtime)
                if len(largest) < TOP_N:
                    heapq.heappush(largest, item)
                elif size > largest[0][0]:
                    heapq.heapreplace(largest, item)

                if stale_limit and mtime < stale_limit:
                    stale_count += 1
                    stale_size += size
                    if len(stale_largest) < TOP_N:
                        heapq.heappush(stale_largest, item)
                    elif size > stale_largest[0][0]:
                        heapq.heapreplace(stale_largest, item)

                if screening and screening.match(name) and len(screened) < MAX_LISTED:
                    screened.append({"path": rel, "size": size, "mtime": mtime})
                if (ransom_ext.match(name) or ransom_notes.match(name)) and len(ransom) < MAX_LISTED:
                    ransom.append({"path": rel, "size": size, "mtime": mtime,
                                   "kind": "note" if ransom_notes.match(name) else "extension"})

                if progress is not None and file_count % 500 == 0:
                    progress.update(file_count=file_count, total_size=total_size)

    def files(heap):
        return [{"path": p, "size": s, "mtime": m} for s, p, m in sorted(heap, reverse=True)]

    ext_sorted = sorted(by_ext.items(), key=lambda kv: kv[1][1], reverse=True)
    ext_top = [{"ext": k, "count": v[0], "size": v[1]} for k, v in ext_sorted[:20]]
    rest = ext_sorted[20:]
    if rest:
        ext_top.append({"ext": "Autres", "count": sum(v[0] for _, v in rest), "size": sum(v[1] for _, v in rest)})

    try:
        du = shutil.disk_usage(root_real)
        disk = {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        disk = None

    return {
        "total_size": total_size,
        "file_count": file_count,
        "dir_count": dir_count,
        "error_count": error_count,
        "errors": errors,
        "stale_count": stale_count,
        "stale_size": stale_size,
        "newest_mtime": newest_mtime or None,
        "by_extension": ext_top,
        "ages": [{"label": AGE_BUCKETS[i][0], "count": c, "size": s} for i, (c, s) in enumerate(ages)],
        "largest": files(largest),
        "stale_largest": files(stale_largest),
        "top_folders": sorted(
            ({"name": k, "count": v[0], "size": v[1]} for k, v in top_dirs.items()),
            key=lambda d: d["size"], reverse=True,
        )[:30],
        "screened": screened,
        "ransomware": ransom,
        "disk": disk,
    }


def find_duplicates(root, min_size=1024 * 1024, max_groups=200, cancel=None):
    """Doublons : regroupement par taille, puis empreinte partielle, puis SHA-256 complet."""
    root_real = os.path.realpath(root)
    by_size = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root_real):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                if os.path.islink(p):
                    continue
                size = os.path.getsize(p)
            except OSError:
                continue
            if size >= min_size:
                by_size[size].append(p)

    def digest(path, limit=None):
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

    groups = []
    for size, paths in sorted(by_size.items(), reverse=True):
        if len(paths) < 2:
            continue
        partial = defaultdict(list)
        for p in paths:
            try:
                partial[digest(p, 64 * 1024)].append(p)
            except OSError:
                continue
        for candidates in partial.values():
            if len(candidates) < 2:
                continue
            full = defaultdict(list)
            for p in candidates:
                if cancel is not None and cancel.is_set():
                    raise ScanCancelled()
                try:
                    full[digest(p)].append(p)
                except OSError:
                    continue
            for h, same in full.items():
                if len(same) > 1:
                    groups.append({
                        "hash": h, "size": size, "count": len(same),
                        "wasted": size * (len(same) - 1),
                        "files": [relpath(root_real, p) for p in same],
                    })
        if len(groups) >= max_groups:
            break
    groups.sort(key=lambda g: g["wasted"], reverse=True)
    return {"groups": groups[:max_groups], "wasted_total": sum(g["wasted"] for g in groups)}


def quarantine_file(root, rel_path, quarantine_dir, tag):
    """Déplace un fichier vers la zone de quarantaine en conservant son arborescence relative."""
    src = os.path.join(os.path.realpath(root), rel_path)
    dest = os.path.join(quarantine_dir, tag, rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        dest += f".{int(time.time())}"
    shutil.move(src, dest)
    return dest
