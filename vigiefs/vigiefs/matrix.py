"""Matrice des droits : extraction de l'existant, proposition AGDLP, plan d'écart, scripts, import/export.

Une matrice décrit le niveau d'accès SOUHAITÉ (effectif) de chaque identité sur chaque dossier des
premiers niveaux. Chaque ligne indique si le dossier hérite des droits de son parent ou s'il possède
sa propre liste d'accès (héritage désactivé). Le plan d'application est l'écart entre la matrice et
le dernier instantané des droits réels.
"""

import csv
import io
import re
import time
import unicodedata

from . import acl
from .acl import LEVEL_LABEL, LEVEL_RANK, LEVELS

SEP = "\t"
LABEL_TO_LEVEL = {}
for _lv, _lab in LEVEL_LABEL.items():
    LABEL_TO_LEVEL[_lab.lower()] = _lv
    LABEL_TO_LEVEL[_lv] = _lv
LABEL_TO_LEVEL.update({"": "none", "-": "none", "—": "none", "aucun": "none", "l": "list", "liste": "list",
                       "r": "read", "rx": "read", "lecture": "read", "w": "write", "m": "modify",
                       "modification": "modify", "f": "full", "ct": "full", "contrôle total": "full",
                       "controle total": "full", "rw": "modify"})


def key(path, principal):
    return f"{path}{SEP}{principal}"


def cell(matrix, path, principal):
    return matrix["cells"].get(key(path, principal), "none")


def parent_path(path):
    return path.rsplit("/", 1)[0] if "/" in path else ("" if path else None)


def _column_order(principal, ptype):
    if acl.is_admin(principal):
        return (0, principal.lower())
    if acl.is_broad(principal):
        return (3, principal.lower())
    return (1 if ptype == "group" else 2, principal.lower())


def _columns(principals, snapshot):
    out = []
    for p in principals:
        ptype = snapshot["principals"].get(p, {}).get("type", "group")
        out.append({"principal": p, "ptype": ptype, "system": acl.is_admin(p), "broad": acl.is_broad(p)})
    out.sort(key=lambda c: _column_order(c["principal"], c["ptype"]))
    return out


# ================================================================ matrice actuelle
def build_current(snapshot, depth):
    rows, cells, used = [], {}, set()
    for n in snapshot["nodes"]:
        if n.get("error") or n["depth"] > depth:
            continue
        rows.append({"path": n["path"], "inherit": not n.get("protected") if n["depth"] else False,
                     "exists": True})
        for p, lv in acl.effective_levels(n).items():
            if lv != "none":
                cells[key(n["path"], p)] = lv
                used.add(p)
    return {"depth": depth, "rows": rows, "columns": _columns(used, snapshot), "cells": cells,
            "groups_plan": [], "options": default_options(), "platform": snapshot["platform"]}


def default_options():
    return {"remove_unlisted": True, "remove_denies": False, "reset_below": False, "create_groups": False,
            "groups_ou": ""}


# ================================================================ proposition AGDLP
def _slug(text, limit=24):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    if len(text) > limit:
        # Coupe sur une frontière de mot (« CABINET_MARTIN » plutôt que « CABINET_MARTIN_A »).
        cut = text[:limit + 1].rsplit("_", 1)[0]
        text = cut if len(cut) >= limit // 2 else text[:limit]
    return text.strip("_") or "X"


def group_name(prefix, client, share, path, access, platform):
    parts = [prefix, _slug(client, 16), _slug(share, 16)]
    if path:
        parts.append(_slug(path.replace("/", "_"), 28))
    parts.append(access)
    name = "_".join(parts)[:63]
    return f"group:{name.lower()}" if platform == "posix" else name


def _admin_columns(snapshot):
    found = []
    for p in snapshot["principals"]:
        sn = acl.short_name(p)
        if sn in ("administrators", "administrateurs") or sn in acl.SYSTEM_NAMES or p == "user:root":
            found.append(p)
    if not found:
        found = ["user:root"] if snapshot["platform"] == "posix" else ["BUILTIN\\Administrators", "NT AUTHORITY\\SYSTEM"]
    return sorted(set(found))


