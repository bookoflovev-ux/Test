"""Supervision : export Prometheus, envoi périodique vers Nexus (webhook JSON) et alertes de seuils."""
import json
import socket
import threading
import time
import urllib.request

from . import __version__, config, journal, monitor

_alerts = {}
_last_push = {"ts": None, "ok": None, "error": None}


def payload(include_processes=False):
    s = monitor.snapshot()
    if not s:
        return {}
    data = {
        "source": "overdrive", "version": __version__, "host": socket.gethostname(), "timestamp": int(s["ts"]),
        "cpu_percent": s["cpu"]["percent"], "cpu_temp": s["cpu"]["temp"], "cpu_freq_mhz": s["cpu"]["freq"],
        "ram_percent": s["ram"]["percent"], "ram_used": s["ram"]["used"], "ram_total": s["ram"]["total"],
        "gpu_percent": s["gpu"]["percent"], "gpu_temp": s["gpu"]["temp"], "gpu_name": s["gpu"]["name"],
        "net_down_bps": s["net"]["down"], "net_up_bps": s["net"]["up"],
        "disks": [{"mount": d["mount"], "percent": d["percent"], "free": d["free"]} for d in s["disks"]],
        "battery": s["battery"], "uptime": int(s["uptime"]), "health": monitor.health_score(s),
        "alerts": list(_alerts.values()),
    }
    if include_processes:
        data["top_processes"] = s["processes"]["by_cpu"]
    return data


def prometheus():
    s = monitor.snapshot()
    if not s:
        return "# en attente de données\n"
    host = socket.gethostname()
    lines = []

    def m(name, value, help_, labels=None):
        if value is None:
            return
        lab = {"host": host, **(labels or {})}
        ls = ",".join(f'{k}="{v}"' for k, v in lab.items())
        lines.append(f"# HELP overdrive_{name} {help_}")
        lines.append(f"overdrive_{name}{{{ls}}} {float(value)}")

    m("cpu_percent", s["cpu"]["percent"], "Utilisation CPU (%)")
    m("cpu_temp_celsius", s["cpu"]["temp"], "Température CPU")
    m("cpu_freq_mhz", s["cpu"]["freq"], "Fréquence CPU")
    m("ram_percent", s["ram"]["percent"], "Utilisation RAM (%)")
    m("ram_used_bytes", s["ram"]["used"], "RAM utilisée")
    for i, g in enumerate(s["gpus"]):
        lab = {"gpu": str(i), "name": g["name"]}
        m("gpu_percent", g["util"], "Utilisation GPU (%)", lab)
        m("gpu_temp_celsius", g.get("temp"), "Température GPU", lab)
        m("gpu_mem_used_bytes", g.get("mem_used"), "VRAM utilisée", lab)
        m("gpu_power_watts", g.get("power"), "Consommation GPU", lab)
    m("net_down_bytes_per_second", s["net"]["down"], "Débit descendant")
    m("net_up_bytes_per_second", s["net"]["up"], "Débit montant")
    for d in s["disks"]:
        m("disk_used_percent", d["percent"], "Occupation disque (%)", {"mount": d["mount"].replace("\\", "/")})
    m("uptime_seconds", s["uptime"], "Durée de fonctionnement")
    m("health_score", monitor.health_score(s)["score"], "Score de santé Overdrive")
    m("alerts_active", len(_alerts), "Alertes actives")
    return "\n".join(lines) + "\n"


def _post(body):
    cfg = config.get()["nexus"]
    req = urllib.request.Request(cfg["url"], data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": f"Overdrive/{__version__}"})
    if cfg.get("api_key"):
        req.add_header("Authorization", f"Bearer {cfg['api_key']}")
        req.add_header("X-API-Key", cfg["api_key"])
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def push_now():
    cfg = config.get()["nexus"]
    if not cfg["url"]:
        raise ValueError("Renseignez l'URL de votre serveur Nexus.")
    try:
        status = _post({"type": "metrics", **payload(include_processes=True)})
        _last_push.update(ts=time.time(), ok=True, error=None)
        return {"ok": True, "status": status}
    except Exception as e:
        _last_push.update(ts=time.time(), ok=False, error=str(e))
        raise RuntimeError(f"Envoi vers Nexus impossible : {e}")


def _check_alerts():
    s = monitor.snapshot()
    if not s:
        return
    th = config.get()["alerts"]
    checks = {
        "cpu": (s["cpu"]["percent"], th["cpu_percent"], "Charge CPU élevée", "%"),
        "ram": (s["ram"]["percent"], th["ram_percent"], "Mémoire vive saturée", "%"),
        "cpu_temp": (s["cpu"]["temp"], th["cpu_temp"], "Température CPU critique", "°C"),
        "gpu_temp": (s["gpu"]["temp"], th["gpu_temp"], "Température GPU critique", "°C"),
    }
    for d in s["disks"]:
        checks[f"disk:{d['mount']}"] = (d["percent"], th["disk_percent"], f"Disque {d['mount']} presque plein", "%")
    for key, (value, limit, label, unit) in checks.items():
        active = value is not None and value >= limit
        if active and key not in _alerts:
            _alerts[key] = {"id": key, "label": label, "value": round(value, 1), "limit": limit, "unit": unit, "since": time.time()}
            journal.log("alert", f"{label} : {value:.0f}{unit} (seuil {limit}{unit})", "error")
            cfg = config.get()["nexus"]
            if cfg["enabled"] and cfg["url"] and cfg["send_alerts"]:
                try:
                    _post({"type": "alert", "state": "firing", "host": socket.gethostname(), **_alerts[key]})
                except Exception:
                    pass
        elif active:
            _alerts[key]["value"] = round(value, 1)
        elif not active and key in _alerts and (value is None or value < limit - 3):
            resolved = _alerts.pop(key)
            journal.log("alert", f"Résolu : {label}", "success")
            cfg = config.get()["nexus"]
            if cfg["enabled"] and cfg["url"] and cfg["send_alerts"]:
                try:
                    _post({"type": "alert", "state": "resolved", "host": socket.gethostname(), **resolved})
                except Exception:
                    pass


def _loop():
    last = 0
    while True:
        time.sleep(5)
        try:
            _check_alerts()
        except Exception:
            pass
        cfg = config.get()["nexus"]
        if cfg["enabled"] and cfg["url"] and time.time() - last >= max(int(cfg["interval"]), 5):
            last = time.time()
            try:
                push_now()
            except Exception:
                pass


def start():
    threading.Thread(target=_loop, daemon=True, name="supervision").start()


def status():
    return {"last_push": _last_push, "alerts": list(_alerts.values())}
