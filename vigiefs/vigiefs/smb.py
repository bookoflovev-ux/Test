"""Partages SMB, sessions et fichiers ouverts (Windows : cmdlets SmbShare ; Linux : Samba)."""

import json
import re

from . import system


def _date(v):
    m = re.search(r"/Date\((-?\d+)", str(v or ""))
    return int(m.group(1)) / 1000 if m else None


def _list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


# ================================================================ partages
def shares():
    if system.IS_WINDOWS:
        data = system.powershell("""
$out = foreach ($s in Get-SmbShare | Where-Object { -not $_.Special }) {
  $acc = @(Get-SmbShareAccess -Name $s.Name | ForEach-Object { @{account=[string]$_.AccountName; right=[string]$_.AccessRight; type=[string]$_.AccessControlType} })
  [pscustomobject]@{name=$s.Name; path=$s.Path; description=$s.Description; abe=([string]$s.FolderEnumerationMode -eq 'AccessBased');
    caching=[string]$s.CachingMode; encrypt=[bool]$s.EncryptData; users=$s.CurrentUsers; access=$acc}
}
@($out) | ConvertTo-Json -Depth 4 -Compress
""", timeout=120)
        return [{**s, "access": _list(s.get("access"))} for s in _list(data)]
    return samba_shares()


def samba_shares():
    text = ""
    try:
        text = system.run(["testparm", "-s", "--suppress-prompt"], timeout=30, check=False)
    except system.CommandError:
        try:
            with open("/etc/samba/smb.conf", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return []
    out, cur = [], None
    for line in text.splitlines():
        line = line.split(";")[0].split("#")[0].strip() if not line.strip().startswith("[") else line.strip()
        if not line:
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            name = m.group(1)
            cur = None if name.lower() in ("global", "printers", "print$", "homes") else \
                {"name": name, "path": "", "description": "", "abe": False, "caching": "", "encrypt": False,
                 "users": None, "access": [], "read_only": True}
            if cur is not None:
                out.append(cur)
            continue
        if cur is None or "=" not in line:
            continue
        k, v = (x.strip().lower() for x in line.split("=", 1))
        raw = line.split("=", 1)[1].strip()
        if k == "path":
            cur["path"] = raw
        elif k == "comment":
            cur["description"] = raw
        elif k == "access based share enum":
            cur["abe"] = v in ("yes", "true", "1")
        elif k in ("smb encrypt", "server smb encrypt"):
            cur["encrypt"] = v in ("required", "mandatory", "yes", "desired")
        elif k in ("read only", "writeable", "writable"):
            ro = v in ("yes", "true", "1")
            cur["read_only"] = ro if k == "read only" else not ro
        elif k in ("valid users", "write list", "read list"):
            cur["access"].append({"account": raw, "right": k, "type": "Allow"})
    return out


def set_abe(name, enabled):
    if not system.IS_WINDOWS:
        raise system.CommandError("Sous Samba, ajoutez « access based share enum = yes » dans smb.conf.")
    mode = "AccessBased" if enabled else "Unrestricted"
    system.powershell(f"Set-SmbShare -Name {system.ps_quote(name)} -FolderEnumerationMode {mode} -Force | Out-Null; '{{}}'")


def share_findings(share):
    out = []
    if not share.get("abe"):
        out.append({"severity": "info", "title": "Énumération basée sur l'accès désactivée",
                    "detail": "Les utilisateurs voient des dossiers auxquels ils n'ont pas accès."})
    if not share.get("encrypt"):
        out.append({"severity": "info", "title": "Chiffrement SMB non exigé",
                    "detail": "Activez EncryptData pour les partages contenant des données sensibles."})
    for a in share.get("access", []):
        acc = a.get("account", "").lower()
        if a.get("right", "").lower() == "full" and ("everyone" in acc or "tout le monde" in acc):
            out.append({"severity": "info", "title": "Partage : Tout le monde en contrôle total",
                        "detail": "Acceptable si les droits NTFS restreignent l'accès (bonne pratique) ; "
                                  "vérifiez la matrice NTFS."})
    return out


# ================================================================ sessions et fichiers ouverts
def open_files():
    if system.IS_WINDOWS:
        data = system.powershell("""
@(Get-SmbOpenFile | Select-Object @{n='id';e={[string]$_.FileId}}, @{n='session';e={[string]$_.SessionId}}, Path,
  ShareRelativePath, ClientComputerName, ClientUserName, Locks) | ConvertTo-Json -Compress
""", timeout=120)
        return [{"id": f["id"], "session": f.get("session"), "path": f.get("Path"), "share_path": f.get("ShareRelativePath"),
                 "client": f.get("ClientComputerName"), "user": f.get("ClientUserName"), "locks": f.get("Locks") or 0}
                for f in _list(data) if f.get("Path") and not str(f.get("Path")).endswith("\\")]
    return samba_status()["open_files"]


def sessions():
    if system.IS_WINDOWS:
        data = system.powershell("""
@(Get-SmbSession | Select-Object @{n='id';e={[string]$_.SessionId}}, ClientComputerName, ClientUserName, NumOpens,
  SecondsExists, SecondsIdle, Dialect) | ConvertTo-Json -Compress
""", timeout=120)
        return [{"id": s["id"], "client": s.get("ClientComputerName"), "user": s.get("ClientUserName"),
                 "opens": s.get("NumOpens"), "duration": s.get("SecondsExists"), "idle": s.get("SecondsIdle"),
                 "dialect": s.get("Dialect")} for s in _list(data)]
    return samba_status()["sessions"]


def samba_status():
    try:
        raw = system.run(["smbstatus", "--json"], timeout=30, check=False)
        data = json.loads(raw)
    except (system.CommandError, ValueError):
        return {"sessions": [], "open_files": [], "available": False}
    sess = []
    for sid, s in (data.get("sessions") or {}).items():
        sess.append({"id": sid, "client": s.get("remote_machine") or s.get("hostname"), "user": s.get("username"),
                     "opens": None, "duration": None, "idle": None, "dialect": s.get("session_dialect")})
    files = []
    for path, f in (data.get("open_files") or {}).items():
        for oid, o in (f.get("opens") or {"": {}}).items():
            files.append({"id": oid, "session": None, "path": path, "share_path": f.get("filename"),
                          "client": None, "user": o.get("username") or str(o.get("uid", "")), "locks": 0})
    return {"sessions": sess, "open_files": files, "available": True}


def close_file(file_id):
    if not system.IS_WINDOWS:
        raise system.CommandError("Fermeture individuelle non prise en charge par Samba.")
    system.powershell(f"Close-SmbOpenFile -FileId {int(file_id)} -Force; '{{}}'")


def close_session(session_id):
    if not system.IS_WINDOWS:
        raise system.CommandError("Fermeture de session non prise en charge par Samba.")
    system.powershell(f"Close-SmbSession -SessionId {int(session_id)} -Force; '{{}}'")
