"""Réorganisation d'arborescence : architecture optimisée proposée, puis appliquée sans perte de fichier.

Proposition (règles, chacune sélectionnable opération par opération) :
    naming        noms non conformes (caractères interdits, espaces / point final, noms réservés, OneDrive)
    length        chemins trop longs : noms de dossiers (et de fichiers) raccourcis sur les branches en cause
    intermediate  niveau intermédiaire inutile (« Projet/Projet », « Nouveau dossier ») supprimé
    empty         dossiers vides supprimés
    crowded       dossier surchargé : fichiers rangés par année dans des sous-dossiers
    root          fichiers à la racine rangés dans « _A classer »
    cold          dossiers dont le fichier le plus récent est ancien déplacés dans « _Archives »
    template      premier niveau rangé dans un modèle numéroté (01 - Direction, 02 - Finances…)

Les opérations décrivent l'arborescence CIBLE. VigieFS calcule l'emplacement final de chaque dossier pour
l'ensemble des changements retenus, puis applique en deux étapes :
    1. préparer  contrôles puis création de toute l'arborescence cible (aucun fichier n'est déplacé) ;
    2. déplacer  cliché instantané (Windows), sauvegarde des droits, inventaire, renommages de fichiers
                 (atomiques), puis robocopy vers les dossiers finaux SANS écraser (/XC /XN /XO) ;
                 chaque fichier est vérifié (taille, date ; empreinte SHA-256 en option et pour toute
                 fusion avec un fichier déjà présent) et seule une source vérifiée est supprimée.
                 Le reste demeure en place et est listé. Inventaire final comparé à l'initial.
Chaque fichier déplacé est consigné dans un manifeste : le retour arrière le remet exactement en place.
"""

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from collections import Counter, defaultdict

from . import pathcheck, system
from .rules import long_path, strip_long

RULES = {
    "naming": ("Noms non conformes", True),
    "length": ("Chemins trop longs : noms raccourcis", True),
    "intermediate": ("Niveaux intermédiaires inutiles", True),
    "empty": ("Dossiers vides", True),
    "crowded": ("Dossiers surchargés : rangement par année", False),
    "root": ("Fichiers à la racine", False),
    "cold": ("Données froides : vers les archives", False),
    "template": ("Premier niveau : modèle normalisé", False),
}
OP_LABEL = {"rename_dir": "Renommer le dossier", "rename_file": "Renommer le fichier", "move_dir": "Déplacer le dossier",
            "merge_up": "Supprimer le niveau intermédiaire", "move_files": "Ranger des fichiers",
            "remove_empty": "Supprimer le dossier vide"}
DIR_OPS = ("rename_dir", "move_dir", "merge_up")
OPTION_DEFAULTS = {"verify_hash": False, "vss_snapshot": True, "threads": 8, "backup_mode": False}
GENERIC = re.compile(r"^(nouveau dossier|new folder|dossier|folder|sans titre|untitled)( ?\(\d+\))?$", re.I)
IGNORED_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}
TEMPLATE = [
    ("01 - Direction", r"direction|conseil|gouvernance|g[ée]rance|associ[ée]s|\bca\b|\bag\b|juridique"),
    ("02 - Finances", r"compta|financ|factur|banque|budget|tr[ée]sor|fiscal|bilan"),
    ("03 - Ressources humaines", r"\brh\b|ressources humaines|personnel|paie|salari|recrut|formation"),
    ("04 - Commercial", r"commercial|vente|devis|marketing|communication|prospect"),
    ("05 - Clients et dossiers", r"client|dossier|affaire|contentieux|mandat"),
    ("06 - Projets", r"projet|chantier|[ée]tude"),
    ("07 - Exploitation et technique", r"technique|informatique|\bit\b|exploitation|production|logistique|maintenance|planning"),
    ("08 - Achats et fournisseurs", r"achat|fournisseur|commande"),
    ("09 - Qualité et contrats", r"qualit|conformit|contrat|assurance|r[ée]glement"),
    ("90 - Echanges", r"partage|[ée]change|transfert|\btemp|scan|commun"),
    ("99 - Archives", r"archive|ancien|\bold\b|^(19|20)\d\d$"),
]
STOP_WORDS = {"de", "du", "des", "la", "le", "les", "et", "à", "a", "au", "aux", "en", "pour", "par", "sur", "avec",
              "un", "une", "l", "d", "dans"}
ABBREV = [(r"ressources humaines", "RH"), (r"assembl[ée]e g[ée]n[ée]rale", "AG"), (r"conseil d.administration", "CA"),
          (r"comptabilit[ée]", "Compta"), (r"administratif", "Admin"), (r"administration", "Admin"),
          (r"proc[ée]dure", "Proc"), (r"documents?", "Docs"), (r"dossiers?", "Doss"), (r"correspondances?", "Corresp"),
          (r"informatique", "Info"), (r"fournisseurs?", "Fourn"), (r"facturation", "Fact"), (r"factures?", "Fact"),
          (r"communications?", "Com"), (r"commercial", "Com"), (r"attestations?", "Attest"), (r"conventions?", "Conv"),
          (r"communiqu[ée]es? par la partie adverse", "adverses"), (r"de la partie adverse", "adverses"),
          (r"communiqu[ée]es?", "comm"), (r"partie adverse", "adverse"), (r"gestionnaire", "gest"),
          (r"juridiques?", "Jur"), (r"exploitation", "Exploit"), (r"g[ée]n[ée]rales?", "gen")]


