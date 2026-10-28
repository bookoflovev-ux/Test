"""Écran principal : fréquence actuelle / maximale et passage à la fréquence maximale (ex. 60 → 144 Hz)."""
import ctypes

from . import journal
from .system import IS_WINDOWS, NotSupported

ENUM_CURRENT = -1
DM_DISPLAYFREQUENCY = 0x400000
CDS_UPDATEREGISTRY, CDS_TEST = 0x1, 0x2

if IS_WINDOWS:
    from ctypes import wintypes

    class _POINTL(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class _DISPLAY(ctypes.Structure):
        _fields_ = [("dmPosition", _POINTL), ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD)]

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD), ("display", _DISPLAY),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD),
        ]


def _mode(i):
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    return dm if ctypes.windll.user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)) else None


def info():
    """{width, height, current, max} pour l'écran principal, à la résolution actuelle."""
    if not IS_WINDOWS:
        return None
    cur = _mode(ENUM_CURRENT)
    if not cur:
        return None
    best, i = cur.dmDisplayFrequency, 0
    while True:
        m = _mode(i)
        if m is None:
            break
        if (m.dmPelsWidth, m.dmPelsHeight) == (cur.dmPelsWidth, cur.dmPelsHeight) and m.dmBitsPerPel == cur.dmBitsPerPel:
            best = max(best, m.dmDisplayFrequency)
        i += 1
    return {"width": cur.dmPelsWidth, "height": cur.dmPelsHeight, "current": cur.dmDisplayFrequency, "max": best}


def set_max_refresh():
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    d = info()
    if not d or d["max"] <= d["current"]:
        return {"changed": False, **(d or {})}
    dm = _mode(ENUM_CURRENT)
    dm.dmDisplayFrequency = d["max"]
    dm.dmFields = DM_DISPLAYFREQUENCY
    user32 = ctypes.windll.user32
    if user32.ChangeDisplaySettingsW(ctypes.byref(dm), CDS_TEST) != 0:
        raise RuntimeError(f"{d['max']} Hz refusé par le pilote graphique.")
    if user32.ChangeDisplaySettingsW(ctypes.byref(dm), CDS_UPDATEREGISTRY) != 0:
        raise RuntimeError("Le changement de fréquence a échoué.")
    journal.log("display", f"Écran : {d['current']} Hz → {d['max']} Hz", "success")
    return {"changed": True, "from": d["current"], "to": d["max"]}
