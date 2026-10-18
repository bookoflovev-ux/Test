"""Accès au système hôte : exécution PowerShell / commandes, détection des capacités disponibles."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

IS_WINDOWS = sys.platform.startswith("win")


class CommandError(RuntimeError):
    pass


def run(cmd, timeout=120, input_text=None, check=True):
    """Exécute une commande (liste d'arguments) et renvoie stdout (texte)."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_text,
                             encoding="oem" if IS_WINDOWS else "utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise CommandError(f"Commande introuvable : {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"Délai dépassé : {' '.join(map(str, cmd[:3]))}…") from exc
    if check and res.returncode != 0:
        raise CommandError((res.stderr or res.stdout or f"code {res.returncode}").strip()[:2000])
    return res.stdout


def powershell(script, timeout=600, as_json=True):
    """Exécute un script PowerShell (Windows PowerShell 5.1 ou pwsh) ; renvoie le JSON décodé ou le texte."""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise CommandError("PowerShell indisponible sur ce serveur")
    # Script écrit dans un fichier UTF-8 avec BOM : chemins accentués et longs scripts sans souci d'échappement.
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="vigiefs-")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
        fh.write("$ProgressPreference='SilentlyContinue'\n[Console]::OutputEncoding=[Text.Encoding]::UTF8\n")
        fh.write(script)
    try:
        res = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path],
                             capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CommandError("Délai dépassé pour le script PowerShell") from exc
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    out = res.stdout.decode("utf-8", "replace").strip()
    if res.returncode != 0 and not out:
        raise CommandError(res.stderr.decode("utf-8", "replace").strip()[:2000] or "Échec PowerShell")
    if not as_json:
        return out
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError as exc:
        raise CommandError(f"Réponse PowerShell illisible : {out[:300]}") from exc


def ps_quote(value):
    """Chaîne PowerShell entre apostrophes (les apostrophes sont doublées)."""
    return "'" + str(value).replace("'", "''") + "'"


_CAPS = None


def capabilities(refresh=False):
    """Fonctions du système exploitables par VigieFS (mises en cache)."""
    global _CAPS
    if _CAPS is not None and not refresh:
        return _CAPS
    caps = {"platform": "windows" if IS_WINDOWS else "posix", "powershell": False, "fsrm": False,
            "smb_cmdlets": False, "ad_module": False, "icacls": False, "getfacl": False, "setfacl": False,
            "smbstatus": False, "testparm": False}
    if IS_WINDOWS:
        caps["icacls"] = bool(shutil.which("icacls"))
        try:
            found = powershell(
                "$c=@{}; foreach($n in 'Get-FsrmQuota','Get-SmbShare','Get-ADGroupMember'){"
                "$c[$n]=[bool](Get-Command $n -ErrorAction SilentlyContinue)}; $c | ConvertTo-Json -Compress",
                timeout=60)
            caps["powershell"] = True
            caps["fsrm"] = bool(found.get("Get-FsrmQuota"))
            caps["smb_cmdlets"] = bool(found.get("Get-SmbShare"))
            caps["ad_module"] = bool(found.get("Get-ADGroupMember"))
        except CommandError:
            pass
    else:
        for tool in ("getfacl", "setfacl", "smbstatus", "testparm"):
            caps[tool] = bool(shutil.which(tool))
    _CAPS = caps
    return caps
