"""Optimisation RAM : vidage des working sets et purge de la liste « standby » (comme RAMMap / ISLC)."""
import ctypes
import time

import psutil

from . import journal
from .system import IS_WINDOWS, NotSupported, is_admin

PROTECTED = {"system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe", "services.exe",
             "memory compression", "secure system"}

if IS_WINDOWS:
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


def _enable_privilege(name: str) -> bool:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
        return False
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(LUID_AND_ATTRIBUTES(luid, 0x2)))
        advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        return ctypes.get_last_error() == 0
    finally:
        kernel32.CloseHandle(token)


def _system_memory_command(command: int) -> int:
    """NtSetSystemInformation(SystemMemoryListInformation) — 2: vider working sets, 3: modified list, 4: standby."""
    ntdll = ctypes.WinDLL("ntdll")
    value = ctypes.c_int(command)
    return ntdll.NtSetSystemInformation(80, ctypes.byref(value), ctypes.sizeof(value))


def trim_working_sets(exclude=()):
    """Demande à chaque processus de libérer sa mémoire inutilisée (EmptyWorkingSet)."""
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    exclude = {e.lower() for e in exclude}
    count = 0
    for p in psutil.process_iter(["pid", "name"]):
        name = (p.info["name"] or "").lower()
        if p.pid in (0, 4) or name in PROTECTED or name in exclude:
            continue
        h = kernel32.OpenProcess(0x0400 | 0x0100, False, p.pid)  # QUERY_INFORMATION | SET_QUOTA
        if h:
            if psapi.EmptyWorkingSet(h):
                count += 1
            kernel32.CloseHandle(h)
    return count


def optimize(purge_standby=True, exclude=()):
    """Optimisation complète de la RAM. Renvoie la mémoire libérée."""
    if not IS_WINDOWS:
        raise NotSupported("Optimisation RAM disponible uniquement sous Windows.")
    before = psutil.virtual_memory().available
    trimmed = trim_working_sets(exclude)
    standby_ok = False
    if purge_standby and is_admin():
        if _enable_privilege("SeProfileSingleProcessPrivilege"):
            standby_ok = _system_memory_command(4) == 0
            _system_memory_command(3)
    time.sleep(0.5)
    after = psutil.virtual_memory().available
    freed = max(after - before, 0)
    msg = f"RAM optimisée : {freed / 1024 ** 2:.0f} Mo libérés ({trimmed} processus allégés"
    msg += ", liste standby purgée)" if standby_ok else ")"
    journal.log("ram", msg, "success")
    return {"freed": freed, "processes_trimmed": trimmed, "standby_purged": standby_ok,
            "note": None if standby_ok or not purge_standby else "Lancez Overdrive en administrateur pour purger la liste standby."}