# ================================================================ noms
def sanitize(name, is_dir):
    """Nom conforme Windows / OneDrive le plus proche possible de l'original."""
    new = name.replace(":", " -").replace("/", "-").replace("\\", "-")
    new = re.sub(r'[*?"<>|]', "", new)
    new = "".join(c for c in new if ord(c) >= 32)
    new = re.sub(r"\s{2,}", " ", new).strip(" ").rstrip(". ")
    new = new.replace("_vti_", "_vti-")
    stem, ext = (new, "") if is_dir else os.path.splitext(new)
    if pathcheck.RESERVED.match(new) or new.lower() == ".lock":
        stem = stem + "_"
    new = (stem + ext) or "_"
    if len(new) > 255:
        stem, ext = (new, "") if is_dir else os.path.splitext(new)
        new = stem[:255 - len(ext)].rstrip(" .") + ext
    return new


def shorten(name, limit, is_dir=True):
    """Nom raccourci lisible : abréviations courantes, mots vides retirés, coupe sur un mot."""
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    limit = max(8, limit - len(ext))
    if len(stem) <= limit:
        return name
    s = stem
    for pat, rep in ABBREV:
        if len(s) <= limit:
            break
        s = re.sub(rf"\b{pat}\b", rep, s, flags=re.I)
    if len(s) > limit:
        words = re.split(r"(\s+|['’])", s)
        kept = [w for w in words if w.strip().lower() not in STOP_WORDS or not w.strip()]
        s = re.sub(r"\s{2,}", " ", "".join(kept).replace("'", " ").replace("’", " ")).strip()
    if len(s) > limit:
        cut = s[:limit + 1].rsplit(" ", 1)[0]
        s = cut if len(cut) >= limit // 2 else s[:limit]
    return s.strip(" .-_") + ext


def _unique(name, taken):
    if name.lower() not in taken:
        return name
    stem, ext = os.path.splitext(name) if "." in name[1:] else (name, "")
    n = 2
    while f"{stem} ({n}){ext}".lower() in taken:
        n += 1
    return f"{stem} ({n}){ext}"


def _join(a, b):
    return f"{a}/{b}" if a and b else (a or b)


def _parent(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _base(rel):
    return rel.rsplit("/", 1)[-1]


def _depth(rel):
    return rel.count("/") + 1 if rel else 0


# ================================================================ inventaire
def walk(root, skip_roots=(), cancel=None, keep_files_over=2000):
    """Inventaire des dossiers (agrégats, longueurs de noms, années, noms non conformes)."""
    root_io = long_path(os.path.realpath(root))
    skip = {os.path.normcase(os.path.realpath(r)) for r in skip_roots if r}
    dirs = {}
    stack = [(root_io, "", None)]
    now = time.time()
    while stack:
        if cancel is not None and cancel():
            raise InterruptedError()
        cur, rel, dmtime = stack.pop()
        node = {"files": 0, "size": 0, "newest": 0.0, "hist": Counter(), "years": Counter(), "subdirs": [],
                "issues": [], "mtime": dmtime or now, "file_list": []}
        dirs[rel] = node
        try:
            entries = list(os.scandir(cur))
        except OSError:
            node["error"] = True
            continue
        lower = Counter(e.name.lower() for e in entries)
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            issues = pathcheck.name_issues(e.name)
            if lower[e.name.lower()] > 1:
                issues.append("case_conflict")
            if is_dir:
                if os.path.normcase(strip_long(e.path)) in skip:
                    continue
                node["subdirs"].append(e.name)
                stack.append((e.path, _join(rel, e.name), st.st_mtime))
            else:
                node["files"] += 1
                node["size"] += st.st_size
                node["newest"] = max(node["newest"], st.st_mtime)
                node["hist"][len(e.name)] += 1
                node["years"][time.localtime(st.st_mtime).tm_year] += 1
                node["file_list"].append((e.name, st.st_size, st.st_mtime))
            if issues and not (e.name.lower() in IGNORED_FILES or e.name.startswith("~$")):
                node["issues"].append((e.name, is_dir, issues))
        if node["issues"]:
            node["names"] = set(lower)       # unicité des noms corrigés (dossiers concernés seulement)
        if rel and node["files"] <= keep_files_over:
            node["file_list"] = []          # listes conservées : racine et dossiers surchargés seulement
    for rel in sorted(dirs, key=_depth, reverse=True):
        n = dirs[rel]
        files, size, newest = n["files"], n["size"], n["newest"]
        maxlen = max(n["hist"]) if n["hist"] else 0
        for s in n["subdirs"]:
            c = dirs.get(_join(rel, s))
            if c is None:
                continue
            files += c["t_files"]
            size += c["t_size"]
            newest = max(newest, c["t_newest"])
            maxlen = max(maxlen, len(s) + (1 + c["t_maxlen"] if c["t_maxlen"] else 0))
        n.update(t_files=files, t_size=size, t_newest=newest, t_maxlen=maxlen)
    return dirs


def save_index(path, dirs):
    data = {rel: [n["files"], n["size"], dict(n["hist"]), n["t_files"], n["t_size"]] for rel, n in dirs.items()}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)