def propose(snapshot, depth, client, share_name, prefix="GDL"):
    """Matrice cible selon les bonnes pratiques : groupes RW/RO par dossier métier, héritage maîtrisé."""
    platform = snapshot["platform"]
    admins = _admin_columns(snapshot)
    nodes = [n for n in snapshot["nodes"] if not n.get("error") and n["depth"] <= depth]
    by_path = {n["path"]: n for n in nodes}
    rows, cells, plan, columns = [], {}, [], list(admins)
    owners = {}  # chemin → (groupe RW, groupe RO) du dossier qui définit les droits
    for n in nodes:
        d = n["depth"]
        own = d == 1 or (d > 1 and n.get("protected"))
        inherit = d > 0 and not own
        rows.append({"path": n["path"], "inherit": inherit, "exists": True})
        for a in admins:
            cells[key(n["path"], a)] = "full"
        if d == 0:
            continue
        if own:
            rw = group_name(prefix, client, share_name, n["path"], "RW", platform)
            ro = group_name(prefix, client, share_name, n["path"], "RO", platform)
            owners[n["path"]] = (rw, ro)
            members = {"RW": [], "RO": []}
            review = {"RW": [], "RO": []}
            for p, lv in acl.effective_levels(n).items():
                if p in admins or acl.is_admin(p) or lv in ("none", "list"):
                    continue
                if snapshot["principals"].get(p, {}).get("type") == "orphan":
                    continue
                side = "RW" if LEVEL_RANK[lv] >= LEVEL_RANK["write"] else "RO"
                if platform == "posix" and not p.startswith("user:"):
                    # Un groupe POSIX ne contient que des comptes : « other » et les groupes sont à revoir.
                    review[side].append(p)
                    continue
                members[side].append(p)
                if acl.is_broad(p):
                    review[side].append(p)
            for g, side, access in ((rw, "RW", "modification"), (ro, "RO", "lecture")):
                plan.append({"group": g, "members": sorted(set(members[side])), "access": access, "path": n["path"],
                             "review": sorted(set(review[side])), "create": True})
                columns.append(g)
            cells[key(n["path"], rw)] = "modify"
            cells[key(n["path"], ro)] = "read"
        else:
            parent = parent_path(n["path"])
            for c in columns:
                lv = cells.get(key(parent, c), "none")
                if lv not in ("none", "list"):
                    cells[key(n["path"], c)] = lv
    # Droit de parcours sur les dossiers parents : indispensable avec l'énumération basée sur l'accès.
    for path, (rw, ro) in owners.items():
        anc = parent_path(path)
        while anc is not None and anc in by_path:
            for g in (rw, ro):
                if cells.get(key(anc, g), "none") == "none":
                    cells[key(anc, g)] = "list"
            anc = parent_path(anc)
    seen, ordered = set(), []
    for c in columns:
        if c not in seen:
            seen.add(c)
            ordered.append({"principal": c, "ptype": "group" if c not in admins else "wellknown",
                            "system": c in admins, "broad": False, "new": c not in admins})
    return {"depth": depth, "rows": rows, "columns": ordered, "cells": cells, "groups_plan": plan,
            "options": default_options(), "platform": platform}


# ================================================================ plan d'application
def _effective_inherited(matrix, path):
    parent = parent_path(path)
    if parent is None:
        return {}
    return {c["principal"]: cell(matrix, parent, c["principal"]) for c in matrix["columns"]
            if cell(matrix, parent, c["principal"]) not in ("none", "list")}


def desired_explicit(matrix):
    """{chemin: ({identité: niveau}, protégé, avertissements)} — entrées explicites à poser."""
    out = {}
    for row in sorted(matrix["rows"], key=lambda r: (r["path"].count("/") if r["path"] else -1, r["path"])):
        path = row["path"]
        wanted = {c["principal"]: cell(matrix, path, c["principal"]) for c in matrix["columns"]}
        wanted = {p: lv for p, lv in wanted.items() if lv != "none"}
        protected = not row.get("inherit") or not path
        warnings = []
        if matrix.get("platform") == "posix":
            # Pas d'héritage dynamique en POSIX : chaque dossier porte ses entrées (copiées récursivement).
            out[path] = (wanted, True, warnings)
            continue
        if not protected:
            inherited = _effective_inherited(matrix, path)
            lowered = [p for p, lv in inherited.items() if LEVEL_RANK[wanted.get(p, "none")] < LEVEL_RANK[lv]]
            if lowered:
                protected = True
                warnings.append("Héritage désactivé pour pouvoir restreindre : "
                                + ", ".join(acl.display_name(p) for p in lowered))
            else:
                wanted = {p: lv for p, lv in wanted.items() if LEVEL_RANK[lv] > LEVEL_RANK[inherited.get(p, "none")]}
        if protected and not any(acl.is_admin(p) and lv == "full" for p, lv in wanted.items()):
            warnings.append("Aucun administrateur n'a le contrôle total : risque de perte de maîtrise du dossier.")
        out[path] = (wanted, protected, warnings)
    return out


