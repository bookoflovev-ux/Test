"""Maintenance système (SFC, DISM, TRIM, CHKDSK) et pilotage à distance (arrêt, redémarrage, verrouillage)."""
import os

from . import journal
from .system import IS_WINDOWS, NotSupported, is_admin, ps, run

TOOLS = {
    "sfc": ("Réparer les fichiers système (SFC)", ["sfc", "/scannow"], 3600),
    "dism_restore": ("Réparer l'image Windows (DISM RestoreHealth)", ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], 3600),
    "dism_cleanup": ("Nettoyer les composants Windows (WinSxS)", ["DISM", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], 3600),
    "trim": ("Optimiser les SSD (TRIM)", None, 1800),
    "chkdsk": ("Vérifier le disque C: (CHKDSK en ligne)", ["chkdsk", "C:", "/scan"], 3600),
}


def run_tool(job, tool):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    label, cmd, timeout = TOOLS[tool]
    job.update(0.05, f"{label} en cours… (plusieurs minutes)")
    if tool == "trim":
        r = ps("Get-Volume | ? { $_.DriveLetter -and $_.DriveType -eq 'Fixed' } | % { Optimize-Volume -DriveLetter $_.DriveLetter -ReTrim -Verbose } 4>&1 | Out-String", timeout)
    else:
        r = run(cmd, timeout=timeout)
    out = (r["out"] or r["err"]).replace("\x00", "")
    journal.log("maintenance", f"{label} : {'terminé' if r['ok'] else 'terminé avec erreurs'}", "success" if r["ok"] else "warning")
    return {"ok": r["ok"], "output": out[-4000:]}


POWER = {
    "shutdown": (["shutdown", "/s", "/t", "30", "/c", "Arrêt demandé depuis Overdrive"], "Arrêt dans 30 s"),
    "restart": (["shutdown", "/r", "/t", "30", "/c", "Redémarrage demandé depuis Overdrive"], "Redémarrage dans 30 s"),
    "cancel": (["shutdown", "/a"], "Arrêt programmé annulé"),
    "lock": (["rundll32.exe", "user32.dll,LockWorkStation"], "Session verrouillée"),
    "sleep": (["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "Mise en veille"),
}


def power(action):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    cmd, label = POWER[action]
    r = run(cmd, timeout=20)
    journal.log("remote", label, "warning")
    return {"ok": r["ok"] or action == "cancel", "message": label}


def open_tool(name):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    tools = {"taskmgr": "taskmgr.exe", "devmgmt": "devmgmt.msc", "cleanmgr": "cleanmgr.exe", "msconfig": "msconfig.exe",
             "services": "services.msc", "control_power": "powercfg.cpl", "windows_update": "ms-settings:windowsupdate",
             "display": "ms-settings:display", "graphics": "ms-settings:display-advancedgraphics", "storage": "ms-settings:storagesense"}
    os.startfile(tools[name])
    return {"ok": True}