def load_index(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {rel: {"files": v[0], "size": v[1], "hist": {int(k): c for k, c in v[2].items()}, "t_files": v[3],
                  "t_size": v[4]} for rel, v in raw.items()}


# ================================================================ proposition
def propose(share, settings, path_rules, skip_roots=(), cancel=None, now=None):
    """Plan d'architecture optimisée (opérations sélectionnables) et index des dossiers."""
    now = now or time.time()
    s = {**settings}
    pr = {**pathcheck.DEFAULTS, **(path_rules or {})}
    prefix = share.get("access_path") or share["path"]
    dirs = walk(share["path"], skip_roots, cancel, keep_files_over=int(pr["crowded_items"]))
    ops = []
    dir_rename = {}
    claimed = set()                               # dossiers déjà pris par une opération exclusive

    def add(rule, typ, src, **kw):
        op = {"id": f"op{len(ops) + 1}", "rule": rule, "type": typ, "src": src, "selected": RULES[rule][1], **kw}
        ops.append(op)
        return op

    def plen(rel, tail_len):
        return pathcheck.user_path_length(prefix, rel) + (1 + tail_len if tail_len else 0)

    # Dossiers vides (sous-arborescence sans aucun fichier) : seul le plus haut est proposé.
    empty_roots = []
    for rel in sorted(dirs, key=_depth):
        n = dirs[rel]
        if rel and n["t_files"] == 0 and not n.get("error") and not any(rel.startswith(e + "/") for e in empty_roots):
            empty_roots.append(rel)
    for rel in empty_roots:
        add("empty", "remove_empty", rel, reason="Dossier vide (aucun fichier, sous-dossiers compris)",
            files=0, size=0)
        claimed.add(rel)

    # Niveaux intermédiaires : « X/X » ou « X/Nouveau dossier » ne contenant que ce sous-dossier.
    for rel in sorted(dirs, key=_depth, reverse=True):
        n = dirs[rel]
        if not rel or n["files"] or len(n["subdirs"]) != 1 or rel in claimed:
            continue
        child = _join(rel, n["subdirs"][0])
        c = dirs.get(child)
        if c is None or child in claimed or any(x.lower() == n["subdirs"][0].lower() for x in c["subdirs"]):
            continue
        same = n["subdirs"][0].strip().lower() == _base(rel).strip().lower()
        if same or GENERIC.match(n["subdirs"][0]):
            add("intermediate", "merge_up", child, files=c["t_files"], size=c["t_size"],
                reason=f"« {n['subdirs'][0]} » est le seul contenu de « {_base(rel)} » : son contenu remonte d'un niveau")
            claimed.add(child)

    # Noms non conformes, puis noms raccourcis sur les branches trop longues.
    crit = int(pr["crit_len"])
    long_name, short_name = int(s.get("long_name", 40)), int(s.get("short_name", 30))
    for rel in sorted(dirs, key=_depth, reverse=True):
        n = dirs[rel]
        taken = set(n.get("names") or ()) | {x.lower() for x in n["subdirs"]} | {f[0].lower() for f in n["file_list"]}
        fixes = {}
        for name, is_dir, issues in n["issues"]:
            new = sanitize(name, is_dir)
            if new != name or "case_conflict" in issues:
                fixes[(name, is_dir)] = (new, [pathcheck.ISSUE_LABEL.get(i, i) for i in issues])
        for sub in n["subdirs"]:
            c = dirs.get(_join(rel, sub))
            if c is None:
                continue
            cur = fixes.get((sub, True), (sub, []))[0]
            if len(cur) > long_name and plen(_join(rel, cur), c["t_maxlen"]) > crit:
                new = shorten(cur, short_name)
                if new != cur:
                    reasons = fixes.get((sub, True), (sub, []))[1] + [
                        f"branche de {plen(_join(rel, sub), c['t_maxlen'])} caractères (limite {crit})"]
                    fixes[(sub, True)] = (new, reasons)
        case_seen = set()
        for (name, is_dir), (new, reasons) in sorted(fixes.items()):
            child = _join(rel, name)
            if is_dir and child in claimed:
                continue
            if new == name:                       # conflit de casse : le premier garde son nom
                if name.lower() not in case_seen:
                    case_seen.add(name.lower())
                    continue
            if new.lower() != name.lower() or new == name:
                new = _unique(new, taken)
            taken.add(new.lower())
            if new == name:
                continue
            rule = "naming" if any("branche" not in r for r in reasons) else "length"
            if is_dir:
                c = dirs.get(child, {})
                dir_rename[child] = new
                add(rule, "rename_dir", child, name=new, reason=" ; ".join(reasons),
                    files=c.get("t_files", 0), size=c.get("t_size", 0))
            else:
                add(rule, "rename_file", child, name=new, reason=" ; ".join(reasons), files=1, size=0)

    # Fichiers trop longs eux-mêmes : nom de fichier raccourci si la branche reste trop longue.
    for rel, n in dirs.items():
        if not n["hist"] or not n["file_list"]:
            continue
        for name, size, _mt in n["file_list"]:
            if len(name) > long_name and plen(_join(rel, name), 0) > crit:
                new = shorten(name, short_name, is_dir=False)
                if new != name:
                    add("length", "rename_file", _join(rel, name), name=new, files=1, size=size,
                        reason=f"chemin de {plen(_join(rel, name), 0)} caractères (limite {crit})")

    # Dossiers surchargés : rangement par année de modification.
    for rel, n in dirs.items():
        if not rel or n["files"] <= int(pr["crowded_items"]) or len(n["years"]) < 2:
            continue
        by_year = defaultdict(list)
        for name, size, mt in n["file_list"]:
            by_year[time.localtime(mt).tm_year].append((name, size))
        for year, items in sorted(by_year.items()):
            add("crowded", "move_files", rel, dest_sub=str(year), files_list=sorted(x[0] for x in items),
                files=len(items), size=sum(x[1] for x in items),
                reason=f"{n['files']} fichiers directement dans ce dossier : rangement des fichiers de {year}")

    # Fichiers à la racine.
    root = dirs.get("", {})
    root_files = [f for f in root.get("file_list", []) if f[0].lower() not in IGNORED_FILES and not f[0].startswith("~$")]
    unsorted = s.get("unsorted_folder") or "_A classer"
    if root_files:
        add("root", "move_files", "", dest_dir=unsorted, files_list=sorted(f[0] for f in root_files),
            files=len(root_files), size=sum(f[1] for f in root_files),
            reason=f"{len(root_files)} fichier(s) déposé(s) à la racine du partage")

    # Données froides : dossiers de 1er ou 2e niveau sans modification depuis N ans.
    archive = s.get("archive_folder") or "_Archives"
    limit = now - float(s.get("cold_years", 3)) * 365.25 * 86400
    cold_roots = []
    for rel in sorted(dirs, key=_depth):
        n = dirs[rel]
        if not rel or _depth(rel) > 2 or rel in claimed or n["t_files"] == 0 or not n["t_newest"]:
            continue
        top = rel.split("/")[0]
        if top.lower() == archive.lower() or any(rel.startswith(c + "/") for c in cold_roots):
            continue
        if n["t_newest"] < limit:
            cold_roots.append(rel)
            add("cold", "move_dir", rel, dest="{archive}/{parent}", files=n["t_files"], size=n["t_size"],
                reason="aucune modification depuis le " + time.strftime("%d/%m/%Y", time.localtime(n["t_newest"])))

    # Modèle de premier niveau : le dossier qui porte déjà le nom de la catégorie devient la catégorie,
    # les autres y sont rangés.
    def plain(text):
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()

    renamed_to = {}
    matches = []
    for sub in sorted(root.get("subdirs", [])):
        if sub.lower() == archive.lower() or sub in claimed or sub in cold_roots or re.match(r"^\d\d - ", sub):
            continue
        for cat, pattern in TEMPLATE:
            if re.search(pattern, sub, re.I):
                matches.append((sub, cat))
                if plain(sub) == plain(cat.split(" - ", 1)[1]) and cat not in renamed_to:
                    renamed_to[cat] = sub
                break
    for sub, cat in matches:
        c = dirs.get(sub, {})
        if renamed_to.get(cat) == sub:
            add("template", "rename_dir", sub, name=cat, files=c.get("t_files", 0), size=c.get("t_size", 0),
                reason=f"devient « {cat} »")
        else:
            add("template", "move_dir", sub, dest=cat, files=c.get("t_files", 0), size=c.get("t_size", 0),
                reason=f"rangé dans « {cat} »")

    plan = {"version": 1, "share_id": share["id"], "root": share["path"], "prefix": prefix,
            "generated_at": now, "settings": s, "path_rules": pr, "ops": ops, "options": dict(OPTION_DEFAULTS),
            "archive_folder": archive}
    plan["options"].update(verify_hash=bool(s.get("verify_hash")), vss_snapshot=bool(s.get("vss_snapshot", True)),
                           threads=int(s.get("robocopy_threads", 8)))
    return plan, dirs


# ================================================================ emplacements finaux
class Mapping:
    """Emplacement final de chaque dossier et fichier pour les opérations retenues."""

    def __init__(self, plan):
        self.plan = plan
        sel = [o for o in plan["ops"] if o.get("selected")]
        self.rename = {o["src"]: o["name"] for o in sel if o["type"] == "rename_dir"}
        self.move = {o["src"]: o["dest"] for o in sel if o["type"] == "move_dir"}
        self.merge = {o["src"] for o in sel if o["type"] == "merge_up"}
        self.remove = {o["src"] for o in sel if o["type"] == "remove_empty"}
        self.rename_file = {o["src"]: o["name"] for o in sel if o["type"] == "rename_file"}
        self.move_files = {}
        for o in sel:
            if o["type"] == "move_files":
                for f in o["files_list"]:
                    self.move_files[_join(o["src"], f)] = o
        self.archive = plan.get("archive_folder") or "_Archives"
        self._memo = {"": ""}

    def dir(self, rel):
        if rel in self._memo:
            return self._memo[rel]
        parent, name = _parent(rel), _base(rel)
        fp = self.dir(parent)
        if rel in self.merge:
            out = fp
        else:
            base = fp
            if rel in self.move:
                dest = self.move[rel]
                base = dest.replace("{archive}", self.archive).replace("{parent}", fp).rstrip("/")
                base = re.sub(r"/{2,}", "/", base)
            out = _join(base, self.rename.get(rel, name))
        self._memo[rel] = out
        return out

    def file(self, rel):
        parent, name = _parent(rel), _base(rel)
        name_new = self.rename_file.get(rel, name)
        mv = self.move_files.get(rel)
        if mv:
            folder = mv["dest_dir"] if mv.get("dest_dir") else _join(self.dir(parent), mv["dest_sub"])
        else:
            folder = self.dir(parent)
        return _join(folder, name_new)

    def roots(self):
        """Dossiers dont l'emplacement change (racines des régions à déplacer)."""
        return sorted(set(self.rename) | set(self.move) | set(self.merge), key=_depth)


def fingerprint(plan):
    sel = [(o["id"], o["type"], o["src"], o.get("name"), o.get("dest"), o.get("dest_sub"), o.get("dest_dir"),
            tuple(o.get("files_list") or ())) for o in plan["ops"] if o.get("selected")]
    return hashlib.sha256(json.dumps(sel, ensure_ascii=False).encode()).hexdigest()


def metrics(plan, index):
    """Indicateurs avant / après la sélection (chemins trop longs, profondeur, racine, nombre de dossiers)."""
    crit = int(plan["path_rules"]["crit_len"])
    prefix = plan["prefix"]
    m = Mapping(plan)

    def ulen(folder, name_len):
        return pathcheck.user_path_length(prefix, folder) + 1 + name_len if folder else \
            len(prefix.rstrip("\\/")) + 1 + name_len

    def removed(rel):
        return any(rel == r or rel.startswith(r + "/") for r in m.remove)

    before = {"long_paths": 0, "max_depth": 0, "root_files": index.get("", {}).get("files", 0), "dirs": len(index)}
    after = dict(before, long_paths=0, max_depth=0)
    for rel, n in index.items():
        new = m.dir(rel)
        before["max_depth"] = max(before["max_depth"], _depth(rel))
        if removed(rel):
            after["dirs"] -= 1
            continue
        after["max_depth"] = max(after["max_depth"], _depth(new))
        for length, count in n["hist"].items():
            before["long_paths"] += count if ulen(rel, length) > crit else 0
            after["long_paths"] += count if ulen(new, length) > crit else 0
    for rel in set(m.rename_file) | set(m.move_files):
        counted = ulen(m.dir(_parent(rel)), len(_base(rel))) > crit
        final = m.file(rel)
        after["long_paths"] += int(ulen(_parent(final), len(_base(final))) > crit) - int(counted)
        if not _parent(rel) and rel in m.move_files:
            after["root_files"] -= 1
    after["dirs"] -= len(m.merge)
    after["long_paths"] = max(0, after["long_paths"])
    return {"before": before, "after": after}


def validate(plan, index=None):
    """Avertissements (warning) et blocages (error) sur la sélection, avant toute action."""
    m = Mapping(plan)
    problems = []
    sel = [o for o in plan["ops"] if o.get("selected")]
    exclusive = {o["src"] for o in sel if o["type"] in ("merge_up", "remove_empty")}
    moves = Counter(o["src"] for o in sel if o["type"] == "move_dir")
    for o in sel:
        if o["type"] in ("rename_dir", "move_dir") and o["src"] in exclusive:
            problems.append({"op": o["id"], "level": "warning",
                             "message": f"« {o['src']} » est aussi supprimé ou fusionné : cette opération sera ignorée."})
        if o["type"] == "move_dir" and moves[o["src"]] > 1:
            problems.append({"op": o["id"], "level": "error",
                             "message": f"Plusieurs déplacements retenus pour « {o['src']} » : n'en gardez qu'un."})
        if o["type"] in ("rename_dir", "rename_file") and (pathcheck.name_issues(o["name"]) or not o["name"].strip()):
            problems.append({"op": o["id"], "level": "error", "message": f"Nom cible non conforme : « {o['name']} »"})
    roots = m.roots()
    for r in roots:
        target = m.dir(r)
        for x in roots:
            if x != r and (target == x or target.startswith(x + "/")):
                problems.append({"op": None, "level": "error",
                                 "message": f"La destination de « {r} » ({target}) est un dossier lui-même déplacé "
                                            f"(« {x} ») : décochez l'une des deux opérations."})
        if r not in m.merge and (target == r or target.startswith(r + "/")):
            problems.append({"op": None, "level": "error", "message": f"Destination « {target} » à l'intérieur de la source"})
    if index:
        finals = defaultdict(list)
        for rel in index:
            if rel and rel not in m.merge and not any(rel == r or rel.startswith(r + "/") for r in m.remove):
                finals[m.dir(rel).lower()].append(rel)
        for sources in finals.values():
            if len(sources) > 1:
                problems.append({"op": None, "level": "warning",
                                 "message": f"Fusion de {len(sources)} dossiers dans « {m.dir(sources[0])} » : les "
                                            "fichiers en conflit ne sont jamais écrasés et restent à leur place."})
    return problems


# ================================================================ copie, vérification, suppression
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same(src, dst, deep):
    try:
        a, b = os.stat(src), os.stat(dst)
    except OSError:
        return False
    if a.st_size != b.st_size or abs(a.st_mtime - b.st_mtime) > 2:
        return False
    return _sha256(src) == _sha256(dst) if deep else True


def _abs(root, rel):
    return long_path(os.path.join(root, *rel.split("/"))) if rel else long_path(root)


def _win_arg(path):
    p = strip_long(path).rstrip("\\/")
    return p + "\\." if p.endswith(":") else p


class Mover:
    """Déplace des fichiers sans jamais écraser ni supprimer une source non vérifiée."""

    def __init__(self, root, log_dir, options, cancel=None, use_robocopy=None):
        self.root = os.path.realpath(root)
        self.log_dir = log_dir
        self.opts = {**OPTION_DEFAULTS, **(options or {})}
        self.cancel = cancel
        self.robocopy = system.IS_WINDOWS if use_robocopy is None else use_robocopy

    def _check(self):
        if self.cancel is not None and self.cancel():
            raise InterruptedError()

    def robocopy_cmd(self, src, dst, files=None, exclude_dirs=()):
        cmd = ["robocopy", _win_arg(src), _win_arg(dst)] + list(files or [])
        if files is None:
            cmd.append("/E")
        cmd += ["/COPY:DATS", "/DCOPY:DAT", "/XC", "/XN", "/XO", "/XJ", "/R:2", "/W:5",
                f"/MT:{max(1, min(64, int(self.opts['threads'])))}", "/NP", "/NDL",
                "/UNILOG+:" + os.path.join(self.log_dir, "robocopy.log")]
        if self.opts.get("backup_mode"):
            cmd.append("/B")
        if exclude_dirs:
            cmd += ["/XD"] + [_win_arg(d) for d in exclude_dirs]
        return cmd

    def _run_robocopy(self, cmd):
        os.makedirs(self.log_dir, exist_ok=True)     # journal robocopy (/UNILOG+) ; jamais créé par script()
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                creationflags=system.NO_WINDOW)
        while True:
            try:
                return proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if self.cancel is not None and self.cancel():
                    proc.terminate()
                    proc.wait(30)
                    raise InterruptedError()

    def _py_copy(self, src_file, dst_file):
        if os.path.lexists(dst_file):
            return False
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        tmp = dst_file + ".vigiefs-part"
        shutil.copy2(src_file, tmp)
        try:
            st = os.stat(src_file)
            os.chown(tmp, st.st_uid, st.st_gid)
        except (OSError, AttributeError):
            pass
        try:
            os.link(tmp, dst_file) if os.name != "nt" else os.rename(tmp, dst_file)
        except FileExistsError:
            return False
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return True

    def region_files(self, src_abs, exclude_abs):
        """(chemin source, chemin relatif dans la région) des fichiers de la région, hors sous-régions."""
        excl = {os.path.normcase(strip_long(e)) for e in exclude_abs}
        for dirpath, dirnames, filenames in os.walk(src_abs):
            dirnames[:] = [d for d in dirnames if os.path.normcase(strip_long(os.path.join(dirpath, d))) not in excl
                           and not os.path.islink(os.path.join(dirpath, d))]
            sub = os.path.relpath(dirpath, src_abs)
            for f in filenames:
                self._check()
                yield os.path.join(dirpath, f), (f if sub == "." else os.path.join(sub, f))

    def transfer(self, src_abs, dst_abs, exclude_abs=(), files=None):
        """Copie sans écraser, vérifie, supprime les sources vérifiées. Renvoie le bilan et le manifeste."""
        if files is None:
            items = list(self.region_files(src_abs, exclude_abs))
        else:
            items = [(os.path.join(src_abs, f), f) for f in files if os.path.isfile(os.path.join(src_abs, f))]
        preexisting = {rel for _, rel in items if os.path.lexists(os.path.join(dst_abs, rel))}
        os.makedirs(dst_abs, exist_ok=True)
        code = None
        if self.robocopy:
            if files is None:
                code = self._run_robocopy(self.robocopy_cmd(src_abs, dst_abs, exclude_dirs=exclude_abs))
            else:
                chunk, size = [], 0
                for f in files:
                    chunk.append(f)
                    size += len(f) + 3
                    if size > 20000:
                        code = max(code or 0, self._run_robocopy(self.robocopy_cmd(src_abs, dst_abs, chunk)))
                        chunk, size = [], 0
                if chunk:
                    code = max(code or 0, self._run_robocopy(self.robocopy_cmd(src_abs, dst_abs, chunk)))
        else:
            for src_file, rel in items:
                self._check()
                if rel not in preexisting:
                    try:
                        self._py_copy(src_file, os.path.join(dst_abs, rel))
                    except OSError:
                        pass
        moved, kept = [], []
        for src_file, rel in items:
            self._check()
            dst_file = os.path.join(dst_abs, rel)
            if _same(src_file, dst_file, self.opts["verify_hash"] or rel in preexisting):
                try:
                    os.remove(src_file)
                    moved.append(rel)
                except OSError as exc:
                    kept.append({"path": rel, "reason": f"source non supprimée : {exc.strerror or exc}"})
            else:
                kept.append({"path": rel, "reason": "conflit avec un fichier déjà présent" if rel in preexisting
                             else "copie absente ou différente (fichier ouvert, verrouillé ou modifié ?)"})
        return {"moved": moved, "kept": kept, "robocopy": code, "preexisting": len(preexisting)}


