"""Gestion des processus : liste, arrêt, priorité, détection des applis inutiles en arrière-plan."""
import os

import psutil

from . import journal
from .system import IS_WINDOWS

CRITICAL = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe", "services.exe",
    "svchost.exe", "dwm.exe", "fontdrvhost.exe", "memory compression", "secure system", "lsaiso.exe",
    "sihost.exe", "ctfmon.exe", "audiodg.exe", "msmpeng.exe", "conhost.exe", "runtimebroker.exe", "systemd", "init",
}

# Applications fréquemment lancées en arrière-plan qui consomment des ressources pendant le jeu.
BLOAT = {
    "onedrive.exe": "Synchronisation OneDrive (disque + réseau)",
    "teams.exe": "Microsoft Teams", "ms-teams.exe": "Microsoft Teams",
    "spotify.exe": "Spotify", "epicgameslauncher.exe": "Epic Games Launcher", "eadesktop.exe": "EA App",
    "widgets.exe": "Widgets Windows", "phoneexperiencehost.exe": "Lien avec le téléphone", "yourphone.exe": "Votre téléphone",
    "adobecollabsync.exe": "Adobe sync", "ccxprocess.exe": "Adobe Creative Cloud", "creative cloud.exe": "Adobe Creative Cloud",
    "cortana.exe": "Cortana", "skypeapp.exe": "Skype", "msedgewebview2.exe": "WebView2 (applis web en arrière-plan)",
    "googledrivefs.exe": "Google Drive", "dropbox.exe": "Dropbox", "ubisoftconnect.exe": "Ubisoft Connect",
    "battle.net.exe": "Battle.net", "origin.exe": "Origin", "gamebar.exe": "Xbox Game Bar", "gamebarftserver.exe": "Xbox Game Bar",
    "searchapp.exe": "Recherche Windows (UI)", "copilot.exe": "Copilot",
}

PRIORITIES = {
    "idle": "IDLE_PRIORITY_CLASS", "below": "BELOW_NORMAL_PRIORITY_CLASS", "normal": "NORMAL_PRIORITY_CLASS",
    "above": "ABOVE_NORMAL_PRIORITY_CLASS", "high": "HIGH_PRIORITY_CLASS", "realtime": "REALTIME_PRIORITY_CLASS",
}
_UNIX_NICE = {"idle": 19, "below": 10, "normal": 0, "above": -5, "high": -10, "realtime": -20}
_cache = {}


def _priority_name(p):
    try:
        nice = p.nice()
    except (psutil.Error, OSError):
        return None
    if IS_WINDOWS:
        for k, attr in PRIORITIES.items():
            if nice == getattr(psutil, attr):
                return k
        return str(nice)
    return min(_UNIX_NICE, key=lambda k: abs(_UNIX_NICE[k] - nice))


def list_processes():
    out = []
    ncpu = psutil.cpu_count() or 1
    alive = set()
    for p in psutil.process_iter(["pid", "name", "username", "memory_info", "status", "create_time", "exe"]):
        alive.add(p.pid)
        c = _cache.setdefault(p.pid, p)
        try:
            cpu = c.cpu_percent(None) / ncpu
        except psutil.Error:
            cpu = 0
        name = p.info["name"] or "?"
        if p.pid == 0:
            continue
        low = name.lower()
        out.append({
            "pid": p.pid, "name": name, "user": (p.info["username"] or "").split("\\")[-1],
            "mem": p.info["memory_info"].rss if p.info["memory_info"] else 0, "cpu": round(cpu, 1),
            "status": p.info["status"], "exe": p.info["exe"] or "", "priority": _priority_name(p),
            "critical": _protected(p.pid, name), "bloat": BLOAT.get(low),
        })
    for pid in list(_cache):
        if pid not in alive:
            _cache.pop(pid)
    return out


def _protected(pid, name):
    from .anticheat import is_protected
    return pid <= 4 or pid == os.getpid() or pid == os.getppid() or name.lower() in CRITICAL or is_protected(name)


def kill(pid: int):
    p = psutil.Process(pid)
    name = p.name()
    if pid == os.getpid():
        raise PermissionError("Impossible d'arrêter Overdrive depuis lui-même.")
    if _protected(pid, name):
        raise PermissionError(f"{name} est protégé (système, jeu, anti-triche, Discord ou Steam) : arrêt refusé.")
    p.terminate()
    try:
        p.wait(3)
    except psutil.TimeoutExpired:
        p.kill()
    journal.log("process", f"Processus arrêté : {name} (PID {pid})", "success")
    return {"name": name}


def set_priority(pid: int, level: str):
    if level not in PRIORITIES:
        raise ValueError("Priorité inconnue")
    p = psutil.Process(pid)
    if _protected(pid, p.name()):
        raise PermissionError(f"{p.name()} est protégé : sa priorité n'est pas modifiée en direct (compatibilité anti-triche). "
                              "Pour un jeu, utilisez son profil dans « Mes jeux ».")
    p.nice(getattr(psutil, PRIORITIES[level]) if IS_WINDOWS else _UNIX_NICE[level])
    journal.log("process", f"Priorité de {p.name()} → {level}", "success")
    return {"name": p.name(), "priority": level}


def kill_by_names(names):
    wanted = {n.lower() for n in names}
    killed = []
    for p in psutil.process_iter(["pid", "name"]):
        n = (p.info["name"] or "").lower()
        if n in wanted and not _protected(p.pid, n):
            try:
                p.terminate()
                killed.append(p.info["name"])
            except psutil.Error:
                pass
    return sorted(set(killed))


def find(name: str):
    name = name.lower()
    return [p for p in psutil.process_iter(["pid", "name"]) if (p.info["name"] or "").lower() == name]
