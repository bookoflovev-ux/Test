"""Helpers bas niveau : détection OS/admin, exécution de commandes et PowerShell."""
import ctypes
import json
import os
import subprocess

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000


class NotSupported(Exception):
    """Action disponible uniquement sous Windows (ou nécessitant l'admin)."""


def require_windows():
    if not IS_WINDOWS:
        raise NotSupported("Action disponible uniquement sous Windows.")


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def run(cmd, timeout=120, shell=False) -> dict:
    """Exécute une commande et renvoie {ok, code, out, err} sans jamais lever."""
    kwargs = {"capture_output": True, "timeout": timeout, "shell": shell}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, **kwargs)
        enc = "cp850" if IS_WINDOWS else "utf-8"
        out = p.stdout.decode(enc, errors="replace") if p.stdout else ""
        err = p.stderr.decode(enc, errors="replace") if p.stderr else ""
        return {"ok": p.returncode == 0, "code": p.returncode, "out": out.strip(), "err": err.strip()}
    except FileNotFoundError:
        return {"ok": False, "code": -1, "out": "", "err": f"Commande introuvable : {cmd[0] if isinstance(cmd, list) else cmd}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -2, "out": "", "err": f"Délai dépassé ({timeout}s)"}


def ps(script: str, timeout=180) -> dict:
    """Exécute un script PowerShell (sortie forcée en UTF-8)."""
    require_windows()
    full = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue';" + script
    kwargs = {"capture_output": True, "timeout": timeout, "creationflags": CREATE_NO_WINDOW}
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full], **kwargs
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -2, "out": "", "err": f"Délai dépassé ({timeout}s)"}
    out = p.stdout.decode("utf-8", errors="replace").strip()
    err = p.stderr.decode("utf-8", errors="replace").strip()
    return {"ok": p.returncode == 0, "code": p.returncode, "out": out, "err": err}


def ps_json(script: str, timeout=180):
    """Exécute un script PowerShell dont la sortie est du JSON ; renvoie toujours une liste ou un dict."""
    r = ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout=timeout)
    if not r["out"]:
        if not r["ok"]:
            raise RuntimeError(r["err"] or "Erreur PowerShell")
        return []
    try:
        return json.loads(r["out"])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Réponse PowerShell illisible : {r['out'][:200]}") from e


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def human_size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "o" else f"{int(n)} o"
        n /= 1024
    return f"{n:.1f} Po"


def env_path(*parts) -> str:
    """Construit un chemin en développant les variables d'environnement (%TEMP%, ~...)."""
    return os.path.expandvars(os.path.expanduser(os.path.join(*parts)))