def remove_empty_dirs(path):
    """Supprime les dossiers vides de bas en haut (jamais un dossier contenant encore un fichier)."""
    removed = 0
    for dirpath, _dirnames, _files in os.walk(path, topdown=False):
        try:
            os.rmdir(dirpath)
            removed += 1
        except OSError:
            pass
    return removed


def safe_rename(src, dst):
    """Renommage atomique sans écrasement (dans le même dossier)."""
    if os.path.lexists(dst) and os.path.normcase(src) != os.path.normcase(dst):
        raise FileExistsError(dst)
    if os.name == "nt" or os.path.normcase(src) == os.path.normcase(dst):
        os.rename(src, dst)
    else:
        os.link(src, dst)
        os.unlink(src)


def inventory(root, skip_roots=(), cancel=None):
    root_io = long_path(os.path.realpath(root))
    skip = {os.path.normcase(os.path.realpath(r)) for r in skip_roots if r}
    files = size = 0
    for dirpath, dirnames, filenames in os.walk(root_io):
        if cancel is not None and cancel():
            raise InterruptedError()
        dirnames[:] = [d for d in dirnames if os.path.normcase(strip_long(os.path.join(dirpath, d))) not in skip
                       and not os.path.islink(os.path.join(dirpath, d))]
        for f in filenames:
            try:
                size += os.lstat(os.path.join(dirpath, f)).st_size
                files += 1
            except OSError:
                pass
    return {"files": files, "size": size}


