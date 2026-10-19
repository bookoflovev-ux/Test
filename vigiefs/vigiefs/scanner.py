"""Analyse complète d'un dossier supervisé : volumétrie, typologie, âge, filtrage, rançongiciel."""

import heapq
import os
import shutil
import time
from collections import defaultdict

from .rules import Matcher, long_path, strip_long

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
              progress=None, cancel=None, junk=None, skip_roots=(), paths=None):
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
    junk_stats = defaultdict(lambda: {"count": 0, "size": 0, "samples": []})
    empty_dirs = {"count": 0, "samples": []}
    skip_real = {os.path.normcase(os.path.realpath(r)) for r in skip_roots if r}
    # Parcours en chemin étendu sous Windows : les éléments au-delà de 260 caractères restent visibles.
    root_io = long_path(root_real)
    base_len = len(root_io.rstrip("\\/")) + 1

    def rel_of(p):
        return p[base_len:].replace("\\", "/") if len(p) > base_len else ""

    if paths is not None:
        paths.directory("")
    stack = [root_io]
    while stack:
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError as exc:
            error_count += 1
            if len(errors) < 20:
                errors.append({"path": rel_of(current), "error": exc.strerror or str(exc)})
            continue
        names = []
        with it:
            has_entries = False
            for entry in it:
                has_entries = True
                names.append(entry.name)
                try:
                    if entry.is_symlink() or _is_junction(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if skip_real and os.path.normcase(strip_long(entry.path)) in skip_real:
                            continue
                        dir_count += 1
                        stack.append(entry.path)
                        if paths is not None:
                            paths.directory(rel_of(entry.path))
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    error_count += 1
                    if len(errors) < 20:
                        errors.append({"path": rel_of(entry.path), "error": exc.strerror or str(exc)})
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

                rel = rel_of(entry.path)
                if paths is not None:
                    paths.file(rel, name, size, mtime)
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

                if junk is not None:
                    cat = junk.classify(name, size, mtime)
                    if cat:
                        js = junk_stats[cat]
                        js["count"] += 1
                        js["size"] += size
                        if len(js["samples"]) < 100:
                            js["samples"].append({"path": rel, "size": size, "mtime": mtime})

                if progress is not None and file_count % 500 == 0:
                    progress.update(file_count=file_count, total_size=total_size)
        if paths is not None:
            paths.listing(rel_of(current), names)
        if not has_entries and current != root_io:
            empty_dirs["count"] += 1
            if len(empty_dirs["samples"]) < 100:
                empty_dirs["samples"].append({"path": rel_of(current)})

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
        "junk": {k: v for k, v in junk_stats.items()},
        "junk_total": {"count": sum(v["count"] for v in junk_stats.values()),
                       "size": sum(v["size"] for v in junk_stats.values())},
        "empty_dirs": empty_dirs,
        "paths": paths.report() if paths is not None else None,
        "_folder_stats": paths.folder_stats() if paths is not None else None,
    }


def quarantine_file(root, rel_path, quarantine_dir, tag):
    """Déplace un fichier vers la zone de quarantaine en conservant son arborescence relative."""
    src = os.path.join(os.path.realpath(root), rel_path)
    dest = os.path.join(quarantine_dir, tag, rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        dest += f".{int(time.time())}"
    shutil.move(src, dest)
    return dest
