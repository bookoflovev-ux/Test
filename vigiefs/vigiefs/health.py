"""Bilan de santé et de sécurité du serveur de fichiers (Windows ou Linux/Samba)."""

import os
import platform
import re
import shutil
import time

from . import system
from .smb import samba_shares

CHECK_SCRIPT = r"""
$r = @{}
$fs = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
$r.os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, LastBootUpTime
try { $r.smb = Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol, EnableSMB2Protocol, RequireSecuritySignature, EncryptData, RejectUnencryptedAccess } catch {}
$r.longpaths = (Get-ItemProperty $fs -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
$r.nt83 = (Get-ItemProperty $fs -Name NtfsDisable8dot3NameCreation -ErrorAction SilentlyContinue).NtfsDisable8dot3NameCreation
$r.volumes = @(Get-Volume | Where-Object DriveLetter | Select-Object DriveLetter, FileSystemLabel, FileSystem, Size, SizeRemaining, HealthStatus)
$vols = @{}; foreach ($v in Get-CimInstance Win32_Volume) { if ($v.DriveLetter) { $vols[$v.DeviceID] = $v.DriveLetter } }
$r.shadows = @(Get-CimInstance Win32_ShadowCopy | Group-Object VolumeName | ForEach-Object {
  @{ drive = $vols[$_.Name]; count = $_.Count; latest = (($_.Group | ForEach-Object { $_.InstallDate } | Sort-Object | Select-Object -Last 1)) } })
$r.audit = (auditpol /get /subcategory:'{0CCE921D-69AE-11D9-BED3-505054503030}' /r 2>$null) -join "`n"
$r.fsrm = [bool](Get-Command Get-FsrmQuota -ErrorAction SilentlyContinue)
try { $r.dedup = @(Get-DedupStatus | Select-Object Volume, SavedSpace, OptimizedFilesCount) } catch {}
$r.services = @(Get-Service | Where-Object { $_.Name -match 'Datto|CagService|AEMAgent|ShadowProtect|StorageCraft' -or $_.DisplayName -match 'Datto' } | Select-Object Name, DisplayName, @{n='Status';e={[string]$_.Status}})
try { $r.defender = Get-MpComputerStatus | Select-Object AntivirusEnabled, RealTimeProtectionEnabled, AntivirusSignatureLastUpdated } catch {}
try { $r.cfa = (Get-MpPreference).EnableControlledFolderAccess } catch {}
$r.reboot = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
$r | ConvertTo-Json -Depth 5 -Compress
"""


