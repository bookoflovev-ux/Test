"""Recherche de pilotes sur plusieurs sources, pas seulement Windows Update :
- Windows Update (y compris les pilotes « facultatifs ») ;
- ASUS : serveur officiel de mises à jour (pilotes + BIOS du modèle exact, comparés aux identifiants matériels) ;
- NVIDIA : dernier pilote Game Ready officiel pour la carte détectée.
Téléchargement depuis les serveurs officiels uniquement (liste blanche)."""
import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile

from . import journal, updates
from .config import DATA_DIR
from .system import IS_WINDOWS, NotSupported, as_list, ps_json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Overdrive"
ASUS_API = "https://rog.asus.com/support/webapi/product/{kind}?website=global&model={model}&cpu={model}{extra}"
NV_DATA = "https://raw.githubusercontent.com/ZenitH-AT/nvidia-data/main/gpu-data.json"
NV_AJAX = ("https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
           "?func=DriverManualLookup&pfid={pfid}&osID={os}&dch=1&numberOfResults=10&languageCode=1078")
ASUS_SKIP = {"Armoury Crate & Aura Creator Installer", "MyASUS", "ASUS Smart Display Control", "Aura Wallpaper", "Virtual Pet", "ROG Font V1.5"}
DOWNLOAD_HOSTS = re.compile(r"(^|\.)(asus\.com|asus\.com\.cn|nvidia\.com)$", re.I)


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    return json.loads(data.decode("utf-8", errors="replace"))