def plan(matrix, snapshot):
    """Opérations nécessaires pour passer de l'instantané à la matrice (ordre sûr d'exécution)."""
    opts = {**default_options(), **(matrix.get("options") or {})}
    platform = snapshot["platform"]
    nodes = {n["path"]: n for n in snapshot["nodes"] if not n.get("error")}
    ops, warnings = [], []
    depth = matrix.get("depth", 2)
    row_paths = [r["path"] for r in matrix["rows"]]

    def is_leaf(path):
        prefix = path + "/" if path else ""
        return not any(p != path and p.startswith(prefix) for p in row_paths)

    for path, (wanted, protected, warns) in desired_explicit(matrix).items():
        for w in warns:
            warnings.append({"path": path, "message": w})
        node = nodes.get(path)
        row = next((r for r in matrix["rows"] if r["path"] == path), {})
        if node is None:
            if row.get("create"):
                ops.append({"op": "mkdir", "path": path, "reason": "dossier prévu par la matrice"})
                node = {"path": path, "aces": [], "protected": False, "depth": path.count("/") + 1}
            else:
                warnings.append({"path": path, "message": "Dossier introuvable sur le serveur : ligne ignorée."})
                continue
        current, denies, fixed = {}, [], set()
        for a in node["aces"]:
            if a["inherited"]:
                continue
            if not a["allow"]:
                denies.append(a["principal"])
                continue
            if a["level"] != "none":
                current[a["principal"]] = acl.max_level(current.get(a["principal"], "none"), a["level"])
            if a.get("role"):
                fixed.add(a["principal"])  # propriétaire / groupe propriétaire POSIX : non supprimables
        # POSIX : propagation récursive uniquement sous les feuilles de la matrice ; ailleurs, le dossier
        # et ses fichiers directs (les sous-dossiers ont leur propre ligne).
        leaf = is_leaf(path)
        recursive = platform == "posix" and leaf
        files = platform == "posix" and not leaf
        for p, lv in wanted.items():
            if current.get(p) != lv:
                ops.append({"op": "grant", "path": path, "principal": p, "level": lv,
                            "recursive": recursive and lv != "list", "files": files and lv != "list",
                            "reason": f"{acl.LEVEL_LABEL[current.get(p, 'none')]} → {acl.LEVEL_LABEL[lv]}"})
        if platform == "windows":
            if protected and not node.get("protected"):
                ops.append({"op": "protect", "path": path, "strip": True, "reason": "désactiver l'héritage"})
            elif not protected and node.get("protected"):
                ops.append({"op": "inherit", "path": path, "reason": "réactiver l'héritage"})
        if opts["remove_unlisted"]:
            for p in current:
                if p not in wanted and p not in fixed:
                    ops.append({"op": "revoke", "path": path, "principal": p, "recursive": recursive, "files": files,
                                "reason": f"retirer ({acl.LEVEL_LABEL[current[p]]})"})
        if opts["remove_denies"]:
            for p in sorted(set(denies)):
                ops.append({"op": "remove_deny", "path": path, "principal": p, "reason": "supprimer le refus"})
        if opts["reset_below"] and platform == "windows" and node.get("depth", 0) == depth:
            deeper = [n for n in nodes.values() if n["path"].startswith(path + "/" if path else "")
                      and n["path"] != path and any(not a["inherited"] for a in n["aces"])]
            if deeper:
                ops.append({"op": "reset_children", "path": path,
                            "reason": f"remettre {len(deeper)} sous-dossier(s) en héritage"})
    return {"ops": ops, "warnings": warnings, "summary": _plan_summary(ops)}


def _plan_summary(ops):
    out = {}
    for o in ops:
        out[o["op"]] = out.get(o["op"], 0) + 1
    return out


OP_LABEL = {"grant": "Accorder", "revoke": "Retirer", "protect": "Désactiver l'héritage", "inherit": "Réactiver l'héritage",
            "remove_deny": "Supprimer un refus", "reset_children": "Réinitialiser les sous-dossiers", "mkdir": "Créer le dossier",
            "owner": "Changer le propriétaire", "deny_write": "Verrouiller en écriture"}