# ================================================================ étapes
def planned_dirs(plan, root, cancel=None):
    """Dossiers à créer à l'étape 1 : emplacement final de chaque dossier des régions déplacées."""
    m = Mapping(plan)
    targets = set()
    for r in m.roots():
        src = _abs(root, r)
        if not os.path.isdir(src):
            continue
        for dirpath, dirnames, _ in os.walk(src):
            if cancel is not None and cancel():
                raise InterruptedError()
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            sub = os.path.relpath(dirpath, src).replace("\\", "/")
            rel = r if sub == "." else _join(r, sub)
            targets.add(m.dir(rel))
    for o in plan["ops"]:
        if o.get("selected") and o["type"] == "move_files":
            targets.add(o["dest_dir"] if o.get("dest_dir") else _join(m.dir(o["src"]), o["dest_sub"]))
    return sorted(t for t in targets if t)


def preflight(plan, root, open_files=(), free_bytes=None):
    """Contrôles avant création des dossiers : bloquants (error) et avertissements (warning)."""
    problems = validate(plan)
    m = Mapping(plan)
    for o in [o for o in plan["ops"] if o.get("selected")]:
        src = _abs(root, o["src"])
        if o["type"] in ("rename_file",):
            if not os.path.isfile(src):
                problems.append({"op": o["id"], "level": "warning", "message": f"Fichier absent : {o['src']} (ignoré)"})
        elif not os.path.isdir(src):
            problems.append({"op": o["id"], "level": "warning", "message": f"Dossier absent : {o['src']} (ignoré)"})
    crit = int(plan["path_rules"]["crit_len"])
    prefix = plan["prefix"]
    for t in planned_dirs(plan, root):
        n = pathcheck.user_path_length(prefix, t)
        if n > crit:
            problems.append({"op": None, "level": "warning",
                             "message": f"Dossier cible de {n} caractères (limite {crit}) : {t}"})
        for part in t.split("/"):
            if pathcheck.name_issues(part):
                problems.append({"op": None, "level": "error", "message": f"Nom de dossier cible non conforme : {t}"})
                break
    if open_files:
        sources = [_abs(root, r) for r in m.roots()] + [_abs(root, o["src"]) for o in plan["ops"]
                                                         if o.get("selected") and o["type"] == "move_files"]
        busy = [f for f in open_files if any(os.path.normcase(strip_long(f)).startswith(os.path.normcase(strip_long(s)))
                                             for s in sources)]
        if busy:
            problems.append({"op": None, "level": "warning",
                             "message": f"{len(busy)} fichier(s) ouvert(s) dans les dossiers concernés : ils resteront "
                                        "à leur place et seront listés (fermez-les ou relancez plus tard).",
                             "files": busy[:50]})
    largest = max([o.get("size") or 0 for o in plan["ops"] if o.get("selected")] or [0])
    if free_bytes is not None and largest and free_bytes < largest * 1.1:
        problems.append({"op": None, "level": "error",
                         "message": "Espace disque insuffisant : chaque fichier est copié avant que la source ne soit "
                                    "supprimée, il faut au moins la taille de la plus grosse opération."})
    return problems