def _ver(v):
    """'V31.0.101.5592' → (31, 0, 101, 5592) pour comparer des versions."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:6]) or (0,)


def _installed():
    return as_list(ps_json("Get-CimInstance Win32_PnPSignedDriver | ? { $_.DeviceID -and $_.DriverVersion } | "
                           "Select DeviceID,DeviceName,DriverVersion", timeout=120))


# --- ASUS ------------------------------------------------------------------------------------------------------------

def _asus_bios_model():
    b = as_list(ps_json("Get-CimInstance Win32_BIOS | Select SMBIOSBIOSVersion,Manufacturer"))
    raw = (b[0].get("SMBIOSBIOSVersion") if b else "") or ""
    model, _, bios = raw.partition(".")  # ex. « FX507ZV4.311 »
    return model.strip(), bios.strip()


def check_asus(installed):
    from . import asus
    if not asus.is_asus():
        return None
    model, bios = _asus_bios_model()
    if not model:
        raise RuntimeError("Modèle ASUS introuvable dans le BIOS.")
    extra = "&systemCode=rog" if "rog" in (asus.series() or "").lower() else ""
    items = []
    # Pilotes
    data = _get(ASUS_API.format(kind="GetPDDrivers", model=model, extra=extra + "&osid=52"))
    for group in (data.get("Result") or {}).get("Obj") or []:
        seen = set()
        for f in group.get("Files") or []:
            title = f.get("Title", "")
            if title in seen or title in ASUS_SKIP:
                continue
            seen.add(title)
            version = str(f.get("Version", "")).lstrip("Vv")
            hw_ids = [h.get("hardwareid", "").upper() for h in f.get("HardwareInfoList") or [] if h.get("hardwareid")]
            local = [d["DriverVersion"] for d in installed for h in hw_ids if h and h in (d.get("DeviceID") or "").upper()]
            if local:
                status = "update" if all(_ver(version) > _ver(lv) for lv in local) else "ok"
            else:
                status = "unknown"
            items.append({"source": "ASUS", "category": group.get("Name", ""), "title": title, "version": version,
                          "installed": max(local, key=_ver) if local else None, "date": f.get("ReleaseDate"),
                          "url": ((f.get("DownloadUrl") or {}).get("Global")), "status": status, "action": "download"})
    # BIOS
    try:
        data = _get(ASUS_API.format(kind="GetPDBIOS", model=model, extra=extra))
        for group in (data.get("Result") or {}).get("Obj") or []:
            for f in (group.get("Files") or [])[:1]:
                v = str(f.get("Version", "")).lstrip("Vv")
                newer = v.isdigit() and bios.isdigit() and int(v) > int(bios)
                items.append({"source": "ASUS", "category": "BIOS", "title": f.get("Title") or f"BIOS {model}", "version": v,
                              "installed": bios, "date": f.get("ReleaseDate"), "url": (f.get("DownloadUrl") or {}).get("Global"),
                              "status": "update" if newer else "ok", "action": "bios"})
    except Exception as e:
        journal.log("drivers", f"BIOS ASUS : {e}", "warning")
    return {"model": model, "bios": bios, "items": items}


# --- NVIDIA -----------------------------------------------------------------------------------------------------------

def _nv_version(win_version):
    """Version Windows « 32.0.15.6636 » → version NVIDIA « 566.36 »."""
    parts = str(win_version).split(".")
    if len(parts) < 4:
        return None
    digits = (parts[2] + parts[3])[-5:]
    return f"{digits[:3]}.{digits[3:]}"


def _nv_gpu_data():
    path = os.path.join(DATA_DIR, "nvidia-gpu-data.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = _get(NV_DATA)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def check_nvidia():
    gpus = [g for g in as_list(ps_json("Get-CimInstance Win32_VideoController | Select Name,DriverVersion"))
            if "nvidia" in (g.get("Name") or "").lower()]
    if not gpus:
        return None
    g = gpus[0]
    name = re.sub(r"^NVIDIA\s+", "", g["Name"]).strip()
    table = _nv_gpu_data()
    notebook = "laptop" in name.lower() or "max-q" in name.lower()
    pfid = (table["notebook"] if notebook else table["desktop"]).get(name) or table["desktop" if notebook else "notebook"].get(name)
    if not pfid:
        raise RuntimeError(f"Carte « {name} » absente de la base NVIDIA : utilisez l'application NVIDIA.")
    from .advisor import last
    build = ((last() or {}).get("hardware") or {}).get("build") or 0
    os_id = 135 if build >= 22000 else 57
    data = _get(NV_AJAX.format(pfid=pfid, os=os_id))
    ids = data.get("IDS") or []
    if not ids:
        raise RuntimeError("Aucun pilote renvoyé par NVIDIA.")
    infos = [x.get("downloadInfo") or {} for x in ids]
    best = next((i for i in infos if str(i.get("IsCRD")) == "0"), infos[0])  # 0 = Game Ready
    online = best.get("Version")
    local = _nv_version(g.get("DriverVersion"))
    return {"source": "NVIDIA", "category": "Carte graphique", "title": f"Pilote Game Ready — {name}", "version": online,
            "installed": local, "date": (best.get("ReleaseDateTime") or "")[:16], "url": best.get("DownloadURL"),
            "status": "update" if local and online and _ver(online) > _ver(local) else "ok", "action": "download"}


# --- Recherche globale ------------------------------------------------------------------------------------------------

def scan(job):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    result = {"items": [], "errors": {}, "asus": None, "hints": []}
    job.update(0.05, "Lecture des pilotes installés…")
    installed = _installed()
    job.update(0.15, "Windows Update (pilotes, y compris facultatifs)…")
    try:
        wu = updates.windows_scan(None, "driver")
        for u in wu["updates"]:
            result["items"].append({"source": "Windows Update", "category": "Pilote", "title": u["title"], "version": None,
                                    "installed": None, "date": None, "url": None, "status": "update", "action": "windows",
                                    "id": u["id"], "optional": u["optional"]})
    except Exception as e:
        result["errors"]["Windows Update"] = str(e)
    job.update(0.5, "Serveur de mises à jour ASUS…")
    try:
        a = check_asus(installed)
        if a:
            result["asus"] = {"model": a["model"], "bios": a["bios"]}
            result["items"] += a["items"]
    except Exception as e:
        result["errors"]["ASUS"] = f"serveur ASUS injoignable ({e})"
    job.update(0.8, "Serveur de pilotes NVIDIA…")
    try:
        n = check_nvidia()
        if n:
            result["items"].append(n)
    except Exception as e:
        result["errors"]["NVIDIA"] = str(e)
    names = " ".join(d.get("DeviceName") or "" for d in installed).lower()
    if "radeon" in names:
        result["hints"].append({"title": "Carte AMD Radeon", "text": "AMD ne publie pas de service de vérification ouvert : utilisez « AMD Software: Adrenalin Edition » (vérification automatique).",
                                "url": "https://www.amd.com/fr/support/download/drivers.html"})
    if "intel" in names:
        result["hints"].append({"title": "Composants Intel", "text": "« Intel Driver & Support Assistant » vérifie les pilotes Intel (graphique, Wi-Fi, Bluetooth).",
                                "url": "https://www.intel.fr/content/www/fr/fr/support/detect.html"})
    order = {"update": 0, "ok": 1, "unknown": 2}
    result["items"].sort(key=lambda x: (order[x["status"]], x["source"], x["category"]))
    n_up = sum(1 for x in result["items"] if x["status"] == "update")
    journal.log("drivers", f"Pilotes : {n_up} mise(s) à jour trouvée(s) (Windows Update, ASUS, NVIDIA)")
    return result


def download(job, url, title=""):
    """Télécharge un pilote depuis ASUS/NVIDIA dans Téléchargements\\Overdrive, puis lance l'installeur ou ouvre le dossier."""
    u = urllib.parse.urlparse(url or "")
    if u.scheme != "https" or not DOWNLOAD_HOSTS.search(u.hostname or ""):
        raise ValueError("Téléchargement refusé : seuls les serveurs officiels ASUS et NVIDIA sont autorisés.")
    folder = os.path.join(os.path.expanduser("~"), "Downloads", "Overdrive")
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(u.path) or "pilote"
    dest = os.path.join(folder, name)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                job.update(done / total, f"Téléchargement {done / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} Mo")
    os.replace(dest + ".part", dest)
    opened = dest
    if dest.lower().endswith(".zip"):
        out = os.path.splitext(dest)[0]
        with zipfile.ZipFile(dest) as z:
            for m in z.namelist():  # protection contre les chemins sortant du dossier
                if os.path.isabs(m) or ".." in m.replace("\\", "/").split("/"):
                    raise ValueError("Archive suspecte, extraction annulée.")
            z.extractall(out)
        opened = out
    if IS_WINDOWS:
        os.startfile(opened)
    journal.log("drivers", f"Pilote téléchargé : {title or name} → {opened}", "success")
    return {"path": opened, "launched": opened.lower().endswith(".exe")}
