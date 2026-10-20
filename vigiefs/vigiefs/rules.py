"""Règles métier : filtrage de fichiers, détection rançongiciel, quotas, prévisions."""

import fnmatch
import os
import time

DOS_EPOCH = 315532800          # 01/01/1980 : en deçà, une date de fichier est manifestement fausse


def safe_localtime(ts):
    """time.localtime() sans exception : None pour une date hors plage (sous Windows, toute date antérieure à
    1970 ou postérieure à l'an 3000 lève « [Errno 22] Invalid argument », fréquent après une copie ratée)."""
    try:
        return time.localtime(ts)
    except (OSError, OverflowError, ValueError, TypeError):
        return None


def plausible_mtime(ts, now=None):
    """Date de fichier vraisemblable (entre 1980 et demain) : les autres ne servent à aucune décision."""
    return ts is not None and DOS_EPOCH <= ts <= (now or time.time()) + 2 * 86400


# Fichiers de machines virtuelles (Hyper-V, VMware) : jamais archivés, nettoyés, dédoublonnés ni déplacés.
# Un disque virtuel déplacé, remplacé ou supprimé casse la VM (sa configuration pointe vers ce chemin exact).
VM_FILE_EXT = (".vhd", ".vhdx", ".avhd", ".avhdx", ".vhds", ".vmcx", ".vmrs", ".vmgs", ".vsv", ".vmdk", ".vmx", ".vmxf",
               ".vmsd", ".vmsn", ".vmss", ".vmem", ".nvram", ".hrl", ".rct", ".mrt")


def is_vm_file(name):
    return str(name).lower().endswith(VM_FILE_EXT)


def contains_vm_files(path, limit=20000):
    """Un dossier contient-il des fichiers de machine virtuelle ? (parcours borné à `limit` éléments)"""
    if is_vm_file(path):
        return True
    seen = 0
    for _dirpath, _dirs, files in os.walk(path):
        for f in files:
            if is_vm_file(f):
                return True
        seen += len(files) + len(_dirs)
        if seen > limit:
            return False
    return False


def fmt_day(ts, default="date inconnue"):
    lt = safe_localtime(ts)
    return time.strftime("%d/%m/%Y", lt) if lt else default


def normalize_extensions(raw):
    """Accepte une liste ou une chaîne « .exe, .bat ; msi » et renvoie ['.exe', '.bat', '.msi']."""
    if isinstance(raw, str):
        raw = raw.replace(";", ",").replace("\n", ",").split(",")
    out = []
    for ext in raw or []:
        ext = ext.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in out:
            out.append(ext)
    return out


class Matcher:
    """Test rapide d'un nom de fichier contre une liste d'extensions et de motifs."""

    def __init__(self, extensions=(), patterns=()):
        self.extensions = tuple(e.lower() for e in normalize_extensions(list(extensions)))
        self.patterns = tuple(p.lower() for p in patterns if p)

    def match(self, filename):
        name = filename.lower()
        if self.extensions and name.endswith(self.extensions):
            return True
        return any(fnmatch.fnmatchcase(name, p) for p in self.patterns)

    def __bool__(self):
        return bool(self.extensions or self.patterns)


def share_screening_matcher(share, screening_settings):
    if not share.get("screening_enabled"):
        return Matcher()
    exts = normalize_extensions(share.get("blocked_extensions") or "")
    if not exts:
        exts = normalize_extensions(screening_settings.get("default_blocked_extensions", []))
    return Matcher(exts)


def ransomware_matchers(rs_settings):
    if not rs_settings.get("enabled", True):
        return Matcher(), Matcher()
    return (
        Matcher(rs_settings.get("extensions", [])),
        Matcher(patterns=rs_settings.get("note_patterns", [])),
    )


def quota_state(share, size):
    """Renvoie (niveau, pourcentage) ; niveau ∈ {None, 'warning', 'critical'}."""
    quota = share.get("quota_bytes") or 0
    if quota <= 0 or size is None:
        return None, None
    pct = size * 100.0 / quota
    if pct >= (share.get("quota_crit_pct") or 95):
        return "critical", pct
    if pct >= (share.get("quota_warn_pct") or 80):
        return "warning", pct
    return None, pct


def growth_per_day(points):
    """Régression linéaire sur [(timestamp, taille)] → octets/jour (ou None)."""
    pts = [(t, s) for t, s in points if s is not None]
    if len(pts) < 2:
        return None
    span = pts[-1][0] - pts[0][0]
    if span < 3600:
        return None
    n = len(pts)
    mean_t = sum(t for t, _ in pts) / n
    mean_s = sum(s for _, s in pts) / n
    num = sum((t - mean_t) * (s - mean_s) for t, s in pts)
    den = sum((t - mean_t) ** 2 for t, _ in pts)
    if den == 0:
        return None
    return num / den * 86400


def days_until_full(share, size, slope_per_day):
    quota = share.get("quota_bytes") or 0
    if quota <= 0 or not slope_per_day or slope_per_day <= 0 or size is None:
        return None
    remaining = quota - size
    if remaining <= 0:
        return 0.0
    return remaining / slope_per_day


def safe_join(root, relative):
    """Résout `relative` sous `root` et refuse toute sortie de l'arborescence."""
    root_real = os.path.realpath(root)
    rel = (relative or "").replace("\\", "/").lstrip("/")
    target = os.path.realpath(os.path.join(root_real, rel))
    try:
        if os.path.commonpath([root_real, target]) != root_real:
            raise ValueError("Chemin hors du dossier supervisé")
    except ValueError:
        raise ValueError("Chemin hors du dossier supervisé")
    return target


def long_path(path):
    r"""Windows : chemin étendu (\\?\) pour lire au-delà de 260 caractères, même sans LongPathsEnabled.

    Sans effet sur les autres systèmes. Les chemins affichés et stockés restent relatifs (relpath).
    """
    if os.name != "nt" or not path or path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path


def strip_long(path):
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def relpath(root, path):
    rel = os.path.relpath(strip_long(path), os.path.realpath(strip_long(root)))
    return "" if rel == "." else rel.replace("\\", "/")