def prepare(plan, root, cancel=None):
    """Étape 1 : crée l'arborescence cible. Aucun fichier n'est déplacé."""
    created, existing = [], []
    for t in planned_dirs(plan, root, cancel):
        p = _abs(root, t)
        if os.path.isdir(p):
            existing.append(t)
            continue
        os.makedirs(p, exist_ok=True)
        created.append(t)
    return {"created": created, "existing": existing, "fingerprint": fingerprint(plan), "at": time.time()}


def unprepare(created, root):
    removed = 0
    for t in sorted(created, key=_depth, reverse=True):
        try:
            os.rmdir(_abs(root, t))
            removed += 1
        except OSError:
            pass
    return removed


def execute(plan, root, work_dir, cancel=None, progress=None):
    """Étape 2 : déplacements vérifiés. Renvoie le bilan par opération et le chemin du manifeste."""
    m = Mapping(plan)
    mover = Mover(root, work_dir, plan.get("options"), cancel)
    manifest_path = os.path.join(work_dir, "manifeste.jsonl")
    results = {}
    totals = {"moved": 0, "kept": 0, "renamed": 0, "removed_dirs": 0}
    sel = [o for o in plan["ops"] if o.get("selected")]
    done_count = 0

    def note(op, status, **extra):
        results[op["id"]] = {"status": status, **extra}

    def tick(label):
        nonlocal done_count
        done_count += 1
        if progress:
            progress(done=done_count, total=len(sel), current=label, **totals)

    with open(manifest_path, "a", encoding="utf-8") as manifest:
        def record(kind, src, dst):
            manifest.write(json.dumps({"kind": kind, "src": src, "dst": dst, "at": time.time()}, ensure_ascii=False) + "\n")
            manifest.flush()

        # 1. Renommages de fichiers, atomiques, à leur emplacement d'origine.
        renamed_now = {}
        for o in [o for o in sel if o["type"] == "rename_file"]:
            src = _abs(root, o["src"])
            dst = os.path.join(os.path.dirname(src), o["name"])
            try:
                safe_rename(src, dst)
                renamed_now[o["src"]] = _join(_parent(o["src"]), o["name"])
                record("rename", o["src"], renamed_now[o["src"]])
                totals["renamed"] += 1
                note(o, "done")
            except FileNotFoundError:
                note(o, "skipped", error="fichier absent")
            except FileExistsError:
                note(o, "skipped", error="un fichier porte déjà ce nom")
            except OSError as exc:
                note(o, "failed", error=exc.strerror or str(exc))
            tick(o["src"])

        # 2. Fichiers rangés ailleurs (racine, rangement par année).
        for o in [o for o in sel if o["type"] == "move_files"]:
            src_dir = _abs(root, o["src"])
            dest_rel = o["dest_dir"] if o.get("dest_dir") else _join(m.dir(o["src"]), o["dest_sub"])
            names = [_base(renamed_now.get(_join(o["src"], f), _join(o["src"], f))) for f in o["files_list"]]
            res = mover.transfer(src_dir, _abs(root, dest_rel), files=[f for f in names
                                                                        if os.path.isfile(os.path.join(src_dir, f))])
            for f in res["moved"]:
                record("move", _join(o["src"], f), _join(dest_rel, f))
            totals["moved"] += len(res["moved"])
            totals["kept"] += len(res["kept"])
            note(o, "done" if not res["kept"] else "partial", moved=len(res["moved"]), kept=res["kept"][:200],
                 robocopy=res["robocopy"])
            tick(o["src"])

        # 3. Régions de dossiers (renommés, déplacés, niveau supprimé) : chaque fichier copié une seule fois.
        roots = m.roots()
        for r in roots:
            ops_here = [o for o in sel if o["type"] in DIR_OPS and o["src"] == r]
            src_abs = _abs(root, r)
            if not os.path.isdir(src_abs):
                for o in ops_here:
                    note(o, "skipped", error="dossier absent")
                    tick(r)
                continue
            nested = [_abs(root, x) for x in roots if x != r and x.startswith(r + "/")]
            target = m.dir(r)
            res = mover.transfer(src_abs, _abs(root, target), exclude_abs=nested)
            for f in res["moved"]:
                rel = f.replace("\\", "/")
                record("move", _join(r, rel), _join(target, rel))
            totals["moved"] += len(res["moved"])
            totals["kept"] += len(res["kept"])
            for o in ops_here:
                note(o, "done" if not res["kept"] else "partial", moved=len(res["moved"]), kept=res["kept"][:200],
                     robocopy=res["robocopy"], target=target)
                tick(r)

        # 4. Nettoyage : anciens dossiers devenus vides, dossiers vides proposés.
        for r in sorted(roots, key=_depth, reverse=True):
            p = _abs(root, r)
            if os.path.isdir(p):
                n = remove_empty_dirs(p)
                totals["removed_dirs"] += n
                if not os.path.isdir(p):
                    record("rmdir", r, None)
        for o in [o for o in sel if o["type"] == "remove_empty"]:
            p = _abs(root, o["src"])
            if not os.path.isdir(p):
                note(o, "skipped", error="dossier absent")
            else:
                has_files = any(files for _, _, files in os.walk(p))
                if has_files:
                    note(o, "skipped", error="le dossier contient désormais des fichiers")
                else:
                    totals["removed_dirs"] += remove_empty_dirs(p)
                    record("rmdir", o["src"], None)
                    note(o, "done")
            tick(o["src"])
    return {"ops": results, "totals": totals, "manifest": manifest_path}


