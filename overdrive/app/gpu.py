"""Lecture GPU : NVML (NVIDIA), sinon nvidia-smi, sinon compteurs Windows génériques (AMD/Intel)."""
import shutil
import time

from .system import IS_WINDOWS, ps, run

_nvml = None
_nvml_failed = False
_generic_cache = {"t": 0, "data": []}


def _init_nvml():
    global _nvml, _nvml_failed
    if _nvml or _nvml_failed:
        return _nvml
    try:
        import pynvml
        pynvml.nvmlInit()
        _nvml = pynvml
    except Exception:
        _nvml_failed = True
    return _nvml


def _read_nvml(nv):
    gpus = []
    driver = nv.nvmlSystemGetDriverVersion()
    driver = driver.decode() if isinstance(driver, bytes) else driver
    for i in range(nv.nvmlDeviceGetCount()):
        h = nv.nvmlDeviceGetHandleByIndex(i)
        name = nv.nvmlDeviceGetName(h)
        name = name.decode() if isinstance(name, bytes) else name
        util = nv.nvmlDeviceGetUtilizationRates(h)
        mem = nv.nvmlDeviceGetMemoryInfo(h)
        g = {"name": name, "vendor": "NVIDIA", "driver": driver, "util": float(util.gpu),
             "mem_used": mem.used, "mem_total": mem.total, "temp": None, "power": None, "clock": None, "fan": None}
        for key, fn in (("temp", lambda: nv.nvmlDeviceGetTemperature(h, 0)),
                        ("power", lambda: nv.nvmlDeviceGetPowerUsage(h) / 1000.0),
                        ("clock", lambda: nv.nvmlDeviceGetClockInfo(h, 0)),
                        ("fan", lambda: nv.nvmlDeviceGetFanSpeed(h))):
            try:
                g[key] = fn()
            except Exception:
                pass
        gpus.append(g)
    return gpus


def _read_smi():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    r = run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr,driver_version",
             "--format=csv,noheader,nounits"], timeout=5)
    if not r["ok"]:
        return None
    gpus = []
    for line in r["out"].splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 8:
            continue
        num = lambda s: float(s) if s.replace(".", "", 1).isdigit() else None
        gpus.append({"name": p[0], "vendor": "NVIDIA", "util": num(p[1]) or 0,
                     "mem_used": (num(p[2]) or 0) * 1024 ** 2, "mem_total": (num(p[3]) or 0) * 1024 ** 2,
                     "temp": num(p[4]), "power": num(p[5]), "clock": num(p[6]), "driver": p[7], "fan": None})
    return gpus


def _read_windows_generic():
    """Utilisation 3D via compteurs de performance (fonctionne pour AMD/Intel/NVIDIA), rafraîchie toutes les 5 s."""
    if time.time() - _generic_cache["t"] < 5:
        return _generic_cache["data"]
    _generic_cache["t"] = time.time()
    try:
        r = ps("$n=(Get-CimInstance Win32_VideoController | Select -Expand Name) -join '|';"
               "$u=(Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage' -EA SilentlyContinue).CounterSamples |"
               " Measure-Object CookedValue -Sum; Write-Output \"$n;$([math]::Round($u.Sum,1))\"", timeout=15)
        names, _, util = r["out"].rpartition(";")
        util = float(util.replace(",", ".")) if util else 0.0
        names = [n for n in names.split("|") if n]
        main = next((n for n in names if not any(k in n for k in ("Intel", "Microsoft"))), names[0] if names else "GPU")
        _generic_cache["data"] = [{"name": main, "vendor": main.split()[0], "util": min(util, 100.0), "mem_used": None,
                                   "mem_total": None, "temp": None, "power": None, "clock": None, "driver": None, "fan": None}]
    except Exception:
        _generic_cache["data"] = []
    return _generic_cache["data"]


def read():
    nv = _init_nvml()
    if nv:
        try:
            return _read_nvml(nv)
        except Exception:
            pass
    smi = _read_smi()
    if smi:
        return smi
    if IS_WINDOWS:
        return _read_windows_generic()
    return []