# ================================================================ scripts
def _abs(root, path, platform):
    if not path:
        return root
    return root.rstrip("\\/") + ("\\" if platform == "windows" else "/") + (path.replace("/", "\\") if platform == "windows" else path)


def render_script(matrix, plan_, root, share_name, author, backup_path=None):
    platform = matrix.get("platform", "windows")
    opts = {**default_options(), **(matrix.get("options") or {})}
    stamp = time.strftime("%d/%m/%Y %H:%M")
    groups = [g for g in matrix.get("groups_plan", []) if g.get("create", True)]
    lines = []
    if platform == "windows":
        backup_path = backup_path or f"C:\\VigieFS\\acl-avant-matrice-{time.strftime('%Y%m%d-%H%M')}.txt"
        lines += [f"# Matrice des droits VigieFS — « {share_name} »", f"# Racine : {root}",
                  f"# Généré le {stamp} par {author}. Relisez avant exécution (PowerShell administrateur).", "",
                  "$ErrorActionPreference = 'Continue'", "",
                  "# 1. Sauvegarde des droits actuels",
                  f'New-Item -ItemType Directory -Force -Path "{backup_path.rsplit(chr(92), 1)[0]}" | Out-Null',
                  acl.quote_cmd(acl.backup_commands(root, backup_path, "windows")[0], "windows"), ""]
        if groups:
            lines += ["# 2. Groupes de domaine local (modèle AGDLP) — module ActiveDirectory requis", "Import-Module ActiveDirectory"]
            ou = opts.get("groups_ou")
            for g in groups:
                name = g["group"].replace("'", "''")
                desc = f"VigieFS {share_name} : {g['access']} sur {g['path'] or 'racine'}".replace("'", "''")
                lines.append(f"if (-not (Get-ADGroup -Filter \"Name -eq '{name}'\")) {{ New-ADGroup -Name '{name}' "
                             f"-GroupScope DomainLocal -GroupCategory Security -Description '{desc}'"
                             + (f" -Path '{ou}'" if ou else "") + " }")
                members = [m.split("\\")[-1].replace("'", "''") for m in g.get("members", []) if not acl.is_broad(m)]
                if members:
                    lines.append(f"Add-ADGroupMember -Identity '{name}' -Members " + ",".join(f"'{m}'" for m in members))
            lines.append("")
        lines.append("# 3. Application des droits")
    else:
        backup_path = backup_path or f"/var/backups/vigiefs-acl-{time.strftime('%Y%m%d-%H%M')}.facl"
        lines += ["#!/bin/sh", f"# Matrice des droits VigieFS — « {share_name} »", f"# Racine : {root}",
                  f"# Généré le {stamp} par {author}. Relisez avant exécution (root).", "set -u", "",
                  "# 1. Sauvegarde des droits actuels",
                  f"getfacl -R --absolute-names {acl.quote_cmd([root], 'posix')} > {acl.quote_cmd([backup_path], 'posix')}", ""]
        if groups:
            lines.append("# 2. Groupes")
            for g in groups:
                name = g["group"].split(":", 1)[-1]
                lines.append(f"getent group {name} >/dev/null || groupadd {name}")
                for m in g.get("members", []):
                    if m.startswith("user:"):
                        lines.append(f"gpasswd -a {m[5:]} {name}")
            lines.append("")
        lines.append("# 3. Application des droits")
    for op in plan_["ops"]:
        target = _abs(root, op["path"], platform)
        lines.append(f"# {OP_LABEL.get(op['op'], op['op'])} — {op['path'] or '(racine)'}"
                     + (f" — {acl.display_name(op['principal'])}" if op.get("principal") else "")
                     + (f" ({op['reason']})" if op.get("reason") else ""))
        if op["op"] == "mkdir":
            lines.append(f'New-Item -ItemType Directory -Force -Path "{target}" | Out-Null' if platform == "windows"
                         else f"mkdir -p {acl.quote_cmd([target], 'posix')}")
            continue
        for cmd in acl.op_commands(op, target, platform):
            lines.append(acl.quote_cmd(cmd, platform))
    lines += ["", "# Restauration si nécessaire :"]
    if platform == "windows":
        parent = root.rstrip("\\").rsplit("\\", 1)[0] or root
        lines.append(f'# icacls "{parent}" /restore "{backup_path}" /C')
    else:
        lines.append(f"# setfacl --restore={backup_path}")
    return "\r\n".join(lines) + "\r\n" if platform == "windows" else "\n".join(lines) + "\n"


