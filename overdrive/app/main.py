"""Serveur Overdrive : API REST + interface web (accessible en local, sur le réseau ou à distance avec jeton)."""
import os
import re
import secrets
import socket
import threading
import time

import psutil
from fastapi import Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import (__version__, advisor, asus, backup, cleaner, config, desktop, drivers, files, games, gaming, jobs, journal, maintenance, memory, monitor, network,
               processes, scheduler, services, startup, supervision, tweaks, updates)
from .system import IS_WINDOWS, NotSupported, is_admin

STATIC_DIR = os.path.join(config.BASE_DIR, "static")
app = FastAPI(title="Overdrive", version=__version__, docs_url="/api/docs", redoc_url=None)
LOCAL = {"127.0.0.1", "::1", "localhost"}


def _is_local(request: Request):
    return (request.client.host if request.client else "") in LOCAL


IP_HOST = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|\[[0-9a-f:]+\])$", re.I)
MUTATING = {"POST", "PUT", "DELETE", "PATCH"}


def _host_allowed(request: Request):
    """Bloque le « DNS rebinding » : seuls localhost, une IP, le nom du PC ou Tailscale sont acceptés."""
    host = request.headers.get("host", "").lower()
    host = host if host.startswith("[") and host.endswith("]") else host.rsplit(":", 1)[0] if ":" in host else host
    return (host in LOCAL or IP_HOST.match(host) is not None or host == socket.gethostname().lower()
            or host.endswith(".ts.net"))


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    if not _host_allowed(request):
        return JSONResponse({"error": "Hôte non autorisé."}, 403)
    if (path.startswith("/api") or path == "/metrics") and request.method != "OPTIONS":
        local = _is_local(request)
        # Les lectures locales sont libres ; toute action (même locale) exige le jeton,
        # sinon n'importe quel site ouvert dans le navigateur pourrait piloter le PC.
        if not local or request.method in MUTATING:
            cfg = config.get()
            if not local and not cfg["allow_remote"]:
                return JSONResponse({"error": "Accès distant désactivé dans les paramètres."}, 403)
            given = (request.headers.get("x-token") or request.query_params.get("token")
                     or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
            if not given or not secrets.compare_digest(given, cfg["token"]):
                return JSONResponse({"error": "Jeton d'accès invalide.", "auth": True}, 401)
    return await call_next(request)


# Autorise les pages locales (cockpit Nexus sur localhost:4190, etc.) à lire Overdrive.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
                   allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-Token", "Authorization"])


@app.exception_handler(Exception)
async def errors(request: Request, exc: Exception):
    status = 500
    if isinstance(exc, NotSupported):
        status = 501
    elif isinstance(exc, (PermissionError, psutil.AccessDenied)):
        status = 403
    elif isinstance(exc, (ValueError, KeyError, FileNotFoundError, psutil.NoSuchProcess)):
        status = 400
    msg = str(exc) or exc.__class__.__name__
    if isinstance(exc, psutil.AccessDenied):
        msg = "Accès refusé par Windows (lancez Overdrive en administrateur)."
    if isinstance(exc, psutil.NoSuchProcess):
        msg = "Le processus n'existe plus."
    return JSONResponse({"error": msg}, status)


def _job(job):
    return {"job": job.id}


# --- Général ------------------------------------------------------------------------------------------------------

def _lan_ips():
    ips = []
    for name, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                ips.append(a.address)
    return ips


@app.get("/api/info")
def info(request: Request):
    cfg = config.get()
    data = {"version": __version__, "windows": IS_WINDOWS, "admin": is_admin(), "static": monitor.STATIC,
            "port": cfg["port"], "lan_ips": _lan_ips(), "local": _is_local(request)}
    if _is_local(request):
        data["token"] = cfg["token"]
    return data


@app.get("/api/stats")
def stats():
    s = monitor.snapshot()
    return {**s, "health": monitor.health_score(s), "alerts": supervision.status()["alerts"], "game": games.current(),
            "asus_mode": config.get()["asus"].get("mode") if asus.is_asus() else None}


@app.get("/api/history")
def history():
    return monitor.history()


@app.get("/api/jobs")
def list_jobs():
    return jobs.all_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise KeyError("Tâche inconnue")
    return j


@app.get("/api/journal")
def get_journal(limit: int = 100):
    return journal.recent(limit)


@app.get("/api/config")
def get_config(request: Request):
    cfg = dict(config.get())
    if not _is_local(request):
        cfg = {**cfg, "token": "••••••"}
    return cfg


@app.put("/api/config")
def put_config(request: Request, patch: dict = Body(...)):
    patch.pop("state", None)
    if "token" in patch and not _is_local(request):
        patch.pop("token")
    if patch.get("regenerate_token") and _is_local(request):
        patch["token"] = secrets.token_urlsafe(18)
    patch.pop("regenerate_token", None)
    config.update(patch)
    journal.log("settings", "Paramètres enregistrés")
    return get_config(request)


def _quick(job):
    report = {}
    job.update(0.1, "Nettoyage des fichiers inutiles…")
    ids = [c["id"] for c in cleaner.list_categories() if c["default"]]
    report["clean"] = cleaner.clean(None, ids)
    if IS_WINDOWS:
        job.update(0.7, "Optimisation de la mémoire vive…")
        try:
            report["ram"] = memory.optimize()
        except Exception as e:
            report["ram"] = {"error": str(e)}
        job.update(0.9, "Vidage du cache DNS…")
        try:
            network.flush_dns()
        except Exception:
            pass
    journal.log("quick", "Optimisation express terminée", "success")
    return report


@app.post("/api/quick-optimize")
def quick_optimize():
    return _job(jobs.submit("Optimisation express", _quick))


# --- RAM / processus / démarrage / services ---------------------------------------------------------------------

@app.post("/api/ram/optimize")
def ram_optimize():
    return memory.optimize()


@app.get("/api/processes")
def get_processes():
    return processes.list_processes()


@app.post("/api/processes/{pid}/kill")
def kill_process(pid: int):
    return processes.kill(pid)


@app.post("/api/processes/{pid}/priority")
def process_priority(pid: int, body: dict = Body(...)):
    return processes.set_priority(pid, body["level"])


@app.get("/api/startup")
def get_startup():
    return startup.list_items()


@app.post("/api/startup")
def set_startup(body: dict = Body(...)):
    return startup.set_enabled(body["id"], bool(body["enabled"]))


@app.get("/api/services")
def get_services():
    return services.list_services()


@app.post("/api/services/{name}/{action}")
def service_action(name: str, action: str):
    if action == "optimize":
        return services.optimize(name)
    if action == "restore":
        return services.restore(name)
    raise ValueError("Action inconnue")


# --- Optimisations ------------------------------------------------------------------------------------------------

@app.get("/api/tweaks")
def get_tweaks():
    return tweaks.list_tweaks()


@app.post("/api/tweaks/recommended")
def tweaks_recommended():
    return tweaks.apply_recommended()


@app.post("/api/tweaks/revert-all")
def tweaks_revert_all():
    return tweaks.revert_all()


@app.post("/api/tweaks/{tweak_id}/{action}")
def tweak_action(tweak_id: str, action: str):
    if action == "apply":
        return tweaks.apply(tweak_id)
    if action == "revert":
        return tweaks.revert(tweak_id)
    raise ValueError("Action inconnue")


@app.get("/api/power-plans")
def power_plans():
    if not IS_WINDOWS:
        return []
    return tweaks.list_power_plans()


@app.post("/api/power-plans")
def set_power_plan(body: dict = Body(...)):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    return tweaks.set_power_plan(body["guid"])


# --- Nettoyage & fichiers -----------------------------------------------------------------------------------------

@app.get("/api/clean/categories")
def clean_categories():
    return cleaner.list_categories()


@app.post("/api/clean/scan")
def clean_scan(body: dict = Body(default={})):
    return _job(jobs.submit("Analyse des fichiers inutiles", cleaner.scan, body.get("ids")))


@app.post("/api/clean/run")
def clean_run(body: dict = Body(...)):
    return _job(jobs.submit("Nettoyage", cleaner.clean, body["ids"]))


@app.post("/api/files/{kind}")
def files_scan(kind: str, body: dict = Body(default={})):
    roots = [r for r in body.get("roots", []) if r] or None
    if kind == "large":
        return _job(jobs.submit("Gros fichiers", files.large_files, roots, int(body.get("min_mb", 500))))
    if kind == "duplicates":
        return _job(jobs.submit("Doublons", files.duplicates, roots, int(body.get("min_mb", 1))))
    if kind == "downloads":
        return _job(jobs.submit("Vieux téléchargements", files.old_downloads, int(body.get("days", 90))))
    if kind == "empty":
        return _job(jobs.submit("Dossiers vides", files.empty_folders, roots))
    if kind == "trash":
        return files.trash(body["paths"])
    raise ValueError("Type d'analyse inconnu")


@app.get("/api/files/roots")
def files_roots():
    return files.default_roots()


# --- Jeu / CS2 ----------------------------------------------------------------------------------------------------

@app.get("/api/gaming")
def gaming_status():
    return gaming.status()


@app.get("/api/gaming/info")
def gaming_info():
    return gaming.info()


@app.post("/api/gaming/profile")
def gaming_profile():
    return gaming.apply_cs2_profile()


@app.delete("/api/gaming/profile")
def gaming_profile_revert():
    return gaming.revert_cs2_profile()


@app.post("/api/gaming/boost")
def gaming_boost(body: dict = Body(default={})):
    return gaming.game_boost(kill=body.get("kill", True))


@app.post("/api/gaming/video")
def gaming_video():
    return gaming.optimize_video()


@app.get("/api/gaming/autoexec")
def get_autoexec():
    return gaming.get_autoexec()


@app.post("/api/gaming/autoexec")
def save_autoexec(body: dict = Body(...)):
    return gaming.save_autoexec(body["content"])


@app.post("/api/gaming/backup-config")
def gaming_backup():
    return gaming.backup_cs2_config()


@app.put("/api/gaming/kill-list")
def gaming_kill_list(body: dict = Body(...)):
    names = [n.strip() for n in body["kill_list"] if n.strip()]
    config.update({"gaming": {"kill_list": names}})
    return {"kill_list": names}


# --- Analyse du PC & constructeur -----------------------------------------------------------------------------------------

@app.get("/api/advisor")
def advisor_last(light: bool = False):
    a = advisor.last()
    if light:  # résumé rapide pour le tableau de bord
        return {"analysis": a and {k: a[k] for k in ("ts", "tier", "auto_count")} | {
            "critical": sum(1 for f in a["findings"] if f["severity"] == "critique")}}
    return {"analysis": a, "asus": asus.status(), "oem": asus.oem_tool(),
            "turbo_in_games": config.get()["asus"].get("turbo_in_games", True)}


@app.post("/api/advisor/scan")
def advisor_scan():
    return _job(jobs.submit("Analyse du PC", advisor.analyze))


@app.post("/api/advisor/optimize")
def advisor_optimize(body: dict = Body(default={})):
    return _job(jobs.submit("Optimisation automatique du PC", advisor.optimize, body.get("actions")))


@app.post("/api/asus/{action}")
def asus_action(action: str, body: dict = Body(default={})):
    if action == "mode":
        return asus.set_mode(body["mode"])
    if action == "battery":
        return asus.set_battery_limit(body["limit"])
    if action == "panel-od":
        return asus.set_panel_od(bool(body["enabled"]))
    if action == "turbo-in-games":
        config.update({"asus": {"turbo_in_games": bool(body["enabled"])}})
        return {"turbo_in_games": bool(body["enabled"])}
    raise ValueError("Action inconnue")


# --- Application (bureau) ---------------------------------------------------------------------------------------------

@app.get("/api/app/autostart")
def app_autostart():
    return desktop.autostart_status()


@app.put("/api/app/autostart")
def app_set_autostart(body: dict = Body(...)):
    return desktop.set_autostart(bool(body["enabled"]))


@app.post("/api/app/quit")
def app_quit():
    server = getattr(app.state, "server", None)
    if server is None:
        raise RuntimeError("Arrêt indisponible dans ce mode de lancement.")
    journal.log("system", "Overdrive arrêté depuis l'interface", "warning")
    threading.Timer(0.5, lambda: setattr(server, "should_exit", True)).start()
    return {"ok": True}


# --- Bibliothèque de jeux (profils automatiques) ---------------------------------------------------------------------

@app.get("/api/games")
def games_library():
    return games.library()


@app.post("/api/games/scan")
def games_scan():
    return _job(jobs.submit("Recherche des jeux installés", games.scan))


@app.put("/api/games/settings")
def games_settings(body: dict = Body(...)):
    config.update({"games": {"auto_profiles": bool(body.get("auto_profiles", True))}})
    return games.library()


@app.put("/api/games/{key}")
def games_profile(key: str, body: dict = Body(...)):
    return games.set_profile(key, body.get("profile"), body.get("auto"))


# --- Réseau -------------------------------------------------------------------------------------------------------

@app.get("/api/network")
def network_status():
    data = {"adapters": network.adapters(), "providers": network.DNS_PROVIDERS, "dns": [], "tcp": ""}
    if IS_WINDOWS:
        try:
            data["dns"] = network.dns_status()
            data["tcp"] = network.tcp_status()
        except Exception as e:
            data["error"] = str(e)
    return data


@app.post("/api/network/{action}")
def network_action(action: str, body: dict = Body(default={})):
    if action == "dns":
        return network.set_dns(body["provider"])
    if action == "flush-dns":
        return network.flush_dns()
    if action == "reset":
        return network.reset_stack()
    if action == "tcp":
        return network.tcp_optimize()
    if action == "latency":
        return _job(jobs.submit("Test de latence", network.latency_test))
    raise ValueError("Action inconnue")


# --- Mises à jour -------------------------------------------------------------------------------------------------

@app.get("/api/updates/vendor")
def updates_vendor():
    return {**updates.vendor_info(), "winget": updates.winget_available()}


@app.post("/api/drivers/scan")
def drivers_scan():
    return _job(jobs.submit("Recherche de pilotes (toutes sources)", drivers.scan))


@app.post("/api/drivers/install")
def drivers_install(body: dict = Body(...)):
    return _job(jobs.submit("Installation de pilotes", drivers.install_many, body["items"]))


@app.get("/api/updates/drivers")
def updates_drivers():
    return updates.drivers()


@app.post("/api/updates/{action}")
def updates_action(action: str, body: dict = Body(default={})):
    kind = body.get("kind", "all")
    if kind not in updates.CRITERIA:
        raise ValueError("Type inconnu")
    if action == "windows-scan":
        return _job(jobs.submit("Recherche Windows Update", updates.windows_scan, kind))
    if action == "windows-install":
        return _job(jobs.submit("Installation Windows Update", updates.windows_install, kind, body.get("ids")))
    if action == "apps-scan":
        return _job(jobs.submit("Recherche mises à jour applis", updates.apps_scan))
    if action == "apps-upgrade":
        return _job(jobs.submit("Mise à jour des applis", updates.apps_upgrade, body.get("ids")))
    if action == "launch":
        return updates.launch_app(body["app_id"])
    if action == "open-url":
        return updates.open_url(body["url"])
    raise ValueError("Action inconnue")


# --- Sauvegarde & maintenance ---------------------------------------------------------------------------------------

@app.get("/api/backup")
def backup_status():
    cfg = config.get()
    points = []
    err = None
    try:
        points = backup.restore_points()
    except Exception as e:
        err = str(e)
    return {"restore_points": points, "error": err, "suggested": backup.suggested_sources(), "config": cfg["backup"],
            "last_backup": cfg["state"].get("last_backup", 0)}


@app.post("/api/backup/{action}")
def backup_action(action: str, body: dict = Body(default={})):
    if action == "restore-point":
        return _job(jobs.submit("Point de restauration", lambda job: backup.create_restore_point(body.get("description") or "Overdrive")))
    if action == "open-restore":
        return backup.open_system_restore()
    if action == "run":
        if body.get("sources") is not None:
            config.update({"backup": {"sources": body["sources"], "destination": body.get("destination", "")}})
        return _job(jobs.submit("Sauvegarde des dossiers", backup.run_backup))
    raise ValueError("Action inconnue")


@app.get("/api/maintenance")
def maintenance_tools():
    return [{"id": k, "label": v[0]} for k, v in maintenance.TOOLS.items()]


@app.post("/api/maintenance/{tool}")
def maintenance_run(tool: str):
    if tool not in maintenance.TOOLS:
        raise ValueError("Outil inconnu")
    return _job(jobs.submit(maintenance.TOOLS[tool][0], maintenance.run_tool, tool))


@app.post("/api/power/{action}")
def power(action: str):
    if action not in maintenance.POWER:
        raise ValueError("Action inconnue")
    return maintenance.power(action)


@app.post("/api/open/{tool}")
def open_tool(tool: str):
    return maintenance.open_tool(tool)


# --- Supervision (Nexus / Prometheus) ------------------------------------------------------------------------------

@app.get("/api/supervision")
def supervision_status():
    return {**supervision.status(), "config": config.get()["nexus"], "alerts_config": config.get()["alerts"]}


@app.post("/api/supervision/test")
def supervision_test():
    return supervision.push_now()


@app.get("/api/nexus")
def nexus_pull():
    """Point d'entrée « pull » : Nexus (ou tout outil de supervision) peut interroger cette URL."""
    return supervision.payload(include_processes=True)


@app.get("/api/changelog", response_class=PlainTextResponse)
def changelog():
    path = os.path.join(config.BASE_DIR, "CHANGELOG.md")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


@app.get("/api/health")
def health():
    s = monitor.snapshot()
    return {"status": "ok", "version": __version__, "health": monitor.health_score(s) if s else None}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    return supervision.prometheus()


# --- Interface ----------------------------------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.webmanifest"), media_type="application/manifest+json")


def _first_run():
    """Réapplique les réglages ASUS mémorisés et lance une première analyse du PC si elle n'a jamais été faite."""
    time.sleep(3)  # laisse le moniteur lire l'identité du PC
    asus.apply_saved()
    if advisor.last() is None:
        try:
            advisor.analyze()
        except Exception as e:
            journal.log("advisor", f"Analyse initiale impossible : {e}", "warning")


@app.on_event("startup")
def on_start():
    monitor.start()
    games.start()
    threading.Thread(target=_first_run, daemon=True).start()
    supervision.start()
    scheduler.start()
    journal.log("system", f"Overdrive {__version__} démarré" + (" (administrateur)" if is_admin() else " (sans droits admin)"))
