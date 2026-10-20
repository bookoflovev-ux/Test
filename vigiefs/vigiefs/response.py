"""Réponses aux incidents sur un utilisateur : fermer ses sessions, bloquer ou débloquer son accès à un partage,
désactiver ou réactiver son compte. Toutes les actions sont réversibles (sauf la fermeture de session, sans
effet durable) et journalisées par l'appelant."""

import os
import re

from . import smb, system
from .activity import login_of, user_key


def _same(a, b):
    a, b = user_key(a), user_key(b)
    return a == b or ("\\" not in a or "\\" not in b) and login_of(a) == login_of(b)


def user_sessions(user):
    return [s for s in smb.sessions() if _same(s.get("user"), user)]


def user_open_files(user):
    return [f for f in smb.open_files() if _same(f.get("user"), user)]


def close_sessions(user):
    """Ferme toutes les sessions SMB de l'utilisateur (ses fichiers ouverts sont libérés). Renvoie leur nombre."""
    sessions = user_sessions(user)
    for s in sessions:
        smb.close_session(s["id"])
    return len(sessions)


def smb_names(share):
    """Noms des partages SMB qui exposent ce dossier supervisé (même chemin ou chemin parent)."""
    target = os.path.normcase(os.path.normpath(share["path"]))
    names = []
    for s in smb.shares():
        p = os.path.normcase(os.path.normpath(s.get("path") or ""))
        if p and (target == p or target.startswith(p.rstrip("\\/") + os.sep)):
            names.append(s["name"])
    return names


def _account(user):
    key = user_key(user)
    return key if "\\" in key else login_of(key)


def block(user, shares):
    """Refus d'accès SMB au niveau du partage (Block-SmbShareAccess) : les droits NTFS ne sont pas modifiés."""
    if not system.IS_WINDOWS:
        raise system.CommandError("Sous Samba, ajoutez l'utilisateur à « invalid users » du partage dans smb.conf.")
    names = sorted({n for s in shares for n in smb_names(s)})
    if not names:
        raise system.CommandError("Aucun partage SMB n'expose ce dossier.")
    for n in names:
        system.powershell(f"Block-SmbShareAccess -Name {system.ps_quote(n)} -AccountName {system.ps_quote(_account(user))} "
                          "-Force | Out-Null; '{}'")
    return names


def unblock(user, shares):
    if not system.IS_WINDOWS:
        raise system.CommandError("Sous Samba, retirez l'utilisateur de « invalid users » dans smb.conf.")
    names = sorted({n for s in shares for n in smb_names(s)})
    for n in names:
        system.powershell(f"Unblock-SmbShareAccess -Name {system.ps_quote(n)} -AccountName "
                          f"{system.ps_quote(_account(user))} -Force | Out-Null; '{{}}'")
    return names


def blocked(user):
    """Partages SMB où l'utilisateur a un refus d'accès (Windows)."""
    if not system.IS_WINDOWS:
        return []
    data = system.powershell("@(Get-SmbShare | Where-Object { -not $_.Special } | Get-SmbShareAccess | "
                             "Where-Object { [string]$_.AccessControlType -eq 'Deny' } | Select-Object Name, "
                             "@{n='account';e={[string]$_.AccountName}}) | ConvertTo-Json -Compress", timeout=120)
    rows = data if isinstance(data, list) else [data] if data else []
    return sorted({r["Name"] for r in rows if _same(r.get("account"), user)})


def set_account(user, enabled):
    """Désactive / réactive le compte (Active Directory si disponible, sinon compte local)."""
    login = login_of(user_key(user))
    if system.IS_WINDOWS:
        verb = "Enable" if enabled else "Disable"
        if system.capabilities().get("ad_module"):
            system.powershell(f"{verb}-ADAccount -Identity {system.ps_quote(login)}; '{{}}'")
            return "Active Directory"
        system.powershell(f"{verb}-LocalUser -Name {system.ps_quote(login)}; '{{}}'")
        return "compte local"
    system.run(["usermod", "-U" if enabled else "-L", login], timeout=30)
    try:
        system.run(["smbpasswd", "-e" if enabled else "-d", login], timeout=30)
    except system.CommandError:
        pass
    return "compte local et Samba"


def ntfs_quotas():
    """Quotas de disque NTFS par utilisateur (Win32_DiskQuota), si activés sur les volumes."""
    if not system.IS_WINDOWS:
        return []
    data = system.powershell("@(Get-CimInstance Win32_DiskQuota -ErrorAction SilentlyContinue | Select-Object "
                             "@{n='volume';e={[string]$_.QuotaVolume}}, @{n='account';e={[string]$_.User}}, "
                             "DiskSpaceUsed, Limit, WarningLimit, Status) | ConvertTo-Json -Compress", timeout=120)
    out = []
    for r in data if isinstance(data, list) else [data] if data else []:
        vol = re.search(r'DeviceID="([^"]+)"', r.get("volume") or "")
        dom = re.search(r'Domain="([^"]*)"', r.get("account") or "")
        name = re.search(r'Name="([^"]*)"', r.get("account") or "")
        if not name:
            continue
        limit = int(r.get("Limit") or -1)
        out.append({"volume": vol.group(1) if vol else r.get("volume"),
                    "user": user_key(f"{dom.group(1)}\\{name.group(1)}" if dom and dom.group(1) else name.group(1)),
                    "used": int(r.get("DiskSpaceUsed") or 0), "limit": limit if limit >= 0 else None,
                    "warning": int(r.get("WarningLimit") or -1) if int(r.get("WarningLimit") or -1) >= 0 else None,
                    "status": {0: "ok", 1: "warning", 2: "exceeded"}.get(r.get("Status"), "ok")})
    return out
