"""Lecture des permissions : ACL NTFS (icacls) sous Windows, droits POSIX ailleurs."""

import os
import stat
import subprocess
import sys

ICACLS_RIGHTS = {
    "F": "Contrôle total", "M": "Modification", "RX": "Lecture et exécution",
    "R": "Lecture", "W": "Écriture", "D": "Suppression", "N": "Aucun accès",
}
ICACLS_FLAGS = {
    "OI": "héritage objets", "CI": "héritage conteneurs", "IO": "héritage uniquement",
    "NP": "sans propagation", "I": "hérité",
}


def _parse_icacls(output, path):
    """Analyse la sortie d'icacls (anglais ou français)."""
    entries = []
    for i, line in enumerate(l for l in output.splitlines() if l.strip()):
        low = line.lower()
        if "processed" in low or "traités" in low or "traitement" in low:
            continue
        text = line[len(path):] if i == 0 and line.startswith(path) else line
        principal, sep, rights = text.strip().partition(":(")
        if not sep:
            continue
        tokens = [t.strip("( ") for t in ("(" + rights).split(")") if t.strip("( ")]
        readable = []
        for t in tokens:
            if t in ICACLS_RIGHTS:
                readable.append(ICACLS_RIGHTS[t])
            elif "," in t:
                readable.append("Droits spéciaux (" + t + ")")
        entries.append({
            "principal": principal.strip(),
            "rights": ", ".join(readable) or ", ".join(tokens),
            "raw": "".join(f"({t})" for t in tokens),
            "inherited": "I" in tokens,
            "deny": "DENY" in tokens,
            "flags": [ICACLS_FLAGS[t] for t in tokens if t in ICACLS_FLAGS and t != "I"],
        })
    return entries


def read_permissions(path):
    if sys.platform.startswith("win"):
        try:
            res = subprocess.run(["icacls", path], capture_output=True, text=True, timeout=20,
                                 encoding="oem", errors="replace",  # console Windows = page OEM
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return {"platform": "windows", "error": str(exc), "entries": []}
        if res.returncode != 0:
            return {"platform": "windows", "error": (res.stderr or res.stdout).strip(), "entries": []}
        return {"platform": "windows", "entries": _parse_icacls(res.stdout, path)}

    st = os.stat(path, follow_symlinks=False)
    try:
        import grp
        import pwd
        owner = pwd.getpwuid(st.st_uid).pw_name
        group = grp.getgrgid(st.st_gid).gr_name
    except (ImportError, KeyError):
        owner, group = str(st.st_uid), str(st.st_gid)

    def triplet(r, w, x):
        mode = st.st_mode
        return {"read": bool(mode & r), "write": bool(mode & w), "execute": bool(mode & x)}

    def describe(t):
        parts = [n for n, k in (("lecture", "read"), ("écriture", "write"), ("exécution", "execute")) if t[k]]
        return ", ".join(parts) or "aucun"

    u = triplet(stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR)
    g = triplet(stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP)
    o = triplet(stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH)
    return {
        "platform": "posix",
        "mode": stat.filemode(st.st_mode),
        "owner": owner,
        "group": group,
        "entries": [
            {"principal": f"Propriétaire ({owner})", "rights": describe(u), "inherited": False, "deny": False},
            {"principal": f"Groupe ({group})", "rights": describe(g), "inherited": False, "deny": False},
            {"principal": "Autres", "rights": describe(o), "inherited": False, "deny": False,
             "warning": "Accès en écriture pour tous" if o["write"] else None},
        ],
    }