def _date(v):
    m = re.search(r"/Date\((-?\d+)", str(v or ""))
    if m:
        return int(m.group(1)) / 1000
    try:
        return time.mktime(time.strptime(str(v)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


# Cycle de vie Microsoft (LTSC) : build → (nom, fin du support standard, fin du support étendu).
SERVER_LIFECYCLE = {
    14393: ("Windows Server 2016", "2022-01-11", "2027-01-12"),
    17763: ("Windows Server 2019", "2024-01-09", "2029-01-09"),
    20348: ("Windows Server 2022", "2026-10-13", "2031-10-14"),
    26100: ("Windows Server 2025", "2029-11-13", "2034-11-14"),
}


def lifecycle_check(caption, version, now=None):
    """Support Microsoft de la version de Windows Server (None pour un poste ou une version inconnue)."""
    if "server" not in (caption or "").lower():
        return None
    m = re.search(r"\.(\d{5})", version or "")
    entry = SERVER_LIFECYCLE.get(int(m.group(1))) if m else None
    if not entry:
        return None
    name, mainstream, extended = entry
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    fr = lambda d: "/".join(reversed(d.split("-")))  # noqa: E731
    months_left = (int(extended[:4]) - int(today[:4])) * 12 + int(extended[5:7]) - int(today[5:7])
    if today >= extended:
        status, value = "critical", f"plus de correctifs de sécurité depuis le {fr(extended)}"
    elif months_left <= 18:
        status, value = "warning", f"fin des correctifs de sécurité le {fr(extended)}"
    elif today >= mainstream:
        status, value = "info", f"support étendu (sécurité uniquement) jusqu'au {fr(extended)}"
    else:
        status, value = "ok", f"support standard jusqu'au {fr(mainstream)}, étendu jusqu'au {fr(extended)}"
    return check("lifecycle", "Système", f"Support Microsoft — {name}", status, value,
                 "Planifiez la migration vers une version supportée avant la fin des correctifs de sécurité.",
                 "")


def check(c_id, group, label, status, value="", recommendation="", fix=""):
    return {"id": c_id, "group": group, "label": label, "status": status, "value": value,
            "recommendation": recommendation, "fix": fix}


def windows_checks():
    d = system.powershell(CHECK_SCRIPT, timeout=300) or {}
    out = []
    smb = d.get("smb") or {}
    if smb:
        out.append(check("smb1", "SMB", "Protocole SMB1 désactivé", "critical" if smb.get("EnableSMB1Protocol") else "ok",
                         "activé" if smb.get("EnableSMB1Protocol") else "désactivé",
                         "SMB1 est obsolète et exploité par les rançongiciels (WannaCry).",
                         "Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force"))
        out.append(check("signing", "SMB", "Signature SMB exigée", "ok" if smb.get("RequireSecuritySignature") else "warning",
                         "exigée" if smb.get("RequireSecuritySignature") else "facultative",
                         "Protège contre les attaques de relais NTLM.",
                         "Set-SmbServerConfiguration -RequireSecuritySignature $true -Force"))
        out.append(check("encrypt", "SMB", "Chiffrement SMB", "ok" if smb.get("EncryptData") else "info",
                         "global" if smb.get("EncryptData") else "par partage ou désactivé",
                         "Chiffrez au minimum les partages contenant des données sensibles.",
                         "Set-SmbShare -Name <partage> -EncryptData $true"))
    lp = d.get("longpaths")
    out.append(check("longpaths", "Chemins", "Chemins longs (LongPathsEnabled)", "ok" if lp == 1 else "warning",
                     "activé" if lp == 1 else "désactivé",
                     "Autorise les applications compatibles à dépasser 260 caractères (l'Explorateur reste limité).",
                     "Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' -Name LongPathsEnabled -Value 1"))
    n83 = d.get("nt83")
    out.append(check("8dot3", "Chemins", "Noms courts 8.3 désactivés", "ok" if n83 in (1, 3) else "info",
                     {0: "activés", 1: "désactivés", 2: "par volume", 3: "désactivés (hors volume système)"}.get(n83, "inconnu"),
                     "Les noms 8.3 ralentissent NTFS et posent problème aux outils de synchronisation.",
                     "fsutil 8dot3name set 1"))
    for v in _list(d.get("volumes")):
        size, free = v.get("Size") or 0, v.get("SizeRemaining") or 0
        pct = free * 100 / size if size else 100
        out.append(check(f"vol-{v.get('DriveLetter')}", "Volumes", f"Volume {v.get('DriveLetter')}: {v.get('FileSystemLabel') or ''}",
                         "critical" if pct < 5 else "warning" if pct < 15 else "ok",
                         f"{pct:.0f} % libre · {v.get('FileSystem')} · santé {v.get('HealthStatus')}".replace(".", ","),
                         "Gardez au moins 15 % d'espace libre (performances NTFS, clichés instantanés)."))
    shadows = {s.get("drive"): s for s in _list(d.get("shadows")) if s.get("drive")}
    for v in _list(d.get("volumes")):
        letter = f"{v.get('DriveLetter')}:\\"
        s = shadows.get(letter)
        latest = _date(s.get("latest")) if s else None
        status = "ok" if s and latest and time.time() - latest < 2 * 86400 else "warning" if s else "info"
        out.append(check(f"vss-{v.get('DriveLetter')}", "Versions précédentes", f"Clichés instantanés {letter}", status,
                         f"{s['count']} cliché(s), dernier {time.strftime('%d/%m/%Y %H:%M', time.localtime(latest))}" if s and latest
                         else "aucun", "Les clichés instantanés permettent aux utilisateurs de restaurer eux-mêmes une version "
                         "précédente, en complément de Datto.", "vssadmin create shadow /for=" + letter))
    audit = d.get("audit") or ""
    enabled = "Success" in audit or "Succès" in audit or "Réussite" in audit
    out.append(check("audit", "Traçabilité", "Audit du système de fichiers", "ok" if enabled else "info",
                     "actif" if enabled else "inactif",
                     "Activez l'audit (événement 4663) pour savoir qui a supprimé ou modifié un fichier.",
                     "auditpol /set /subcategory:\"{0CCE921D-69AE-11D9-BED3-505054503030}\" /success:enable /failure:enable"))
    out.append(check("fsrm", "Quotas", "FSRM (gestionnaire de ressources)", "ok" if d.get("fsrm") else "info",
                     "installé" if d.get("fsrm") else "absent",
                     "Nécessaire pour des quotas stricts appliqués par Windows (sinon verrouillage VigieFS).",
                     "Install-WindowsFeature FS-Resource-Manager -IncludeManagementTools"))
    svcs = _list(d.get("services"))
    out.append(check("datto", "Sauvegarde", "Agents Datto / sauvegarde", "ok" if any(s.get("Status") == "Running" for s in svcs) else "warning",
                     ", ".join(f"{s.get('DisplayName')} ({s.get('Status')})" for s in svcs) or "aucun agent détecté",
                     "Vérifiez que l'agent Datto est installé et démarré sur ce serveur."))
    df = d.get("defender") or {}
    if df:
        out.append(check("av", "Sécurité", "Antivirus Microsoft Defender",
                         "ok" if df.get("RealTimeProtectionEnabled") else "warning",
                         "protection en temps réel " + ("active" if df.get("RealTimeProtectionEnabled") else "inactive"),
                         "Ignorez ce point si un autre antivirus est déployé."))
        out.append(check("cfa", "Sécurité", "Accès contrôlé aux dossiers", "ok" if d.get("cfa") == 1 else "info",
                         {0: "désactivé", 1: "activé", 2: "audit"}.get(d.get("cfa"), "inconnu"),
                         "Protection anti-rançongiciel native de Windows (à tester avant activation sur un serveur)."))
    out.append(check("reboot", "Système", "Redémarrage en attente", "warning" if d.get("reboot") else "ok",
                     "oui" if d.get("reboot") else "non", "Planifiez le redémarrage pour finaliser les mises à jour."))
    os_ = d.get("os") or {}
    out.append(check("os", "Système", "Système d'exploitation", "info", f"{os_.get('Caption', '')} {os_.get('Version', '')}"))
    life = lifecycle_check(os_.get("Caption"), os_.get("Version"))
    if life:
        out.append(life)
    return out


def posix_checks(data_dirs, share_count_dirs):
    out = []
    caps = system.capabilities()
    out.append(check("os", "Système", "Système d'exploitation", "info", f"{platform.system()} {platform.release()}"))
    out.append(check("acl_tools", "Droits", "Outils ACL (getfacl / setfacl)", "ok" if caps["getfacl"] and caps["setfacl"] else "warning",
                     "présents" if caps["getfacl"] else "absents", "Nécessaires pour lire et appliquer des droits détaillés.",
                     "apt install acl"))
    try:
        with open("/proc/sys/fs/inotify/max_user_watches") as fh:
            watches = int(fh.read().strip())
        needed = share_count_dirs + 1000
        out.append(check("inotify", "Surveillance", "Limite inotify (surveillance temps réel)",
                         "ok" if watches >= needed else "warning", f"{watches} (dossiers surveillés : {share_count_dirs})",
                         "Chaque dossier surveillé consomme une entrée : augmentez la limite si nécessaire.",
                         f"sysctl -w fs.inotify.max_user_watches={max(524288, needed * 2)}"))
    except (OSError, ValueError):
        pass
    if caps["testparm"]:
        text = system.run(["testparm", "-s", "-v", "--suppress-prompt"], timeout=30, check=False).lower()
        def val(k):
            m = re.search(r"^\s*" + re.escape(k) + r"\s*=\s*(.+)$", text, re.M)
            return m.group(1).strip() if m else ""
        minp = val("server min protocol")
        out.append(check("smb1", "SMB", "Protocole minimal Samba", "critical" if minp in ("nt1", "lanman1", "lanman2", "core") else "ok",
                         minp or "défaut", "Refusez SMB1 : « server min protocol = SMB2_10 ».", "server min protocol = SMB2_10"))
        sign = val("server signing")
        out.append(check("signing", "SMB", "Signature SMB", "ok" if sign in ("mandatory", "required") else "warning",
                         sign or "défaut", "Exigez la signature : « server signing = mandatory ».", "server signing = mandatory"))
        enc = val("server smb encrypt") or val("smb encrypt")
        out.append(check("encrypt", "SMB", "Chiffrement SMB", "ok" if enc in ("required", "mandatory") else "info",
                         enc or "défaut", "« server smb encrypt = required » pour chiffrer les échanges."))
        abe = [s for s in samba_shares() if not s.get("abe")]
        out.append(check("abe", "SMB", "Énumération basée sur l'accès", "ok" if not abe else "info",
                         f"désactivée sur {len(abe)} partage(s)" if abe else "activée",
                         "« access based share enum = yes » masque les partages inaccessibles."))
    else:
        out.append(check("samba", "SMB", "Samba", "info", "non détecté", "Serveur de fichiers non Samba ou outils absents."))
    for path in data_dirs:
        try:
            du = shutil.disk_usage(path)
            pct = du.free * 100 / du.total
            out.append(check(f"disk-{path}", "Volumes", f"Espace libre {path}", "critical" if pct < 5 else "warning" if pct < 15 else "ok",
                             f"{pct:.0f} % libre".replace(".", ","), "Gardez au moins 15 % d'espace libre."))
        except OSError:
            continue
    return out


def run_checks(db, engine):
    shares = engine.shares()
    common = []
    for s in shares:
        ok = os.path.isdir(s["path"])
        common.append(check(f"share-{s['id']}", "Dossiers supervisés", f"{s['name']}", "ok" if ok else "critical",
                            ("accessible" if ok else "inaccessible") + f" · surveillance : {engine.monitor.status.get(s['id'], '—')}"))
    try:
        if system.IS_WINDOWS:
            checks = windows_checks()
        else:
            dirs = db.query_one(
                "SELECT COALESCE(SUM(s.dir_count),0) AS n FROM scans s WHERE s.status='done' AND s.finished_at = "
                "(SELECT MAX(x.finished_at) FROM scans x WHERE x.share_id=s.share_id AND x.status='done')")["n"]
            checks = posix_checks(sorted({os.path.dirname(s["path"]) or s["path"] for s in shares} | {engine.data_dir}), dirs)
    except system.CommandError as exc:
        checks = [check("error", "Système", "Collecte des informations", "warning", str(exc))]
    checks += common
    score = {"ok": 0, "info": 0, "warning": 0, "critical": 0}
    for c in checks:
        score[c["status"]] = score.get(c["status"], 0) + 1
    return {"checked_at": time.time(), "platform": "windows" if system.IS_WINDOWS else "posix", "checks": checks,
            "counts": score}
