"""Analyse du PC : inventaire matériel, diagnostic et optimisation automatique adaptée à CE PC
(portable ou fixe, quantité de RAM, SSD ou disque dur, GPU dédié ou intégré, écran, constructeur…)."""
import json
import os
import platform
import re
import time
from datetime import date

import psutil

from . import asus, cleaner, config, display, gpu, journal, maintenance, monitor, services, startup, tweaks
from .config import DATA_DIR
from .system import IS_WINDOWS, ps_json

PATH = os.path.join(DATA_DIR, "advisor.json")

HW_SCRIPT = r"""
$cs=Get-CimInstance Win32_ComputerSystem; $os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_Processor | Select -First 1
$enc=(Get-CimInstance Win32_SystemEnclosure | Select -First 1).ChassisTypes
$mem=@(Get-CimInstance Win32_PhysicalMemory | Select Capacity,Speed,ConfiguredClockSpeed,DeviceLocator,Manufacturer,PartNumber)
$slots=(Get-CimInstance Win32_PhysicalMemoryArray | Measure-Object MemoryDevices -Sum).Sum
$gpus=@(Get-CimInstance Win32_VideoController | Select Name,AdapterCompatibility,DriverVersion,
  @{n='DriverDate';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}}},AdapterRAM)
$disks=@(Get-PhysicalDisk | Select DeviceId,FriendlyName,@{n='MediaType';e={[string]$_.MediaType}},@{n='BusType';e={[string]$_.BusType}},Size,@{n='Health';e={[string]$_.HealthStatus}})
$sys=$null; try { $sys=(Get-Partition -DriveLetter $env:SystemDrive.Substring(0,1) | Get-Disk).Number } catch {}
$dg=Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -EA SilentlyContinue
$pf=(Get-CimInstance Win32_PageFileUsage | Measure-Object AllocatedBaseSize -Sum).Sum
[pscustomobject]@{
  Manufacturer=$cs.Manufacturer; Model=$cs.Model; Chassis=@($enc); Os=$os.Caption; Build=$os.BuildNumber
  Cpu=$cpu.Name; Cores=$cpu.NumberOfCores; Threads=$cpu.NumberOfLogicalProcessors; CpuMaxMhz=$cpu.MaxClockSpeed
  Memory=$mem; Slots=$slots; Gpus=$gpus; Disks=$disks; SystemDisk=$sys
  Vbs=$dg.VirtualizationBasedSecurityStatus; Hvci=@($dg.SecurityServicesRunning) -contains 2
  PageFileMB=$pf; AutoPageFile=$cs.AutomaticManagedPagefile
}"""

LAPTOP_CHASSIS = {8, 9, 10, 11, 14, 30, 31, 32}
INTEGRATED = re.compile(r"(intel.*(uhd|iris|hd graphics|arc graphics)|radeon\(tm\) graphics|radeon graphics|vega \d+ graphics|microsoft basic)", re.I)
HYBRID_CPU = re.compile(r"(1[2-4]th gen|core\(tm\) (ultra|i\d-1[2-4]\d{2,3})|core ultra|i[3579]-1[2-4]\d{3})", re.I)


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _hardware_windows():
    d = ps_json(HW_SCRIPT, timeout=120)
    d = d[0] if isinstance(d, list) else d
    mem = d.get("Memory") or []
    gpus = [{"name": g["Name"], "vendor": g.get("AdapterCompatibility"), "driver": g.get("DriverVersion"),
             "driver_date": g.get("DriverDate"), "integrated": bool(INTEGRATED.search(g["Name"] or ""))} for g in d.get("Gpus") or []]
    disks = [{"name": x["FriendlyName"], "type": "SSD" if x["MediaType"] == "SSD" or x["BusType"] == "NVMe" else
              ("HDD" if x["MediaType"] == "HDD" else "Inconnu"), "bus": x["BusType"], "size": x["Size"], "health": x["Health"],
              "system": str(x["DeviceId"]) == str(d.get("SystemDisk"))} for x in d.get("Disks") or []]
    battery = None
    try:
        battery = psutil.sensors_battery()
    except Exception:
        pass
    return {
        "manufacturer": d.get("Manufacturer"), "model": d.get("Model"), "os": d.get("Os"), "build": int(d.get("Build") or 0),
        "laptop": bool(set(d.get("Chassis") or []) & LAPTOP_CHASSIS) or battery is not None,
        "cpu": {"name": (d.get("Cpu") or "").strip(), "cores": d.get("Cores"), "threads": d.get("Threads"), "max_mhz": d.get("CpuMaxMhz")},
        "ram": {"total": psutil.virtual_memory().total, "sticks": len(mem), "slots": d.get("Slots") or len(mem),
                "speed": max([m.get("Speed") or 0 for m in mem] or [0]), "configured": max([m.get("ConfiguredClockSpeed") or 0 for m in mem] or [0]),
                "modules": [{"size": m.get("Capacity"), "slot": m.get("DeviceLocator"), "part": (m.get("PartNumber") or "").strip()} for m in mem]},
        "gpus": gpus, "disks": disks, "display": display.info(),
        "vbs": d.get("Vbs") == 2, "hvci": bool(d.get("Hvci")),
        "pagefile_mb": d.get("PageFileMB") or 0, "auto_pagefile": d.get("AutoPageFile"),
    }