def rollback(root, work_dir, options=None, cancel=None, progress=None):
    """Retour arrière : chaque fichier du manifeste regagne sa place d'origine (mêmes garanties)."""
    manifest_path = os.path.join(work_dir, "manifeste.jsonl")
    entries = []
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return {"moved": 0, "kept": [], "renamed": 0}
    mover = Mover(root, os.path.join(work_dir, "retour"), options, cancel)
    groups = defaultdict(list)
    for e in entries:
        if e["kind"] == "move":
            groups[(_parent(e["dst"]), _parent(e["src"]))].append((_base(e["dst"]), _base(e["src"])))
    moved, kept = 0, []
    for n, ((dst_dir, src_dir), files) in enumerate(groups.items()):
        if progress:
            progress(done=n, total=len(groups), current=dst_dir)
        os.makedirs(_abs(root, src_dir), exist_ok=True)
        res = mover.transfer(_abs(root, dst_dir), _abs(root, src_dir), files=[f for f, _ in files])
        moved += len(res["moved"])
        kept += [{"path": _join(dst_dir, k["path"]), "reason": k["reason"]} for k in res["kept"]]
    renamed = 0
    for e in reversed(entries):
        if e["kind"] == "rename":
            try:
                safe_rename(_abs(root, e["dst"]), _abs(root, e["src"]))
                renamed += 1
            except OSError as exc:
                kept.append({"path": e["dst"], "reason": f"renommage inverse impossible : {exc.strerror or exc}"})
        elif e["kind"] == "rmdir":
            os.makedirs(_abs(root, e["src"]), exist_ok=True)
    for (dst_dir, _src_dir) in groups:
        p = _abs(root, dst_dir)
        if os.path.isdir(p):
            remove_empty_dirs(p)
    os.replace(manifest_path, manifest_path + f".annule-{time.strftime('%Y%m%d-%H%M%S')}")
    return {"moved": moved, "kept": kept[:500], "renamed": renamed}


