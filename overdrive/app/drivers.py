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


def _download(job, url, label, lo=0.0, hi=1.0):
    u = urllib.parse.urlparse(url or "")
    if u.scheme != "https" or not DOWNLOAD_HOSTS.search(u.hostname or ""):
        raise ValueError("Téléchargement refusé : seuls les serveurs officiels ASUS et NVIDIA sont autorisés.")
    folder = os.path.join(os.path.expanduser("~"), "Downloads", "Overdrive")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.basename(u.path) or "pilote")
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
            if total and job:
                job.update(lo + (hi - lo) * done / total, f"{label} — téléchargement {done / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} Mo")
    os.replace(dest + ".part", dest)
    return dest


def _extract(path):
    out = os.path.splitext(path)[0]
    with zipfile.ZipFile(path) as z:
        for m in z.namelist():  # protection contre les chemins sortant du dossier
            if os.path.isabs(m) or ".." in m.replace("\\", "/").split("/"):
                raise ValueError("Archive suspecte, extraction annulée.")
        z.extractall(out)
    return out


def _find(folder, pattern):
    rx = re.compile(pattern, re.I)
    for root, _, files in os.walk(folder):
        for f in files:
            if rx.fullmatch(f):
                return os.path.join(root, f)
    return None


def _install_file(path, label):
    """Installe un paquet téléchargé. Renvoie (ok, méthode, détail, redémarrage)."""
    from .system import run
    if path.lower().endswith(".exe"):  # NVIDIA : installeur officiel en mode silencieux
        r = run([path, "-s", "-noreboot"], timeout=2400)
        return r["code"] == 0, "installeur NVIDIA (silencieux)", "écran noir quelques secondes pendant l'installation : normal", True
    folder = _extract(path) if path.lower().endswith(".zip") else os.path.dirname(path)
    setup = _find(folder, r"asussetup\.exe")
    if setup:  # paquets ASUS : installeur officiel en mode silencieux
        r = run([setup, "-s"], timeout=2400)
        if r["code"] in (0, 3010):
            return True, "installeur ASUS (silencieux)", "", r["code"] == 3010
    if _find(folder, r".+\.inf"):  # pilotes « INF » : installation directe par Windows
        r = run(["pnputil", "/add-driver", os.path.join(folder, "*.inf"), "/subdirs", "/install"], timeout=1200)
        if r["code"] in (0, 3010) or "ajouté" in r["out"].lower() or "added" in r["out"].lower():
            return True, "pnputil (Windows)", "", r["code"] == 3010
    other = _find(folder, r"(setup|install)\.exe")
    if other:  # dernier recours : installeur interactif
        os.startfile(other)
        return True, "installeur ouvert", "terminez l'installation dans la fenêtre qui s'est ouverte", False
    os.startfile(folder)
    return False, "manuel", "paquet sans installeur reconnu : dossier ouvert", False


def install_many(job, items):
    """Télécharge et installe les pilotes choisis, après un point de restauration. Le BIOS est seulement préparé."""
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    results = []
    n = len(items) or 1
    job.update(0.02, "Point de restauration de sécurité…")
    try:
        from .backup import create_restore_point
        create_restore_point("Overdrive - avant mise a jour des pilotes")
        restore = True
    except Exception:
        restore = False
    wu = [it for it in items if it.get("action") == "windows" and it.get("id")]
    if wu:
        job.update(0.05, f"Windows Update : installation de {len(wu)} pilote(s)…")
        try:
            r = updates.windows_install(None, "driver", [it["id"] for it in wu])
            for it in wu:
                results.append({"key": it.get("key"), "title": it.get("title"), "ok": r.get("result") in (2, 3),
                                "method": "Windows Update", "detail": r.get("status", ""), "reboot": bool(r.get("reboot"))})
        except Exception as e:
            results += [{"key": it.get("key"), "title": it.get("title"), "ok": False, "method": "Windows Update", "detail": str(e), "reboot": False} for it in wu]
    others = [it for it in items if it not in wu]
    for i, it in enumerate(others):
        lo, hi = (len(wu) + i) / n, (len(wu) + i + 1) / n
        label = f"[{len(wu) + i + 1}/{n}] {it.get('title', 'Pilote')}"
        try:
            path = _download(job, it.get("url"), label, lo, lo + (hi - lo) * 0.6)
            if it.get("action") == "bios":
                folder = _extract(path) if path.lower().endswith(".zip") else os.path.dirname(path)
                os.startfile(folder)
                results.append({"key": it.get("key"), "title": it.get("title"), "ok": True, "method": "BIOS préparé",
                                "detail": "Installez-le portable branché via MyASUS ou EZ Flash (F2 au démarrage) avec ce fichier.", "reboot": False})
                continue
            job.update(lo + (hi - lo) * 0.65, f"{label} — installation…")
            ok, method, detail, reboot = _install_file(path, label)
            results.append({"key": it.get("key"), "title": it.get("title"), "ok": ok, "method": method, "detail": detail, "reboot": reboot})
        except Exception as e:
            results.append({"key": it.get("key"), "title": it.get("title"), "ok": False, "method": "", "detail": str(e), "reboot": False})
    ok = sum(1 for r in results if r["ok"])
    journal.log("drivers", f"Pilotes : {ok}/{len(results)} installé(s)" + (" (point de restauration créé)" if restore else ""),
                "success" if ok == len(results) else "warning")
    return {"results": results, "restore_point": restore, "reboot": any(r["reboot"] for r in results)}
