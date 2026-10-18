"""Échantillonnage temps réel des ressources + historique pour les graphiques."""
import json
import platform
import socket
import threading
import time
import urllib.request
from collections import deque

import psutil

from . import gpu
from .system import IS_WINDOWS

HISTORY = 120  # points (1/s)
_history = {k: deque(maxlen=HISTORY) for k in ("cpu", "ram", "gpu", "net_down", "net_up", "cpu_temp", "gpu_temp")}
_snapshot = {}
_lock = threading.Lock()
_started = False
_proc_cache = {}


def _lhm_temps():
    """Températures via LibreHardwareMonitor (serveur web activé, port 8085) — optionnel mais recommandé sous Windows."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8085/data.json", timeout=0.6) as r:
            tree = json.load(r)
    except Exception:
        return {}
    found = {}

    def walk(node, parents):
        text = node.get("Text", "")
        val = node.get("Value", "")
        if "°C" in val:
            try:
                v = float(val.replace("°C", "").replace(",", ".").strip())
            except ValueError:
                v = None
            path = " / ".join(parents + [text]).lower()
            if v is not None:
                if "cpu package" in path or ("cpu" in path and "core (tctl" in path):
                    found.setdefault("cpu", v)
                elif "gpu core" in path:
                    found["gpu"] = v
                elif "gpu" in path and "hot spot" not in path:
                    found.setdefault("gpu", v)
        for c in node.get("Children", []):
            walk(c, parents + [text])

    walk(tree, [])
    return found


def _psutil_temps():
    out = {}
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return out
    for name, entries in temps.items():
        for e in entries:
            label = (e.label or name).lower()
            if name in ("coretemp", "k10temp", "zenpower") or "package" in label or "tctl" in label:
                out.setdefault("cpu", e.current)
            elif name in ("amdgpu", "nouveau"):
                out.setdefault("gpu", e.current)
    return out


def _top_processes(n=8):
    procs = []
    alive = set()
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        alive.add(p.pid)
        cached = _proc_cache.get(p.pid)
        if cached is None:
            _proc_cache[p.pid] = p
            p.cpu_percent(None)
            cached = p
        try:
            cpu = cached.cpu_percent(None) / (psutil.cpu_count() or 1)
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            if p.pid in (0, 4) or p.info["name"] in ("System Idle Process", "Idle"):
                continue
            procs.append({"pid": p.pid, "name": p.info["name"], "cpu": round(cpu, 1), "mem": mem})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    for pid in list(_proc_cache):
        if pid not in alive:
            _proc_cache.pop(pid, None)
    return {
        "by_cpu": sorted(procs, key=lambda x: -x["cpu"])[:n],
        "by_mem": sorted(procs, key=lambda x: -x["mem"])[:n],
        "count": len(procs),
    }


def _static_info():
    info = {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "cpu_name": platform.processor() or platform.machine(),
        "cores": psutil.cpu_count(logical=False),
        "threads": psutil.cpu_count(),
        "ram_total": psutil.virtual_memory().total,
        "model": "",
        "boot_time": psutil.boot_time(),
    }
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                info["cpu_name"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as k:
                info["manufacturer"] = winreg.QueryValueEx(k, "SystemManufacturer")[0]
                info["model"] = winreg.QueryValueEx(k, "SystemProductName")[0]
                info["bios"] = winreg.QueryValueEx(k, "BIOSVersion")[0]
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                info["os"] = winreg.QueryValueEx(k, "ProductName")[0]
                build = int(winreg.QueryValueEx(k, "CurrentBuild")[0])
                if build >= 22000:
                    info["os"] = info["os"].replace("Windows 10", "Windows 11")
                info["os_version"] = f"{winreg.QueryValueEx(k, 'DisplayVersion')[0]} (build {build})"
        except OSError:
            pass
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["cpu_name"] = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
    return info


STATIC = None


def _loop():
    global STATIC
    STATIC = _static_info()
    psutil.cpu_percent(None)
    last_net = psutil.net_io_counters()
    last_disk = psutil.disk_io_counters()
    last_t = time.time()
    tick = 0
    gpus, temps, top, disks = [], {}, {"by_cpu": [], "by_mem": [], "count": 0}, []
    battery = None
    while True:
        time.sleep(1)
        now = time.time()
        dt = max(now - last_t, 0.001)
        last_t = now
        cpu = psutil.cpu_percent(None)
        per_core = psutil.cpu_percent(None, percpu=True)
        freq = psutil.cpu_freq()
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        net = psutil.net_io_counters()
        down = (net.bytes_recv - last_net.bytes_recv) / dt
        up = (net.bytes_sent - last_net.bytes_sent) / dt
        last_net = net
        dio = psutil.disk_io_counters()
        if dio and last_disk:
            d_read = (dio.read_bytes - last_disk.read_bytes) / dt
            d_write = (dio.write_bytes - last_disk.write_bytes) / dt
        else:
            d_read = d_write = 0
        last_disk = dio
        if tick % 2 == 0:
            gpus = gpu.read()
            temps = _lhm_temps() or _psutil_temps()
        if tick % 3 == 0:
            top = _top_processes()
        if tick % 15 == 0:
            disks = []
            for part in psutil.disk_partitions(all=False):
                if IS_WINDOWS and "cdrom" in part.opts:
                    continue
                if not IS_WINDOWS and (part.mountpoint.startswith(("/snap", "/boot", "/proc", "/sys")) or part.fstype in ("squashfs", "overlay", "tmpfs")):
                    continue
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except (PermissionError, OSError):
                    continue
                disks.append({"mount": part.mountpoint, "fstype": part.fstype, "total": u.total, "used": u.used,
                              "free": u.free, "percent": u.percent})
            try:
                b = psutil.sensors_battery()
                battery = {"percent": b.percent, "plugged": b.power_plugged} if b else None
            except Exception:
                battery = None
        tick += 1
        g0 = gpus[0] if gpus else None
        gpu_temp = temps.get("gpu") if temps.get("gpu") is not None else (g0 or {}).get("temp")
        snap = {
            "ts": now,
            "cpu": {"percent": cpu, "per_core": per_core, "freq": freq.current if freq else None,
                    "freq_max": freq.max if freq else None, "temp": temps.get("cpu")},
            "ram": {"percent": vm.percent, "used": vm.total - vm.available, "total": vm.total, "available": vm.available,
                    "swap_percent": sw.percent, "swap_used": sw.used},
            "gpus": gpus,
            "gpu": {"percent": g0["util"] if g0 else None, "temp": gpu_temp, "name": g0["name"] if g0 else None},
            "net": {"down": down, "up": up, "total_down": net.bytes_recv, "total_up": net.bytes_sent},
            "disk_io": {"read": d_read, "write": d_write},
            "disks": disks,
            "battery": battery,
            "processes": top,
            "uptime": now - psutil.boot_time(),
        }
        with _lock:
            _snapshot.clear()
            _snapshot.update(snap)
            _history["cpu"].append(cpu)
            _history["ram"].append(vm.percent)
            _history["gpu"].append(g0["util"] if g0 else 0)
            _history["net_down"].append(down)
            _history["net_up"].append(up)
            _history["cpu_temp"].append(temps.get("cpu"))
            _history["gpu_temp"].append(gpu_temp)


def start():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="monitor").start()


def snapshot():
    with _lock:
        return dict(_snapshot)


def history():
    with _lock:
        return {k: list(v) for k, v in _history.items()}


def health_score(snap=None):
    """Score de santé 0-100 pour le tableau de bord (charge, mémoire, disque, températures)."""
    s = snap or snapshot()
    if not s:
        return {"score": 100, "issues": []}
    score, issues = 100, []
    if s["ram"]["percent"] > 85:
        score -= 15; issues.append("Mémoire vive saturée — purge RAM conseillée")
    if s["cpu"]["percent"] > 90:
        score -= 10; issues.append("Processeur très sollicité")
    for d in s.get("disks", []):
        if d["percent"] > 90:
            score -= 15; issues.append(f"Disque {d['mount']} presque plein ({d['percent']:.0f} %)")
    t = s["cpu"].get("temp")
    if t and t > 90:
        score -= 15; issues.append(f"CPU très chaud ({t:.0f} °C) — vérifiez la ventilation / mode Turbo")
    gt = s["gpu"].get("temp")
    if gt and gt > 85:
        score -= 10; issues.append(f"GPU chaud ({gt:.0f} °C)")
    b = s.get("battery")
    if b and not b["plugged"]:
        score -= 5; issues.append("PC portable sur batterie : le GPU est bridé, branchez le secteur pour jouer")
    if s["processes"]["count"] > 260:
        score -= 5; issues.append(f"{s['processes']['count']} processus actifs — beaucoup d'applis en arrière-plan")
    return {"score": max(score, 0), "issues": issues}