def _hardware_generic():
    """Hors Windows (mode démo) : inventaire minimal via psutil."""
    st = monitor.STATIC or {}
    g = gpu.read()
    return {
        "manufacturer": st.get("manufacturer") or "", "model": st.get("model") or platform.node(), "os": st.get("os"), "build": 0,
        "laptop": psutil.sensors_battery() is not None if hasattr(psutil, "sensors_battery") else False,
        "cpu": {"name": st.get("cpu_name"), "cores": psutil.cpu_count(False), "threads": psutil.cpu_count(), "max_mhz": None},
        "ram": {"total": psutil.virtual_memory().total, "sticks": 0, "slots": 0, "speed": 0, "configured": 0, "modules": []},
        "gpus": [{"name": x["name"], "vendor": x["vendor"], "driver": x.get("driver"), "driver_date": None, "integrated": False} for x in g],
        "disks": [], "display": None, "vbs": False, "hvci": False, "pagefile_mb": 0, "auto_pagefile": True,
    }


def _tier(hw):
    ram_gb = hw["ram"]["total"] / 1024 ** 3
    threads = hw["cpu"]["threads"] or 0
    dgpu = [g for g in hw["gpus"] if not g["integrated"]]
    sys_disk = next((d for d in hw["disks"] if d["system"]), None)
    score = 0
    score += 2 if ram_gb >= 30 else 1 if ram_gb >= 15 else 0
    score += 2 if threads >= 16 else 1 if threads >= 8 else 0
    strong_gpu = re.compile(r"rtx\s?\d0[6-9]0|rx\s?[67]\d00|rx\s?9\d{3}", re.I)
    score += 2 if any(strong_gpu.search(g["name"]) for g in dgpu) else 1 if dgpu else 0
    score += 0 if sys_disk and sys_disk["type"] == "HDD" else 1
    return ("Haut de gamme", "high") if score >= 6 else ("Milieu de gamme", "mid") if score >= 3 else ("Entrée de gamme", "low")


def _f(fid, severity, title, detail, action=None, auto=False, page=None):
    return {"id": fid, "severity": severity, "title": title, "detail": detail, "action": action, "auto": auto, "page": page}