# ================================================================ import / export
def to_rows(matrix):
    header = ["Dossier", "Héritage"] + [acl.display_name(c["principal"]) for c in matrix["columns"]]
    rows = [header]
    for r in matrix["rows"]:
        rows.append([r["path"] or "(racine)", "Hérite" if r.get("inherit") else "Propres droits"]
                    + [LEVEL_LABEL[cell(matrix, r["path"], c["principal"])] if cell(matrix, r["path"], c["principal"]) != "none" else ""
                       for c in matrix["columns"]])
    return rows


def export_csv(matrix):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    for row in to_rows(matrix):
        w.writerow(row)
    w.writerow([])
    w.writerow(["Identités (en-têtes) :"] + [c["principal"] for c in matrix["columns"]])
    return "\ufeff" + buf.getvalue()


def import_csv(text, base):
    """Recharge une matrice éditée dans Excel. `base` fournit les colonnes d'origine (noms complets)."""
    text = text.lstrip("\ufeff")
    dialect = ";" if text.count(";") >= text.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=dialect)]
    if not rows or len(rows[0]) < 3 or rows[0][0].strip().lower() != "dossier":
        raise ValueError("Format inattendu : la première ligne doit commencer par « Dossier;Héritage; »")
    header = rows[0]
    full_names = None
    for r in rows[1:]:
        if r and r[0].startswith("Identités"):
            full_names = r[1:]
    by_display = {acl.display_name(c["principal"]): c for c in base["columns"]}
    columns = []
    for i, label in enumerate(header[2:]):
        label = label.strip()
        if not label:
            continue
        principal = full_names[i] if full_names and i < len(full_names) and full_names[i] else \
            (by_display[label]["principal"] if label in by_display else label)
        old = next((c for c in base["columns"] if c["principal"] == principal), None)
        columns.append(old or {"principal": principal, "ptype": "group", "system": acl.is_admin(principal),
                               "broad": acl.is_broad(principal), "new": True})
    cells, out_rows, errors = {}, [], []
    for line_no, r in enumerate(rows[1:], start=2):
        if not r or not r[0].strip() or r[0].startswith("Identités"):
            continue
        path = "" if r[0].strip() == "(racine)" else r[0].strip().replace("\\", "/").strip("/")
        inherit = (r[1].strip().lower().startswith("h")) if len(r) > 1 else True
        out_rows.append({"path": path, "inherit": inherit and bool(path), "exists": True})
        for i, c in enumerate(columns):
            raw = (r[i + 2] if i + 2 < len(r) else "").strip().lower()
            lv = LABEL_TO_LEVEL.get(raw)
            if lv is None:
                errors.append(f"Ligne {line_no} : niveau « {raw} » inconnu pour {acl.display_name(c['principal'])}")
                continue
            if lv != "none":
                cells[key(path, c["principal"])] = lv
    if errors:
        raise ValueError(" ; ".join(errors[:10]))
    return {**base, "rows": out_rows, "columns": columns, "cells": cells}


def export_xlsx(matrix, path, share_name):
    from .docrepair import write_minimal_xlsx
    legend = [["Niveau", "Signification"]] + [[LEVEL_LABEL[lv], desc] for lv, desc in (
        ("list", "Parcourir et lister ce dossier (sans accès au contenu des sous-dossiers)"),
        ("read", "Lire et exécuter, dossier et sous-dossiers"),
        ("write", "Créer et modifier sans supprimer"),
        ("modify", "Lire, créer, modifier, supprimer"),
        ("full", "Tous les droits, y compris la modification des droits"))]
    groups = [["Groupe", "Accès", "Dossier", "Membres proposés", "À vérifier"]] + [
        [g["group"], g["access"], g["path"] or "(racine)", ", ".join(map(acl.display_name, g.get("members", []))),
         ", ".join(map(acl.display_name, g.get("review", [])))] for g in matrix.get("groups_plan", [])]
    write_minimal_xlsx(path, {"Matrice": to_rows(matrix), "Groupes": groups, "Légende": legend})


def validate(matrix):
    if not isinstance(matrix.get("rows"), list) or not isinstance(matrix.get("columns"), list):
        raise ValueError("Matrice invalide")
    for k, lv in list(matrix.get("cells", {}).items()):
        if lv not in LEVELS:
            raise ValueError(f"Niveau inconnu : {lv}")
        if lv == "none":
            del matrix["cells"][k]
    return matrix