def vss_snapshot(path):
    """Cliché instantané du volume (Windows) avant déplacement : filet de sécurité supplémentaire."""
    if not system.IS_WINDOWS:
        return {"ok": False, "skipped": "non Windows"}
    drive = os.path.splitdrive(os.path.realpath(path))[0]
    if not drive or drive.startswith("\\\\"):
        return {"ok": False, "skipped": "partage réseau (UNC) : cliché impossible depuis ce serveur"}
    script = (f"$r = Invoke-CimMethod -ClassName Win32_ShadowCopy -MethodName Create -Arguments "
              f"@{{Volume={system.ps_quote(drive + chr(92))}; Context='ClientAccessible'}}\n"
              "@{ code = [int]$r.ReturnValue; id = [string]$r.ShadowID } | ConvertTo-Json -Compress")
    try:
        res = system.powershell(script, timeout=600) or {}
    except system.CommandError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": res.get("code") == 0, "id": res.get("id"), "code": res.get("code"), "volume": drive}


# ================================================================ script de revue
def script(plan, root):
    """Script PowerShell équivalent (création des dossiers puis robocopy) pour revue ou exécution manuelle."""
    m = Mapping(plan)
    mover = Mover(root, "C:\\Temp\\VigieFS-reorganisation", plan.get("options"), use_robocopy=True)
    win = lambda rel: _win_arg(os.path.join(root, *rel.split("/")) if rel else root)  # noqa: E731
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
    lines = ["# VigieFS — réorganisation de " + root, "# Étape 1 : création de l'arborescence cible (aucun déplacement)",
             "$ErrorActionPreference = 'Stop'", "New-Item -ItemType Directory -Force -Path 'C:\\Temp\\VigieFS-reorganisation' | Out-Null"]
    for t in planned_dirs(plan, root):
        lines.append(f"New-Item -ItemType Directory -Force -Path {q(win(t))} | Out-Null")
    lines += ["", "# Étape 2 : renommages de fichiers puis copies SANS écrasement (/XC /XN /XO).",
              "# VigieFS vérifie ensuite chaque fichier copié et ne supprime que les sources vérifiées ;",
              "# ce script ne supprime rien : comparez avec la commande /L indiquée avant tout nettoyage."]
    for o in [o for o in plan["ops"] if o.get("selected") and o["type"] == "rename_file"]:
        lines.append(f"Rename-Item -LiteralPath {q(win(o['src']))} -NewName {q(o['name'])}")
    for o in [o for o in plan["ops"] if o.get("selected") and o["type"] == "move_files"]:
        dest = o["dest_dir"] if o.get("dest_dir") else _join(m.dir(o["src"]), o["dest_sub"])
        cmd = mover.robocopy_cmd(win(o["src"]), win(dest), files=o["files_list"][:200])
        lines.append(subprocess.list2cmdline(cmd))
    roots = m.roots()
    for r in roots:
        nested = [win(x) for x in roots if x != r and x.startswith(r + "/")]
        cmd = mover.robocopy_cmd(win(r), win(m.dir(r)), exclude_dirs=nested)
        lines.append(subprocess.list2cmdline(cmd))
        lines.append("# contrôle : " + subprocess.list2cmdline(cmd[:3] + ["/E", "/L", "/XC", "/XN", "/XO", "/NJH"]))
    return "\r\n".join(lines) + "\r\n"