def _findings(hw):
    out = []
    ram_gb = hw["ram"]["total"] / 1024 ** 3
    threads = hw["cpu"]["threads"] or 0
    laptop = hw["laptop"]
    dgpu = [g for g in hw["gpus"] if not g["integrated"]]
    sys_disk = next((d for d in hw["disks"] if d["system"]), None)

    # --- Corrigeable automatiquement ---
    if IS_WINDOWS:
        pending = [t for t in tweaks.list_tweaks() if t["recommended"] and not t["applied"]]
        if pending:
            out.append(_f("tweaks", "important", f"{len(pending)} optimisation(s) Windows recommandée(s) non appliquée(s)",
                          ", ".join(t["name"] for t in pending[:5]) + ("…" if len(pending) > 5 else "") + ". Toutes réversibles.",
                          "tweaks_recommended", True, "tweaks"))
        if ram_gb <= 8.5 or threads <= 4:
            out.append(_f("light", "important", "PC modeste : allègement de Windows",
                          f"{ram_gb:.0f} Go de RAM / {threads} threads : effets visuels réduits et applis en arrière-plan bloquées libèrent des ressources.",
                          "light_profile", True))
        svc = [s for s in services.list_services() if not s["optimized"] and s["risk"] == "faible"
               and not (s["name"] == "SysMain" and sys_disk and sys_disk["type"] == "HDD")]
        if svc:
            out.append(_f("services", "conseil", f"{len(svc)} service(s) inutile(s) actif(s)",
                          ", ".join(s["label"] for s in svc[:4]) + ". Désactivation sûre et réversible.", "services_safe", True, "processes"))
    d = hw.get("display")
    if d and d["max"] > d["current"]:
        out.append(_f("refresh", "critique", f"Écran réglé à {d['current']} Hz au lieu de {d['max']} Hz",
                      f"Votre écran peut afficher {d['max']} images/s mais Windows est limité à {d['current']} Hz : "
                      "les FPS au-delà ne sont pas visibles. Correction instantanée.", "display_refresh", True))
    for dk in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(dk.mountpoint)
        except (OSError, PermissionError):
            continue
        if u.percent > 85 and (not IS_WINDOWS or dk.mountpoint.upper().startswith(os.environ.get("SystemDrive", "C:").upper())):
            out.append(_f("disk_full", "important", f"Disque {dk.mountpoint} rempli à {u.percent:.0f} %",
                          "Moins de 15 % d'espace libre ralentit Windows et les mises à jour. Nettoyage automatique des fichiers inutiles.",
                          "clean", True, "clean"))
            break
    if sys_disk and sys_disk["type"] == "SSD":
        out.append(_f("trim", "conseil", "SSD système : optimisation TRIM", "Garde les performances d'écriture du SSD au maximum.", "trim", True))

    # --- Conseils matériels (non automatisables) ---
    r = hw["ram"]
    if r["sticks"] == 1 and (r["slots"] or 0) >= 2:
        out.append(_f("single_channel", "critique", "RAM en simple canal (1 seule barrette)",
                      "Ajouter une 2e barrette identique double la bande passante mémoire : souvent +20 à 30 % de FPS sur CS2, Valorant, LoL. "
                      "C'est l'amélioration la plus rentable de ce PC."))
    if r["speed"] and r["configured"] and r["configured"] < r["speed"] - 100 and not laptop:
        out.append(_f("xmp", "important", f"RAM bridée à {r['configured']} MHz au lieu de {r['speed']} MHz",
                      "Activez le profil XMP / EXPO dans le BIOS de la carte mère."))
    if ram_gb < 15.5:
        out.append(_f("ram_amount", "conseil", f"{ram_gb:.0f} Go de RAM", "16 Go sont recommandés pour jouer avec Discord et un navigateur ouverts."))
    if sys_disk and sys_disk["type"] == "HDD":
        out.append(_f("hdd", "critique", "Windows est installé sur un disque dur mécanique",
                      "Un SSD rend le PC 5 à 10 fois plus réactif (démarrage, chargements, saccades). Priorité n°1 d'amélioration."))
    for dk in hw["disks"]:
        if dk.get("health") and dk["health"] not in ("Healthy", "OK", "Sain"):
            out.append(_f("disk_health", "critique", f"Disque « {dk['name']} » en mauvaise santé ({dk['health']})",
                          "Sauvegardez vos données rapidement (page Sauvegarde)."), )
    for g in dgpu or hw["gpus"][:1]:
        if g.get("driver_date"):
            try:
                age = (date.today() - date.fromisoformat(g["driver_date"])).days
            except ValueError:
                age = 0
            if age > 180:
                out.append(_f("gpu_driver", "important", f"Pilote graphique ancien ({age // 30} mois)",
                              f"{g['name']} — pilote du {g['driver_date']}. Les nouveaux pilotes apportent souvent des gains dans les jeux récents.",
                              page="updates"))
    if laptop and dgpu and any(g["integrated"] for g in hw["gpus"]):
        out.append(_f("hybrid", "conseil", "Portable à double GPU",
                      "Overdrive force déjà vos jeux sur le GPU dédié. Pour encore plus de FPS : mode MUX « Ultimate / GPU dédié » "
                      "dans l'outil du constructeur (redémarrage requis)."))
    if hw.get("hvci"):
        out.append(_f("hvci", "conseil", "Intégrité de la mémoire (HVCI) activée",
                      "Protection de sécurité qui peut coûter 5 à 10 % de FPS dans certains jeux. À désactiver seulement en connaissance de cause : "
                      "Sécurité Windows → Sécurité de l'appareil → Isolation du noyau."))
    if HYBRID_CPU.search(hw["cpu"]["name"] or "") and hw["build"] and hw["build"] < 22000:
        out.append(_f("win11", "important", "Processeur à cœurs hybrides sous Windows 10",
                      "Votre CPU (cœurs P + E) est mieux exploité par Windows 11, dont le planificateur répartit correctement les jeux."))
    if hw.get("pagefile_mb", 1) == 0 and ram_gb < 32 and IS_WINDOWS:
        out.append(_f("pagefile", "critique", "Fichier d'échange désactivé",
                      "Sans fichier d'échange, les jeux peuvent planter par manque de mémoire. Réactivez la gestion automatique."))
    if IS_WINDOWS:
        n_start = sum(1 for s in startup.list_items() if s["enabled"])
        if n_start > 8:
            out.append(_f("startup", "conseil", f"{n_start} programmes au démarrage", "Désactivez ceux dont vous n'avez pas besoin au démarrage.",
                          page="processes"))
    if laptop:
        out.append(_f("plug", "conseil", "Jouez branché sur secteur",
                      "Sur batterie, le GPU d'un portable est fortement bridé (souvent 50 % de FPS en moins)."))
    oem = asus.oem_tool()
    if oem and not asus.is_asus():
        out.append(_f("oem", "conseil", f"Outil constructeur : {oem['tool']}", oem["tip"]))
    order = {"critique": 0, "important": 1, "conseil": 2}
    return sorted(out, key=lambda f: (order[f["severity"]], not f["auto"]))


