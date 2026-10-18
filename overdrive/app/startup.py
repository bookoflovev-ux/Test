"""Programmes au démarrage : même mécanisme que le Gestionnaire des tâches (clés StartupApproved, réversible)."""
import os
import struct
import time

from . import journal
from .system import IS_WINDOWS, NotSupported, env_path

if IS_WINDOWS:
    import winreg

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
ENABLED = bytes([2]) + bytes(11)


def _sources():
    hk, hl = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
    return [
        ("HKCU", hk, RUN, "Run"), ("HKLM", hl, RUN, "Run"), ("HKLM", hl, RUN32, "Run32"),
    ]


def _folders():
    return [("HKCU", env_path("%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup")),
            ("HKLM", env_path("%ProgramData%\\Microsoft\\Windows\\Start Menu\\Programs\\StartUp"))]


def _hive(name):
    return winreg.HKEY_CURRENT_USER if name == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def _approved_state(hive_name, sub, name):
    try:
        with winreg.OpenKey(_hive(hive_name), f"{APPROVED}\\{sub}") as k:
            data, _ = winreg.QueryValueEx(k, name)
            return not (data and data[0] & 1)  # bit 0 à 1 => désactivé
    except OSError:
        return True


def list_items():
    if not IS_WINDOWS:
        return []
    items = []
    for hive_name, hive, path, sub in _sources():
        try:
            with winreg.OpenKey(hive, path) as k:
                i = 0
                while True:
                    try:
                        name, cmd, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    items.append({"id": f"{hive_name}|{sub}|{name}", "name": name, "command": str(cmd),
                                  "location": f"{hive_name}\\{path}", "scope": "Tous les utilisateurs" if hive_name == "HKLM" else "Utilisateur",
                                  "enabled": _approved_state(hive_name, sub, name)})
        except OSError:
            pass
    for hive_name, folder in _folders():
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.lower() == "desktop.ini":
                    continue
                items.append({"id": f"{hive_name}|StartupFolder|{f}", "name": os.path.splitext(f)[0],
                              "command": os.path.join(folder, f), "location": folder,
                              "scope": "Tous les utilisateurs" if hive_name == "HKLM" else "Utilisateur",
                              "enabled": _approved_state(hive_name, "StartupFolder", f)})
    return sorted(items, key=lambda x: x["name"].lower())


def set_enabled(item_id: str, enabled: bool):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    hive_name, sub, name = item_id.split("|", 2)
    if enabled:
        data = ENABLED
    else:
        ft = int((time.time() + 11644473600) * 10_000_000)  # FILETIME
        data = bytes([3, 0, 0, 0]) + struct.pack("<Q", ft)
    with winreg.CreateKeyEx(_hive(hive_name), f"{APPROVED}\\{sub}", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)
    journal.log("startup", f"Démarrage {'activé' if enabled else 'désactivé'} : {name}", "success")
    return {"id": item_id, "enabled": enabled}
