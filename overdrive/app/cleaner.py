"""Nettoyage des fichiers inutiles : temporaires, caches, dumps, corbeille... (analyse avant suppression)."""
import ctypes
import glob
import os
import shutil
import time

from . import journal
from .system import IS_WINDOWS, env_path, run

L = "%LOCALAPPDATA%"
R = "%APPDATA%"
WIN = "%SystemRoot%"


def _chromium(base):
    return [f"{base}\\User Data\\*\\{d}" for d in ("Cache\\Cache_Data", "Code Cache", "GPUCache", "Service Worker\\CacheStorage")]


# id: (libellé, description, motifs de chemins, âge minimum en heures, coché par défaut, avertissement)
WINDOWS_CATEGORIES = {
    "user_temp": ("Fichiers temporaires utilisateur", "Dossier %TEMP% laissé par les installeurs et applications.",
                  ["%TEMP%"], 24, True, None),
    "windows_temp": ("Fichiers temporaires Windows", "C:\\Windows\\Temp.", [f"{WIN}\\Temp"], 24, True, None),
    "wu_cache": ("Cache Windows Update", "Paquets de mises à jour déjà installés.",
                 [f"{WIN}\\SoftwareDistribution\\Download"], 72, True, None),
    "delivery_opt": ("Optimisation de distribution", "Cache de partage des mises à jour entre PC.",
                     [f"{WIN}\\ServiceProfiles\\NetworkService\\AppData\\Local\\Microsoft\\Windows\\DeliveryOptimization\\Cache"], 0, True, None),
    "crash_dumps": ("Rapports d'erreurs et dumps", "Minidumps, rapports WER et dumps de plantage.",
                    [f"{L}\\CrashDumps", f"{WIN}\\Minidump", f"{WIN}\\MEMORY.DMP", f"{L}\\Microsoft\\Windows\\WER",
                     "%ProgramData%\\Microsoft\\Windows\\WER"], 0, True, None),
    "thumbnails": ("Cache des miniatures", "Reconstruit automatiquement par l'Explorateur.",
                   [f"{L}\\Microsoft\\Windows\\Explorer\\thumbcache_*.db", f"{L}\\Microsoft\\Windows\\Explorer\\iconcache_*.db"], 0, True, None),
    "win_logs": ("Journaux Windows", "Anciens logs CBS/DISM et journaux d'installation.",
                 [f"{WIN}\\Logs\\CBS\\*.log", f"{WIN}\\Logs\\DISM\\*.log", f"{WIN}\\Panther\\*.log"], 72, True, None),
    "browser_chrome": ("Cache Google Chrome", "Cache web (n'efface ni mots de passe ni historique).",
                       _chromium(f"{L}\\Google\\Chrome"), 0, True, None),
    "browser_edge": ("Cache Microsoft Edge", "Cache web (n'efface ni mots de passe ni historique).",
                     _chromium(f"{L}\\Microsoft\\Edge"), 0, True, None),
    "browser_brave": ("Cache Brave / Opera GX", "Cache web.",
                      _chromium(f"{L}\\BraveSoftware\\Brave-Browser") + [f"{L}\\Opera Software\\Opera GX Stable\\Cache"], 0, True, None),
    "browser_firefox": ("Cache Firefox", "Cache web.", [f"{L}\\Mozilla\\Firefox\\Profiles\\*\\cache2"], 0, True, None),
    "discord": ("Cache Discord", "Images et médias mis en cache.",
                [f"{R}\\discord\\Cache", f"{R}\\discord\\Code Cache", f"{R}\\discord\\GPUCache"], 0, True, None),
    "steam": ("Logs et dumps Steam", "Journaux, dumps et cache web de Steam.",
              ["%ProgramFiles(x86)%\\Steam\\logs", "%ProgramFiles(x86)%\\Steam\\dumps",
               "%ProgramFiles(x86)%\\Steam\\appcache\\httpcache"], 0, True, None),
    "shader_cache": ("Caches de shaders GPU", "DirectX / NVIDIA / AMD. À vider après une mise à jour de pilote.",
                     [f"{L}\\D3DSCache", f"{L}\\NVIDIA\\DXCache", f"{L}\\NVIDIA\\GLCache", f"{L}\\AMD\\DxCache",
                      f"{L}\\AMD\\DxcCache", f"{L}\\AMD\\GLCache"], 0, False,
                     "Les jeux recompileront leurs shaders : quelques saccades au premier lancement."),
    "recycle_bin": ("Corbeille", "Vide la corbeille de tous les lecteurs.", [], 0, False,
                    "Suppression définitive des éléments de la corbeille."),
}