def analyze(job=None):
    if job:
        job.update(0.1, "Inventaire du matériel…")
    hw = _hardware_windows() if IS_WINDOWS else _hardware_generic()
    if job:
        job.update(0.6, "Diagnostic…")
    tier, tier_id = _tier(hw)
    findings = _findings(hw)
    result = {"ts": time.time(), "hardware": hw, "tier": tier, "tier_id": tier_id, "findings": findings,
              "auto_count": sum(1 for f in findings if f["auto"])}
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    journal.log("advisor", f"Analyse du PC : {tier}, {len(findings)} point(s), {result['auto_count']} corrigeable(s) automatiquement")
    return result


def last():
    return _load()


# --- Optimisation automatique ------------------------------------------------------------------------------------------

def _run_action(action, job):
    if action == "restore_point":
        from .backup import create_restore_point
        return create_restore_point("Overdrive - avant optimisation automatique")
    if action == "tweaks_recommended":
        res = tweaks.apply_recommended()
        return f"{sum(1 for r in res if not r.get('error'))} réglage(s) appliqué(s)"
    if action == "light_profile":
        for tid in ("visual_fx", "background_apps_off"):
            tweaks.apply(tid)
        return "effets visuels réduits, applis d'arrière-plan bloquées"
    if action == "services_safe":
        hw = (_load() or {}).get("hardware", {})
        hdd = any(d["system"] and d["type"] == "HDD" for d in hw.get("disks", []))
        done = 0
        for s in services.list_services():
            if not s["optimized"] and s["risk"] == "faible" and not (s["name"] == "SysMain" and hdd):
                services.optimize(s["name"])
                done += 1
        return f"{done} service(s) optimisé(s)"
    if action == "display_refresh":
        r = display.set_max_refresh()
        return f"{r.get('from')} → {r.get('to')} Hz" if r.get("changed") else "déjà au maximum"
    if action == "clean":
        ids = [c["id"] for c in cleaner.list_categories() if c["default"]]
        r = cleaner.clean(None, ids)
        return f"{r['freed'] / 1024 ** 2:.0f} Mo libérés"
    if action == "trim":
        return "TRIM effectué" if maintenance.run_tool(job, "trim")["ok"] else "TRIM terminé avec avertissements"
    raise ValueError(f"Action inconnue : {action}")


def optimize(job, actions=None):
    data = _load() or analyze(job)
    available = [f["action"] for f in data["findings"] if f["auto"]]
    actions = [a for a in (actions or available) if a in available]
    steps = ["restore_point"] + actions
    report = []
    for i, a in enumerate(steps):
        job.update(i / len(steps), {"restore_point": "Point de restauration de sécurité…"}.get(a, f"Optimisation : {a}…"))
        try:
            report.append({"action": a, "ok": True, "detail": _run_action(a, job)})
        except Exception as e:
            report.append({"action": a, "ok": False, "detail": str(e)})
    journal.log("advisor", f"Optimisation automatique : {sum(r['ok'] for r in report)}/{len(report)} étape(s) réussie(s)", "success")
    config.update({"state": {"last_auto_optimize": time.time()}})
    new = analyze()
    return {"report": report, "analysis": new}
