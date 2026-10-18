"""Mises à jour : Windows Update (système + pilotes), applications via winget, pilotes installés, outils ASUS."""
import os
import re
import shutil
import webbrowser

from . import journal, monitor
from .system import IS_WINDOWS, NotSupported, as_list, is_admin, ps, ps_json

CRITERIA = {
    "software": "IsInstalled=0 and IsHidden=0 and Type='Software'",
    "driver": "IsInstalled=0 and IsHidden=0 and Type='Driver'",
    "all": "IsInstalled=0 and IsHidden=0",
}
DRIVER_CLASSES = {"DISPLAY": "Carte graphique", "NET": "Réseau", "MEDIA": "Audio", "BLUETOOTH": "Bluetooth",
                  "SYSTEM": "Système / chipset", "HIDCLASS": "Périphériques HID", "MOUSE": "Souris", "KEYBOARD": "Clavier",
                  "SCSIADAPTER": "Stockage", "HDC": "Stockage", "CAMERA": "Caméra", "BIOMETRIC": "Biométrie", "USB": "USB"}


def _need_windows():
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")


def windows_scan(job, kind="all"):
    _need_windows()
    job.update(0.1, "Recherche des mises à jour Windows (cela peut prendre quelques minutes)…")
    data = ps_json(
        "$s=New-Object -ComObject Microsoft.Update.Session;"
        f"$r=$s.CreateUpdateSearcher().Search(\"{CRITERIA[kind]}\");"
        "$r.Updates | % { [pscustomobject]@{Title=$_.Title; Driver=($_.Type -eq 2); KB=($_.KBArticleIDs -join ',');"
        " Size=[int64]$_.MaxDownloadSize; Mandatory=$_.IsMandatory; Severity=$_.MsrcSeverity} }", timeout=900)
    updates = [{"title": u["Title"], "driver": u["Driver"], "kb": u["KB"], "size": u["Size"], "severity": u.get("Severity")}
               for u in as_list(data)]
    journal.log("update", f"Windows Update : {len(updates)} mise(s) à jour disponible(s)")
    return {"updates": updates}


