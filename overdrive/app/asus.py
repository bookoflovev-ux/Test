"""Contrôle des portables ASUS (TUF / ROG / Vivobook…) via le pilote ACPI ASUS (\\\\.\\ATKACPI),
la même interface qu'Armoury Crate et G-Helper : mode de performance, limite de charge, overdrive de l'écran,
état du MUX et du mode Éco GPU. Chaque fonction n'est proposée que si le PC la prend en charge."""
import ctypes
import re
import struct
import threading
import uuid

import psutil

from . import config, journal, monitor
from .system import IS_WINDOWS, NotSupported, as_list, ps_json

CONTROL_CODE = 0x0022240C
DSTS, DEVS = 0x53545344, 0x53564544
DEV = {
    "perf_mode": 0x00120075,      # 0 équilibré, 1 turbo, 2 silencieux
    "perf_mode_vivo": 0x00110019, # variante Vivobook / certains TUF
    "battery_limit": 0x00120057,  # 40..100 %
    "panel_od": 0x00050019,       # overdrive de la dalle (moins de traînées)
    "gpu_eco": 0x00090020,        # 1 = GPU NVIDIA coupé (économie)
    "gpu_mux": 0x00090016,        # 0 = Ultimate (GPU direct), 1 = Standard (Optimus)
}
MODES = {"silent": (2, "Silencieux"), "balanced": (0, "Équilibré"), "turbo": (1, "Turbo")}
# Mode d'alimentation Windows associé (comme G-Helper) — sans effet si un plan personnalisé est actif.
OVERLAY = {"silent": "961cc777-2547-4f9d-8174-7d86181b8a7a", "balanced": "00000000-0000-0000-0000-000000000000",
           "turbo": "ded574b5-45a0-4f42-8737-46345c09c238"}
_lock = threading.Lock()
_game_state = {"previous": None}


def is_asus():
    st = monitor.STATIC or {}
    return "asus" in (st.get("manufacturer") or "").lower() or bool(re.search(r"\b(tuf|rog|zenbook|vivobook)\b", (st.get("model") or "").lower()))


def series():
    model = ((monitor.STATIC or {}).get("model") or "").lower()
    for key, name in (("tuf", "TUF Gaming"), ("rog", "ROG"), ("zenbook", "Zenbook"), ("vivobook", "Vivobook")):
        if key in model:
            return name
    return "ASUS"


# --- Accès bas niveau --------------------------------------------------------------------------------------------------

def _call(method, args: bytes) -> bytes:
    if not IS_WINDOWS:
        raise NotSupported("Contrôle ASUS disponible uniquement sous Windows.")
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.CreateFileW("\\\\.\\ATKACPI", 0xC0000000, 3, None, 3, 0x80, None)
    if h in (None, ctypes.c_void_p(-1).value):
        raise NotSupported("Pilote ASUS (ATKACPI) introuvable : installez « ASUS System Control Interface » via MyASUS.")
    try:
        data = struct.pack("<II", method, len(args)) + args
        inbuf = ctypes.create_string_buffer(data, len(data))
        out = ctypes.create_string_buffer(16)
        ret = wintypes.DWORD()
        with _lock:
            if not k32.DeviceIoControl(h, CONTROL_CODE, inbuf, len(data), out, 16, ctypes.byref(ret), None):
                raise OSError(ctypes.get_last_error(), "DeviceIoControl a échoué")
        return out.raw
    finally:
        k32.CloseHandle(h)


def device_get(dev):
    """Valeur de la fonction, ou un nombre négatif si elle n'existe pas sur ce modèle."""
    raw = _call(DSTS, struct.pack("<II", dev, 0))
    return struct.unpack("<i", raw[:4])[0] - 65536


def device_set(dev, value):
    raw = _call(DEVS, struct.pack("<II", dev, value))
    return struct.unpack("<i", raw[:4])[0]


def _safe_get(dev):
    try:
        return device_get(dev)
    except Exception:
        return None


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


def _set_overlay(mode):
    try:
        fn = ctypes.WinDLL("powrprof").PowerSetActiveOverlayScheme
        fn.argtypes = [_GUID]  # le GUID est passé par valeur
        fn(_GUID.from_buffer_copy(uuid.UUID(OVERLAY[mode]).bytes_le))
    except Exception:
        pass


# --- Fonctions exposées ----------------------------------------------------------------------------------------------------

def _armoury_running():
    names = {"armourycrate.service.exe", "armourycrate.usersessionhelper.exe", "armourycratecontrolinterface.exe", "asusoptimization.exe"}
    found = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if n in names or n == "ghelper.exe":
            found.add("G-Helper" if n == "ghelper.exe" else "Armoury Crate")
    return sorted(found)


def _bios():
    try:
        b = as_list(ps_json("Get-CimInstance Win32_BIOS | Select SMBIOSBIOSVersion,@{n='Date';e={$_.ReleaseDate.ToString('yyyy-MM-dd')}}"))
        return b[0] if b else {}
    except Exception:
        return {}


