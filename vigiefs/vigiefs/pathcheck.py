"""Chemins et nommage : longueur (MAX_PATH, OneDrive/SharePoint), noms invalides, profondeur, structure.

Références :
- Windows (API Win32) : MAX_PATH = 260 caractères, lecteur et caractère nul compris ; l'Explorateur et
  Office restent limités même avec LongPathsEnabled.
- OneDrive / SharePoint : chemin décodé ≤ 400 caractères, nom ≤ 255 ; caractères " * : < > ? / \\ |
  interdits ; espaces en début/fin interdits ; noms réservés (.lock, CON, PRN, AUX, NUL, COM0-9, LPT0-9,
  _vti_, desktop.ini) et préfixe ~$ bloqués.
"""

import heapq
import re
from collections import defaultdict

INVALID_CHARS = set('"*:<>?|\\') | {chr(i) for i in range(32)}
RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
CLOUD_BLOCKED = re.compile(r"^(\.lock|desktop\.ini)$|_vti_", re.I)

ISSUE_LABEL = {
    "invalid_char": "Caractère interdit (\" * : < > ? \\ |)",
    "edge_space": "Espace en début ou fin de nom",
    "trailing_dot": "Point final dans le nom",
    "reserved": "Nom réservé Windows (CON, PRN, AUX, NUL, COM1…)",
    "cloud_blocked": "Nom bloqué par OneDrive / SharePoint",
    "name_too_long": "Nom de plus de 255 caractères",
    "case_conflict": "Noms identiques à la casse près",
    "office_lock": "Préfixe ~$ (bloqué par SharePoint)",
}

DEFAULTS = {"warn_len": 240, "crit_len": 260, "cloud_len": 400, "check_cloud": True, "name_max": 255,
            "max_depth": 8, "crowded_items": 2000, "arch_depth": 4}


def name_issues(name, check_cloud=True, name_max=255):
    out = []
    if any(c in INVALID_CHARS for c in name):
        out.append("invalid_char")
    if name != name.strip(" "):
        out.append("edge_space")
    if name.endswith(".") and name not in (".", ".."):
        out.append("trailing_dot")
    if RESERVED.match(name):
        out.append("reserved")
    if len(name) > name_max:
        out.append("name_too_long")
    if check_cloud:
        if CLOUD_BLOCKED.search(name):
            out.append("cloud_blocked")
        if name.startswith("~$"):
            out.append("office_lock")
    return out


def user_path_length(prefix, rel):
    """Longueur du chemin vu par l'utilisateur (préfixe d'accès + chemin relatif à la Windows)."""
    if not rel:
        return len(prefix)
    return len(prefix.rstrip("\\/")) + 1 + len(rel)


class PathAnalyzer:
    """Accumule, pendant le parcours, les statistiques de chemins, de nommage et de structure."""

    def __init__(self, prefix, rules=None):
        self.r = {**DEFAULTS, **(rules or {})}
        self.prefix = prefix
        self.over = {"warn": 0, "crit": 0, "cloud": 0}
        self.longest = []
        self.issues = defaultdict(int)
        self.issue_samples = []
        self.depth_files = defaultdict(int)
        self.max_depth = 0
        self.deep_items = 0
        self.crowded = []
        self.root_files = 0
        self.stats = {}

    # ------------------------------------------------------------ alimentation
    def _length(self, rel, kind):
        n = user_path_length(self.prefix, rel)
        if n > self.r["warn_len"]:
            self.over["warn"] += 1
        if n > self.r["crit_len"]:
            self.over["crit"] += 1
        if self.r["check_cloud"] and n > self.r["cloud_len"]:
            self.over["cloud"] += 1
        if n > self.r["warn_len"]:
            item = (n, rel, kind)
            if len(self.longest) < 300:
                heapq.heappush(self.longest, item)
            elif n > self.longest[0][0]:
                heapq.heapreplace(self.longest, item)
        return n

    def _name(self, rel, name, kind):
        for code in name_issues(name, self.r["check_cloud"], self.r["name_max"]):
            if code == "office_lock":
                continue  # déjà traité comme fichier inutile
            self.issues[code] += 1
            if len(self.issue_samples) < 500:
                self.issue_samples.append({"path": rel, "code": code, "kind": kind})

    def _stat(self, rel_dir):
        st = self.stats.get(rel_dir)
        if st is None:
            st = self.stats[rel_dir] = {"size": 0, "files": 0, "subdirs": 0, "direct_items": 0, "max_path_len": 0,
                                        "newest_mtime": None}
        return st

    def directory(self, rel):
        depth = rel.count("/") + 1 if rel else 0
        n = self._length(rel, "dossier") if rel else len(self.prefix)
        if rel:
            self._name(rel, rel.rsplit("/", 1)[-1], "dossier")
            if depth > self.r["max_depth"]:
                self.deep_items += 1
        self.max_depth = max(self.max_depth, depth)
        if depth <= self.r["arch_depth"]:
            st = self._stat(rel)
            st["max_path_len"] = max(st["max_path_len"], n)
            if rel:
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                self._stat(parent)["subdirs"] += 1
                self._bubble(rel, n, None, 0, count_file=False)

    def listing(self, rel, names):
        """Contenu direct d'un dossier : encombrement et conflits de casse."""
        depth = rel.count("/") + 1 if rel else 0
        if len(names) > self.r["crowded_items"]:
            self.crowded.append({"path": rel, "items": len(names)})
        if depth <= self.r["arch_depth"]:
            self._stat(rel)["direct_items"] = len(names)
        seen = {}
        for nm in names:
            low = nm.lower()
            if low in seen:
                self.issues["case_conflict"] += 1
                if len(self.issue_samples) < 500:
                    self.issue_samples.append({"path": (rel + "/" if rel else "") + nm, "code": "case_conflict",
                                               "kind": "élément"})
            seen[low] = nm

    def file(self, rel, name, size, mtime):
        depth = rel.count("/")
        n = self._length(rel, "fichier")
        self._name(rel, name, "fichier")
        self.depth_files[depth] += 1
        if depth == 0:
            self.root_files += 1
        if depth + 1 > self.r["max_depth"]:
            self.deep_items += 1
        self.max_depth = max(self.max_depth, depth)
        self._bubble(rel, n, mtime, size)

    def _bubble(self, rel, length, mtime, size, count_file=True):
        parts = rel.split("/")[:-1]
        for i in range(0, min(len(parts), self.r["arch_depth"]) + 1):
            st = self._stat("/".join(parts[:i]))
            if count_file:
                st["size"] += size
                st["files"] += 1
                if mtime and (st["newest_mtime"] is None or mtime > st["newest_mtime"]):
                    st["newest_mtime"] = mtime
            st["max_path_len"] = max(st["max_path_len"], length)

    # ------------------------------------------------------------ restitution
    def report(self):
        return {
            "prefix": self.prefix, "rules": self.r, "over_warn": self.over["warn"], "over_crit": self.over["crit"],
            "over_cloud": self.over["cloud"],
            "longest": [{"path": p, "length": n, "kind": k} for n, p, k in sorted(self.longest, reverse=True)[:200]],
            "naming": {"counts": dict(self.issues), "samples": self.issue_samples},
            "max_depth": self.max_depth, "deep_items": self.deep_items,
            "depth_files": {str(k): v for k, v in sorted(self.depth_files.items())},
            "crowded": sorted(self.crowded, key=lambda c: -c["items"])[:50], "root_files": self.root_files,
        }

    def folder_stats(self):
        return [{"rel_path": p, "depth": p.count("/") + 1 if p else 0, **st} for p, st in self.stats.items()]
