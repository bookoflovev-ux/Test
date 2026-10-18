"""Sauvegardes : points de restauration Windows et copie incrémentale de dossiers (disque externe / NAS)."""
import hashlib
import os
import shutil
import socket
import time

from . import config, journal
from .system import IS_WINDOWS, NotSupported, as_list, is_admin, ps, ps_json


def suggested_sources():
    home = os.path.expanduser("~")
    out = []
    for name in ("Documents", "Desktop", "Bureau", "Pictures", "Images", "Videos", "Music"):
        p = os.path.join(home, name)
        if os.path.isdir(p) and p not in out:
            out.append(p)
    from .gaming import find_cs2
    cs2 = find_cs2()
    if cs2.get("installed"):
        out += [p for p in [cs2["cfg_dir"]] + cs2["user_cfgs"] if os.path.isdir(p)]
    return out


def restore_points():
    if not IS_WINDOWS:
        return []
    data = as_list(ps_json("Get-ComputerRestorePoint -EA SilentlyContinue | Select SequenceNumber,Description,"
                           "@{n='Date';e={$_.ConvertToDateTime($_.CreationTime).ToString('yyyy-MM-dd HH:mm')}}"))
    return [{"id": d["SequenceNumber"], "description": d["Description"], "date": d["Date"]} for d in data][::-1]


def create_restore_point(description="Overdrive — avant optimisation"):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    desc = description.replace("'", "")[:60]
    r = ps("$k='HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore';"
           "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\" -EA SilentlyContinue;"
           "$old=(Get-ItemProperty $k -EA SilentlyContinue).SystemRestorePointCreationFrequency;"
           "Set-ItemProperty $k SystemRestorePointCreationFrequency 0 -Type DWord;"
           f"try {{ Checkpoint-Computer -Description '{desc}' -RestorePointType MODIFY_SETTINGS -EA Stop; 'OK' }}"
           " finally { if($null -ne $old){Set-ItemProperty $k SystemRestorePointCreationFrequency $old} else {Remove-ItemProperty $k SystemRestorePointCreationFrequency -EA SilentlyContinue} }",
           timeout=600)
    if "OK" not in r["out"]:
        raise RuntimeError(r["err"] or "Impossible de créer le point de restauration (protection système désactivée ?)")
    journal.log("backup", f"Point de restauration créé : {desc}", "success")
    return {"ok": True}


def open_system_restore():
    if IS_WINDOWS:
        os.startfile("rstrui.exe")
    return {"ok": True}


def run_backup(job, sources=None, destination=None):
    cfg = config.get()["backup"]
    sources = sources or cfg["sources"]
    destination = destination or cfg["destination"]
    if not sources or not destination:
        raise ValueError("Choisissez au moins un dossier source et une destination.")
    if not os.path.isdir(destination):
        raise FileNotFoundError(f"Destination introuvable : {destination} (disque débranché ?)")
    root = os.path.join(destination, "Overdrive-Backup", socket.gethostname())
    copied = skipped = errors = 0
    size = 0
    files = []
    for src in sources:
        if not os.path.isdir(src):
            continue
        for dirpath, dirnames, filenames in os.walk(src, onerror=lambda e: None):
            for f in filenames:
                files.append((src, os.path.join(dirpath, f)))
    total = len(files) or 1
    t0 = time.time()
    for i, (src, fp) in enumerate(files):
        rel = os.path.relpath(fp, src)
        base = os.path.basename(os.path.normpath(src)) or "racine"
        # suffixe pour différencier deux sources de même nom (ex: plusieurs dossiers « cfg »)
        tag = f"{base}-{hashlib.md5(os.path.normcase(src).encode()).hexdigest()[:6]}" if base.lower() == "cfg" else base
        dest = os.path.join(root, tag, rel)
        if i % 50 == 0:
            job.update(i / total, f"{i}/{total} fichiers — {rel[:70]}")
        try:
            st = os.stat(fp)
            if os.path.exists(dest):
                dst = os.stat(dest)
                if dst.st_size == st.st_size and dst.st_mtime >= st.st_mtime - 2:
                    skipped += 1
                    continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(fp, dest)
            copied += 1
            size += st.st_size
        except OSError:
            errors += 1
    res = {"copied": copied, "skipped": skipped, "errors": errors, "size": size, "destination": root,
           "duration": round(time.time() - t0)}
    config.update({"state": {"last_backup": time.time()}})
    journal.log("backup", f"Sauvegarde : {copied} fichier(s) copiés ({size / 1024 ** 2:.0f} Mo), {skipped} inchangés"
                + (f", {errors} erreurs" if errors else ""), "success" if not errors else "warning")
    return res