def status():
    st = monitor.STATIC or {}
    cfg = config.get().get("asus", {})
    info = {"is_asus": is_asus(), "model": st.get("model"), "series": series(), "available": False, "features": {},
            "mode": cfg.get("mode"), "battery_limit": cfg.get("battery_limit"), "conflicts": [], "bios": {}}
    if not info["is_asus"] or not IS_WINDOWS:
        return info
    info["bios"] = _bios()
    info["conflicts"] = _armoury_running()
    try:
        values = {k: device_get(v) for k, v in DEV.items()}
        info["available"] = True
    except Exception as e:
        info["error"] = str(e)
        return info
    perf_dev = "perf_mode" if values["perf_mode"] >= 0 else "perf_mode_vivo" if values["perf_mode_vivo"] >= 0 else None
    info["features"] = {
        "perf_mode": perf_dev is not None,
        "battery_limit": values["battery_limit"] >= 0,
        "panel_od": values["panel_od"] >= 0,
        "gpu_eco": values["gpu_eco"] >= 0,
        "gpu_mux": values["gpu_mux"] >= 0,
    }
    info["panel_od"] = values["panel_od"] == 1 if values["panel_od"] >= 0 else None
    info["gpu_eco"] = values["gpu_eco"] == 1 if values["gpu_eco"] >= 0 else None
    info["gpu_mux"] = ("Standard (Optimus)" if values["gpu_mux"] == 1 else "Ultimate (GPU direct)") if values["gpu_mux"] >= 0 else None
    return info


def _perf_device():
    v = _safe_get(DEV["perf_mode"])
    return DEV["perf_mode"] if v is not None and v >= 0 else DEV["perf_mode_vivo"]


def set_mode(mode, quiet=False):
    if mode not in MODES:
        raise ValueError("Mode inconnu")
    if not is_asus():
        raise NotSupported("Ce PC n'est pas un ASUS.")
    device_set(_perf_device(), MODES[mode][0])
    _set_overlay(mode)
    config.update({"asus": {"mode": mode}})
    if not quiet:
        journal.log("asus", f"Mode ASUS : {MODES[mode][1]}", "success")
    return {"mode": mode}


def set_battery_limit(limit: int):
    limit = int(limit)
    if not 40 <= limit <= 100:
        raise ValueError("La limite doit être comprise entre 40 et 100 %.")
    device_set(DEV["battery_limit"], limit)
    config.update({"asus": {"battery_limit": limit}})
    journal.log("asus", f"Limite de charge de la batterie : {limit} %", "success")
    return {"battery_limit": limit}


def set_panel_od(enabled: bool):
    device_set(DEV["panel_od"], 1 if enabled else 0)
    journal.log("asus", f"Overdrive de l'écran {'activé' if enabled else 'désactivé'}", "success")
    return {"panel_od": enabled}


def apply_saved():
    """Au démarrage : certains modèles oublient la limite de charge après un redémarrage, on la réapplique."""
    if not (IS_WINDOWS and is_asus()):
        return
    cfg = config.get().get("asus", {})
    try:
        if cfg.get("battery_limit"):
            device_set(DEV["battery_limit"], int(cfg["battery_limit"]))
    except Exception:
        pass


def game_started():
    """Passe en Turbo pendant le jeu (sur secteur uniquement) ; renvoie True si le mode a changé."""
    if not (IS_WINDOWS and is_asus()) or not config.get().get("asus", {}).get("turbo_in_games", True):
        return False
    try:
        b = psutil.sensors_battery()
        if b and not b.power_plugged:
            return False
        current = config.get().get("asus", {}).get("mode") or "balanced"
        if current == "turbo":
            return False
        _game_state["previous"] = current
        set_mode("turbo", quiet=True)
        return True
    except Exception:
        return False


def game_ended():
    prev, _game_state["previous"] = _game_state["previous"], None
    if prev:
        try:
            set_mode(prev, quiet=True)
            journal.log("asus", f"Fin de partie : retour au mode {MODES[prev][1]}")
        except Exception:
            pass


OEM_TOOLS = {
    "lenovo": ("Lenovo Vantage / Legion Space", "Mode Performance (Fn+Q), Hybrid Mode off pour le GPU direct."),
    "msi": ("MSI Center", "Scénario « Extreme Performance », MUX « Discrete Graphics Mode »."),
    "dell": ("Alienware Command Center / Dell Power Manager", "Profil « Performance », mode thermique « Ultra Performance »."),
    "alienware": ("Alienware Command Center", "Profil thermique « Performance »."),
    "hp": ("OMEN Gaming Hub", "Mode « Performance », « Advanced Optimus » si disponible."),
    "acer": ("NitroSense / PredatorSense", "Mode « Turbo » ou « Performance »."),
    "razer": ("Razer Synapse", "Mode « Performance » CPU et GPU au maximum."),
    "gigabyte": ("Gigabyte Control Center", "Mode « Gaming » / « Turbo »."),
    "samsung": ("Samsung Settings", "Mode « Haute performance »."),
    "microsoft": ("Surface App", "Mode d'alimentation « Meilleures performances »."),
}


def oem_tool():
    manu = ((monitor.STATIC or {}).get("manufacturer") or "").lower()
    for key, (tool, tip) in OEM_TOOLS.items():
        if key in manu:
            return {"tool": tool, "tip": tip}
    return None