UNIX_CATEGORIES = {
    "user_cache": ("Cache utilisateur", "~/.cache", ["~/.cache/*"], 24, True, None),
    "thumbnails": ("Miniatures", "~/.cache/thumbnails", ["~/.cache/thumbnails"], 0, True, None),
    "tmp": ("Temporaires", "/tmp (fichiers de l'utilisateur)", ["/tmp"], 24, True, None),
    "trash": ("Corbeille", "~/.local/share/Trash", ["~/.local/share/Trash/files"], 0, False, "Suppression définitive."),
}

CATEGORIES = WINDOWS_CATEGORIES if IS_WINDOWS else UNIX_CATEGORIES


def _expand(pattern):
    path = env_path(pattern)
    if "%" in path:  # variable inexistante
        return []
    return glob.glob(path) if any(c in path for c in "*?") else ([path] if os.path.exists(path) else [])


def _iter_files(root, min_age_h):
    limit = time.time() - min_age_h * 3600
    if os.path.isfile(root):
        try:
            st = os.stat(root)
            if st.st_mtime <= limit:
                yield root, st.st_size
        except OSError:
            pass
        return
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                st = os.lstat(fp)
                if st.st_mtime <= limit:
                    yield fp, st.st_size
            except OSError:
                pass


def _recycle_bin_size():
    if not IS_WINDOWS:
        return 0, 0

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    return info.i64Size, info.i64NumItems


def list_categories():
    return [{"id": k, "label": v[0], "description": v[1], "default": v[4], "warning": v[5]} for k, v in CATEGORIES.items()]


def scan(job=None, ids=None):
    ids = ids or list(CATEGORIES)
    results = []
    for i, cid in enumerate(ids):
        if cid not in CATEGORIES:
            continue
        label, desc, patterns, age, default, warn = CATEGORIES[cid]
        if job:
            job.update(i / len(ids), f"Analyse : {label}")
        size = count = 0
        if cid == "recycle_bin":
            size, count = _recycle_bin_size()
        for pat in patterns:
            for root in _expand(pat):
                for _, s in _iter_files(root, age):
                    size += s
                    count += 1
        results.append({"id": cid, "label": label, "description": desc, "size": size, "files": count,
                        "default": default, "warning": warn})
    total = sum(r["size"] for r in results)
    return {"categories": results, "total": total}


def _remove_empty_dirs(root):
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath != root and not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def clean(job=None, ids=()):
    freed = deleted = failed = 0
    ids = [i for i in ids if i in CATEGORIES]
    for n, cid in enumerate(ids):
        label, _, patterns, age, _, _ = CATEGORIES[cid]
        if job:
            job.update(n / max(len(ids), 1), f"Nettoyage : {label}")
        if cid == "recycle_bin" and IS_WINDOWS:
            size, items = _recycle_bin_size()
            # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
            if ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4) in (0, -2147418113):
                freed += size
                deleted += items
            continue
        for pat in patterns:
            for root in _expand(pat):
                for fp, s in list(_iter_files(root, age)):
                    try:
                        os.chmod(fp, 0o666)
                        os.remove(fp)
                        freed += s
                        deleted += 1
                    except OSError:
                        failed += 1  # fichier en cours d'utilisation : ignoré
                _remove_empty_dirs(root)
    if IS_WINDOWS and "delivery_opt" in ids:
        run(["powershell.exe", "-NoProfile", "-Command", "Delete-DeliveryOptimizationCache -Force"], timeout=60)
    journal.log("clean", f"Nettoyage terminé : {freed / 1024 ** 2:.0f} Mo libérés, {deleted} fichiers supprimés"
                + (f", {failed} ignorés (en cours d'utilisation)" if failed else ""), "success")
    from . import config
    config.update({"state": {"last_clean": time.time()}})
    return {"freed": freed, "deleted": deleted, "skipped": failed}


def disk_cleanup_tool():
    """Ouvre l'outil Windows de nettoyage avancé (fichiers système, anciennes versions de Windows)."""
    if IS_WINDOWS:
        os.startfile("cleanmgr.exe")
    return {"ok": True}


def free_space(path=None):
    path = path or (os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/")
    return shutil.disk_usage(path)._asdict()
