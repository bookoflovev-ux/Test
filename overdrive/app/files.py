"""Recherche des fichiers qui ne servent pas : gros fichiers, doublons, vieux téléchargements, dossiers vides."""
import hashlib
import os
import time
from collections import defaultdict

from send2trash import send2trash

from . import journal
from .system import IS_WINDOWS

SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "programdata", "$windows.~bt", "$windows.~ws",
             "recovery", "proc", "sys", "dev", "node_modules", ".git", "winsxs", "appdata"}
SKIP_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log.tmp"}


def default_roots():
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        return [home] + [f"{d}:\\" for d in "DEFGH" if os.path.exists(f"{d}:\\")]
    return [home]


def _walk(root, job=None):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith("$")]
        for f in filenames:
            if f.lower() in SKIP_FILES:
                continue
            fp = os.path.join(dirpath, f)
            try:
                st = os.stat(fp, follow_symlinks=False)
            except OSError:
                continue
            n += 1
            if job and n % 2000 == 0:
                job.update(None, f"{n} fichiers analysés… {dirpath[:80]}")
            yield fp, st


def large_files(job, roots=None, min_mb=500, limit=200):
    roots = roots or default_roots()
    min_b = min_mb * 1024 ** 2
    found = []
    for root in roots:
        for fp, st in _walk(root, job):
            if st.st_size >= min_b:
                found.append({"path": fp, "size": st.st_size, "mtime": st.st_mtime, "atime": st.st_atime})
    found.sort(key=lambda x: -x["size"])
    return {"files": found[:limit], "total": sum(f["size"] for f in found)}


def old_downloads(job, days=90):
    folder = os.path.join(os.path.expanduser("~"), "Downloads")
    limit = time.time() - days * 86400
    found = []
    if os.path.isdir(folder):
        for fp, st in _walk(folder, job):
            if st.st_mtime < limit and st.st_atime < limit:
                found.append({"path": fp, "size": st.st_size, "mtime": st.st_mtime, "atime": st.st_atime})
    found.sort(key=lambda x: -x["size"])
    return {"files": found, "total": sum(f["size"] for f in found)}


def _hash(path, first_only=False):
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        if first_only:
            h.update(f.read(65536))
        else:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def duplicates(job, roots=None, min_mb=1):
    roots = roots or [os.path.expanduser("~")]
    by_size = defaultdict(list)
    inodes = set()
    for root in roots:
        for fp, st in _walk(root, job):
            if st.st_size >= max(min_mb * 1024 ** 2, 1):
                key = (st.st_dev, st.st_ino)
                if st.st_ino and key in inodes:  # lien physique : même fichier, pas un doublon
                    continue
                inodes.add(key)
                by_size[st.st_size].append(fp)
    candidates = [paths for paths in by_size.values() if len(paths) > 1]
    groups = []
    for i, paths in enumerate(candidates):
        job.update(i / max(len(candidates), 1), f"Comparaison des contenus ({i}/{len(candidates)})")
        partial = defaultdict(list)
        for p in paths:
            try:
                partial[_hash(p, True)].append(p)
            except OSError:
                pass
        for same in partial.values():
            if len(same) < 2:
                continue
            full = defaultdict(list)
            for p in same:
                try:
                    full[_hash(p)].append(p)
                except OSError:
                    pass
            for dup in full.values():
                if len(dup) > 1:
                    size = os.path.getsize(dup[0])
                    dup.sort(key=lambda p: os.path.getmtime(p))
                    groups.append({"size": size, "paths": dup, "wasted": size * (len(dup) - 1)})
    groups.sort(key=lambda g: -g["wasted"])
    return {"groups": groups[:300], "wasted": sum(g["wasted"] for g in groups)}


def empty_folders(job, roots=None):
    roots = roots or [os.path.expanduser("~")]
    found = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False, onerror=lambda e: None):
            if any(part.lower() in SKIP_DIRS for part in dirpath.split(os.sep)):
                continue
            try:
                if dirpath != root and not os.listdir(dirpath):
                    found.append(dirpath)
            except OSError:
                pass
    return {"folders": found[:500]}


def trash(paths):
    """Envoie vers la corbeille (récupérable) plutôt que de supprimer définitivement."""
    ok, errors, size = [], [], 0
    for p in paths:
        try:
            s = os.path.getsize(p) if os.path.isfile(p) else 0
            send2trash(os.path.normpath(p))
            ok.append(p)
            size += s
        except Exception as e:
            errors.append({"path": p, "error": str(e)})
    journal.log("files", f"{len(ok)} élément(s) envoyé(s) à la corbeille ({size / 1024 ** 2:.0f} Mo)", "success")
    return {"trashed": ok, "errors": errors, "size": size}