def windows_install(job, kind="all"):
    _need_windows()
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    job.update(0.05, "Téléchargement et installation des mises à jour… (laissez le PC allumé)")
    r = ps(
        "$s=New-Object -ComObject Microsoft.Update.Session;"
        f"$res=$s.CreateUpdateSearcher().Search(\"{CRITERIA[kind]}\");"
        "$col=New-Object -ComObject Microsoft.Update.UpdateColl;"
        "foreach($u in $res.Updates){ if(-not $u.EulaAccepted){$u.AcceptEula()}; [void]$col.Add($u) };"
        "if($col.Count -eq 0){ '{\"installed\":0,\"reboot\":false,\"result\":2}'; exit };"
        "$d=$s.CreateUpdateDownloader(); $d.Updates=$col; [void]$d.Download();"
        "$i=$s.CreateUpdateInstaller(); $i.Updates=$col; $x=$i.Install();"
        "@{installed=$col.Count; result=$x.ResultCode; reboot=$x.RebootRequired} | ConvertTo-Json -Compress", timeout=7200)
    import json
    try:
        res = json.loads(r["out"].splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(r["err"] or r["out"] or "Échec de Windows Update")
    codes = {2: "réussie", 3: "réussie avec erreurs", 4: "échouée", 5: "annulée"}
    res["status"] = codes.get(res.get("result"), "inconnue")
    journal.log("update", f"Windows Update ({kind}) : {res['installed']} installée(s), installation {res['status']}"
                + (" — redémarrage requis" if res.get("reboot") else ""), "success" if res.get("result") in (2, 3) else "error")
    return res


def drivers():
    _need_windows()
    classes = ",".join(f"'{c}'" for c in DRIVER_CLASSES)
    data = as_list(ps_json(
        f"Get-CimInstance Win32_PnPSignedDriver | ? {{ $_.DeviceName -and $_.DeviceClass -in @({classes}) }} | "
        "Select DeviceName,DeviceClass,DriverVersion,Manufacturer,@{n='Date';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}}}",
        timeout=120))
    seen, out = set(), []
    for d in data:
        key = (d["DeviceName"], d["DriverVersion"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": d["DeviceName"], "class": DRIVER_CLASSES.get(d["DeviceClass"], d["DeviceClass"]),
                    "version": d["DriverVersion"], "vendor": d["Manufacturer"], "date": d.get("Date")})
    out.sort(key=lambda x: (x["class"], x["name"]))
    return out


def _parse_winget_table(text):
    lines = [l.rstrip() for l in text.replace("\r", "\n").split("\n")]
    try:
        sep = next(i for i, l in enumerate(lines) if re.fullmatch(r"-{20,}", l.strip()))
    except StopIteration:
        return []
    header = lines[sep - 1]
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    names = header.split()
    if len(starts) < 4:
        return []
    out = []
    for l in lines[sep + 1:]:
        if not l.strip() or re.match(r"^\d+ ", l.strip()) or len(l) < starts[2]:
            continue
        cols = [l[starts[i]: starts[i + 1] if i + 1 < len(starts) else None].strip() for i in range(len(starts))]
        row = dict(zip(names, cols))
        vals = list(row.values())
        if len(vals) >= 4 and vals[1]:
            out.append({"name": vals[0], "id": vals[1], "version": vals[2], "available": vals[3],
                        "source": vals[4] if len(vals) > 4 else ""})
    return out


def winget_available():
    return IS_WINDOWS and bool(shutil.which("winget"))


def apps_scan(job):
    _need_windows()
    if not winget_available():
        raise RuntimeError("winget est absent : installez « App Installer » depuis le Microsoft Store.")
    job.update(0.2, "Recherche des mises à jour d'applications (winget)…")
    r = ps("winget upgrade --include-unknown --accept-source-agreements --disable-interactivity | Out-String -Width 400", timeout=300)
    apps = _parse_winget_table(r["out"])
    journal.log("update", f"Applications : {len(apps)} mise(s) à jour disponible(s)")
    return {"apps": apps}


def apps_upgrade(job, ids=None):
    _need_windows()
    flags = "--silent --accept-source-agreements --accept-package-agreements --disable-interactivity"
    if not ids:
        job.update(0.1, "Mise à jour de toutes les applications…")
        r = ps(f"winget upgrade --all {flags} | Out-String -Width 300", timeout=7200)
        journal.log("update", "Mise à jour de toutes les applications terminée", "success")
        return {"output": r["out"][-3000:]}
    done = []
    for i, pid in enumerate(ids):
        if not re.fullmatch(r"[\w.+\-]+", pid):
            continue
        job.update(i / len(ids), f"Mise à jour de {pid}…")
        r = ps(f"winget upgrade --id '{pid}' -e {flags} | Out-String -Width 300", timeout=1800)
        done.append({"id": pid, "ok": r["ok"]})
    journal.log("update", f"{sum(d['ok'] for d in done)}/{len(done)} application(s) mise(s) à jour", "success")
    return {"results": done}


def vendor_info():
    """Infos constructeur + outils officiels (MyASUS / Armoury Crate) + pilote GPU."""
    st = monitor.STATIC or {}
    info = {"manufacturer": st.get("manufacturer", ""), "model": st.get("model", ""), "bios": st.get("bios", ""), "apps": []}
    is_asus = "asus" in info["manufacturer"].lower() or "tuf" in info["model"].lower()
    info["is_asus"] = is_asus
    if IS_WINDOWS:
        try:
            info["apps"] = [{"name": a["Name"], "app_id": a["AppID"]} for a in as_list(ps_json(
                "Get-StartApps | ? { $_.Name -match 'MyASUS|Armoury|G-Helper|NVIDIA|AMD Software|Radeon' } | Select Name,AppID"))]
        except Exception:
            pass
        info["ghelper"] = any("g-helper" in a["name"].lower() for a in info["apps"])
    model = info["model"] or "TUF Gaming"
    info["links"] = [
        {"label": f"Support ASUS — pilotes & BIOS ({model})", "url": "https://www.asus.com/fr/support/download-center/"},
        {"label": "G-Helper (alternative gratuite et légère à Armoury Crate)", "url": "https://github.com/seerge/g-helper"},
        {"label": "Pilotes NVIDIA GeForce", "url": "https://www.nvidia.com/fr-fr/drivers/"},
        {"label": "Pilotes AMD Radeon", "url": "https://www.amd.com/fr/support/download/drivers.html"},
        {"label": "LibreHardwareMonitor (températures)", "url": "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases"},
    ]
    info["gpu_driver"] = next((g.get("driver") for g in monitor.snapshot().get("gpus", []) if g.get("driver")), None)
    return info


def launch_app(app_id: str):
    _need_windows()
    if not re.fullmatch(r"[\w.!{}\-\\ ]+", app_id):
        raise ValueError("Identifiant d'application invalide")
    os.startfile(f"shell:AppsFolder\\{app_id}")
    return {"ok": True}


def open_url(url):
    if url.startswith("https://"):
        webbrowser.open(url)
    return {"ok": True}
