"""Optimisation réseau : DNS, cache DNS, pile TCP/IP, test de latence."""
import re
import socket
import statistics
import time

import psutil

from . import journal
from .system import IS_WINDOWS, NotSupported, as_list, is_admin, ps, ps_json, run

DNS_PROVIDERS = {
    "cloudflare": {"label": "Cloudflare (1.1.1.1)", "servers": ["1.1.1.1", "1.0.0.1"]},
    "google": {"label": "Google (8.8.8.8)", "servers": ["8.8.8.8", "8.8.4.4"]},
    "quad9": {"label": "Quad9 (sécurité)", "servers": ["9.9.9.9", "149.112.112.112"]},
    "adguard": {"label": "AdGuard (bloque les pubs)", "servers": ["94.140.14.14", "94.140.15.15"]},
}
PING_TARGETS = [
    {"host": "1.1.1.1", "label": "Cloudflare"},
    {"host": "8.8.8.8", "label": "Google"},
    {"host": "steamcommunity.com", "label": "Steam"},
    {"host": "api.steampowered.com", "label": "Steam API"},
]


def adapters():
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    io = psutil.net_io_counters(pernic=True)
    out = []
    for name, st in stats.items():
        if name.lower().startswith(("loopback", "lo")):
            continue
        ipv4 = [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET]
        kind = "Wi-Fi" if re.search(r"wi-?fi|wlan|wireless", name, re.I) else ("Ethernet" if re.search(r"ethernet|eth|en", name, re.I) else "Autre")
        out.append({"name": name, "up": st.isup, "speed": st.speed, "mtu": st.mtu, "ipv4": ipv4, "kind": kind,
                    "sent": io[name].bytes_sent if name in io else 0, "recv": io[name].bytes_recv if name in io else 0})
    out.sort(key=lambda a: (not a["up"], not a["ipv4"]))
    return out


def dns_status():
    if not IS_WINDOWS:
        return []
    data = as_list(ps_json("Get-DnsClientServerAddress -AddressFamily IPv4 | ? {$_.ServerAddresses} | "
                           "Select InterfaceAlias,InterfaceIndex,ServerAddresses"))
    return [{"interface": d["InterfaceAlias"], "index": d["InterfaceIndex"], "servers": as_list(d["ServerAddresses"])} for d in data]


def set_dns(provider: str):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    up = "Get-NetAdapter | ? Status -eq 'Up'"
    if provider == "auto":
        script = f"{up} | % {{ Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }}"
        label = "automatique (box / FAI)"
    else:
        p = DNS_PROVIDERS[provider]
        servers = ",".join(f"'{s}'" for s in p["servers"])
        script = f"{up} | % {{ Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ({servers}) }}"
        label = p["label"]
    r = ps(script + "; Clear-DnsClientCache")
    if not r["ok"]:
        raise RuntimeError(r["err"] or "Échec de la configuration DNS")
    journal.log("network", f"DNS configuré : {label}", "success")
    return {"provider": provider}


def flush_dns():
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    r = run(["ipconfig", "/flushdns"])
    journal.log("network", "Cache DNS vidé", "success")
    return {"ok": r["ok"], "out": r["out"]}


def reset_stack():
    """Réinitialise Winsock et la pile TCP/IP (corrige les problèmes de connexion ; redémarrage requis)."""
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    outs = [run(c) for c in (["netsh", "winsock", "reset"], ["netsh", "int", "ip", "reset"], ["ipconfig", "/flushdns"])]
    journal.log("network", "Pile réseau réinitialisée (redémarrage requis)", "warning")
    return {"ok": all(o["ok"] for o in outs[:1]), "reboot": True}


def tcp_optimize():
    """Réglages TCP sûrs : auto-tuning normal, RSS activé, ECN désactivé (meilleure compatibilité box)."""
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    cmds = [["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"],
            ["netsh", "int", "tcp", "set", "global", "rss=enabled"],
            ["netsh", "int", "tcp", "set", "global", "ecncapability=disabled"],
            ["netsh", "int", "tcp", "set", "heuristics", "disabled"]]
    res = [run(c) for c in cmds]
    journal.log("network", "Paramètres TCP optimisés", "success")
    return {"ok": all(r["ok"] for r in res)}


def tcp_status():
    if not IS_WINDOWS:
        return ""
    return run(["netsh", "int", "tcp", "show", "global"])["out"]


def _ping(host, count=8):
    if IS_WINDOWS:
        r = run(["ping", "-n", str(count), "-w", "1000", host], timeout=count * 2 + 5)
        times = [float(x) for x in re.findall(r"[=<](\d+)\s?ms", r["out"])]
        times = times[: count]
    else:
        r = run(["ping", "-c", str(count), "-W", "1", "-i", "0.3", host], timeout=count * 2 + 5)
        times = [float(x) for x in re.findall(r"time[=<]([\d.]+)", r["out"])]
    if not times and not r["ok"]:
        # repli : latence de connexion TCP (ICMP souvent bloqué)
        count = 4
        for _ in range(count):
            t0 = time.perf_counter()
            try:
                with socket.create_connection((host, 443), timeout=1.5):
                    times.append((time.perf_counter() - t0) * 1000)
            except OSError:
                pass
    loss = 100 * (1 - len(times) / count) if times else 100
    return {"avg": round(statistics.mean(times), 1) if times else None, "min": round(min(times), 1) if times else None,
            "max": round(max(times), 1) if times else None,
            "jitter": round(statistics.pstdev(times), 1) if len(times) > 1 else 0, "loss": round(max(loss, 0))}


def latency_test(job=None):
    results = []
    for i, t in enumerate(PING_TARGETS):
        if job:
            job.update(i / len(PING_TARGETS), f"Ping {t['label']}…")
        results.append({**t, **_ping(t["host"])})
    best = [r for r in results if r["avg"] is not None]
    verdict = "Connexion excellente pour le jeu compétitif."
    if not best:
        verdict = "Aucune réponse : vérifiez votre connexion."
    else:
        avg = statistics.mean(r["avg"] for r in best)
        jit = max(r["jitter"] for r in best)
        loss = max(r["loss"] for r in best)
        if loss > 2:
            verdict = "Perte de paquets détectée : préférez un câble Ethernet et redémarrez la box."
        elif jit > 15:
            verdict = "Gigue élevée (ping instable) : typique du Wi-Fi chargé. Passez en Ethernet ou en Wi-Fi 5 GHz."
        elif avg > 60:
            verdict = "Latence élevée : vérifiez qu'aucun téléchargement ne tourne (Steam, Windows Update, OneDrive)."
    journal.log("network", f"Test de latence : {verdict}")
    return {"results": results, "verdict": verdict}
