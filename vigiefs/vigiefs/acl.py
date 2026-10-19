"""Droits d'accès : collecte des ACL (NTFS / POSIX), normalisation, audit, comparaison, application.

Modèle normalisé d'une entrée (ACE) :
    {principal, ptype, allow, level, scope, inherited, raw}
- level : none < list < read < write < modify < full ;
- scope : portée d'héritage (this, this_sub, this_files, all, sub_files_only, sub_only, files_only) ;
- ptype : user, group, wellknown, orphan, unknown.

Les mêmes constructeurs de commandes servent à l'exécution directe et à la génération de scripts,
pour que ce qui est relu dans un script soit exactement ce qui serait exécuté.
"""

import os
import re
import time
from collections import defaultdict

try:  # comptes POSIX ; absents sous Windows, où les droits NTFS passent par PowerShell
    import grp
    import pwd
except ImportError:
    grp = pwd = None

from . import system

LEVELS = ["none", "list", "read", "write", "modify", "full"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
LEVEL_LABEL = {"none": "Aucun", "list": "Parcours / liste", "read": "Lecture", "write": "Écriture",
               "modify": "Modification", "full": "Contrôle total", "special": "Droits spéciaux"}
LEVEL_SHORT = {"none": "—", "list": "L", "read": "R", "write": "W", "modify": "M", "full": "F"}
SCOPE_LABEL = {
    "this": "Ce dossier seulement", "this_sub": "Ce dossier et les sous-dossiers",
    "this_files": "Ce dossier et les fichiers", "all": "Ce dossier, les sous-dossiers et les fichiers",
    "sub_files_only": "Sous-dossiers et fichiers seulement", "sub_only": "Sous-dossiers seulement",
    "files_only": "Fichiers seulement",
}
PTYPE_LABEL = {"user": "Utilisateur", "group": "Groupe", "wellknown": "Identité système", "orphan": "SID orphelin",
               "unknown": "Inconnu"}

# Identités « tout le monde » : accès ouvert à l'ensemble des utilisateurs.
BROAD_SIDS = {"S-1-1-0", "S-1-5-11", "S-1-5-32-545", "S-1-5-7", "S-1-5-4", "S-1-5-2"}
BROAD_NAMES = {"everyone", "tout le monde", "authenticated users", "utilisateurs authentifiés", "users",
               "utilisateurs", "domain users", "utilisateurs du domaine", "anonymous logon",
               "ouverture de session anonyme", "interactive", "network", "réseau", "other", "autres"}
# Identités d'administration / système : attendues avec un contrôle total.
ADMIN_SIDS = {"S-1-5-18", "S-1-5-32-544", "S-1-3-0", "S-1-5-32-551", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"}
ADMIN_NAMES = {"system", "système", "systeme", "administrators", "administrateurs", "domain admins", "admins du domaine",
               "creator owner", "créateur propriétaire", "createur proprietaire", "enterprise admins",
               "administrateurs de l'entreprise", "backup operators", "opérateurs de sauvegarde", "trustedinstaller",
               "root"}
SYSTEM_NAMES = {"system", "système", "systeme"}
SID_RE = re.compile(r"^S-1-\d+(-\d+)+$")

# Masques NTFS (FileSystemRights)
FULL, MODIFY, READ_EXEC, READ = 0x1F01FF, 0x1301BF, 0x1200A9, 0x120089
WRITE_DATA, APPEND_DATA, DELETE = 0x2, 0x4, 0x10000
G_ALL, G_EXEC, G_WRITE, G_READ = 0x10000000, 0x20000000, 0x40000000, 0x80000000
ICACLS_PERM = {"list": "(RX)", "read": "(OI)(CI)(RX)", "write": "(OI)(CI)(RX,W)", "modify": "(OI)(CI)(M)",
               "full": "(OI)(CI)(F)"}
POSIX_PERM = {"list": "r-x", "read": "r-x", "write": "rwx", "modify": "rwx", "full": "rwx"}


def short_name(principal):
    name = principal.split(":", 1)[1] if principal.startswith(("user:", "group:")) else principal
    return name.split("\\")[-1].strip().lower()


def is_broad(principal, sid=None):
    if principal == "other" or (sid and (sid in BROAD_SIDS or sid.endswith("-513"))):
        return True
    if principal.startswith(("user:", "group:")):
        return False  # POSIX : seul « other » représente tout le monde
    return short_name(principal) in BROAD_NAMES


def is_admin(principal, sid=None):
    if sid and (sid in ADMIN_SIDS or sid.endswith(("-512", "-519"))):
        return True
    return short_name(principal) in ADMIN_NAMES or principal in ("user:root", "group:root")


def display_name(principal):
    if principal == "other":
        return "Autres (tout le monde)"
    if principal.startswith("user:"):
        return principal[5:]
    if principal.startswith("group:"):
        return principal[6:] + " (groupe)"
    return principal


def max_level(a, b):
    return a if LEVEL_RANK.get(a, 0) >= LEVEL_RANK.get(b, 0) else b


# ================================================================ Windows / NTFS
def _expand(mask):
    m = int(mask) & 0xFFFFFFFF
    if m & G_ALL:
        m |= FULL
    if m & G_READ:
        m |= READ
    if m & G_WRITE:
        m |= 0x120116
    if m & G_EXEC:
        m |= 0x1200A0
    return m & FULL


def level_from_mask(mask):
    m = _expand(mask)
    if m & FULL == FULL:
        return "full"
    if m & MODIFY == MODIFY or (m & (WRITE_DATA | APPEND_DATA) and m & DELETE):
        return "modify"
    if m & (WRITE_DATA | APPEND_DATA):
        return "write"
    if m & READ == READ:
        return "read"
    if m & 0x21:
        return "list"
    return "none"


def is_standard_mask(mask):
    return _expand(mask) in (FULL, MODIFY, READ_EXEC, READ, 0x1301FF, 0x116 | READ_EXEC, 0x1200A9 | 0x116)


def scope_from_flags(inh, prop):
    ci, oi = bool(inh & 1), bool(inh & 2)
    io = bool(prop & 2)
    if io:
        return "sub_files_only" if ci and oi else "sub_only" if ci else "files_only"
    return "all" if ci and oi else "this_sub" if ci else "this_files" if oi else "this"


def windows_collect_script(root, max_depth):
    return f"""
$root = {system.ps_quote(root)}
$maxDepth = {int(max_depth)}
$nodes = New-Object System.Collections.Generic.List[object]
$ids = @{{}}
function Get-Node([string]$full, [int]$depth) {{
  try {{ $acl = Get-Acl -LiteralPath $full -ErrorAction Stop }}
  catch {{ $nodes.Add([pscustomobject]@{{p=$full; d=$depth; err=$_.Exception.Message}}); return }}
  $aces = New-Object System.Collections.Generic.List[object]
  foreach ($a in $acl.Access) {{
    $id = [string]$a.IdentityReference
    $ids[$id] = $true
    $aces.Add([pscustomobject]@{{i=$id; m=[int64]$a.FileSystemRights.value__; t=[string]$a.AccessControlType;
      h=[bool]$a.IsInherited; f=[int]$a.InheritanceFlags.value__; g=[int]$a.PropagationFlags.value__}})
  }}
  $nodes.Add([pscustomobject]@{{p=$full; d=$depth; o=[string]$acl.Owner; x=[bool]$acl.AreAccessRulesProtected; a=$aces}})
  if ($depth -lt $maxDepth) {{
    try {{ $children = Get-ChildItem -LiteralPath $full -Directory -Force -ErrorAction Stop }} catch {{ return }}
    foreach ($c in $children) {{
      if (-not ($c.Attributes -band [IO.FileAttributes]::ReparsePoint)) {{ Get-Node $c.FullName ($depth + 1) }}
    }}
  }}
}}
Get-Node $root 0
$res = @{{}}
foreach ($id in @($ids.Keys)) {{
  $sid = $null; $type = 'unknown'
  try {{
    if ($id -match '^S-1-') {{
      $sid = $id
      try {{ $null = (New-Object System.Security.Principal.SecurityIdentifier($id)).Translate([System.Security.Principal.NTAccount]) }}
      catch {{ $type = 'orphan' }}
    }} else {{
      $sid = (New-Object System.Security.Principal.NTAccount($id)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    }}
  }} catch {{}}
  if ($sid -and $type -ne 'orphan') {{
    try {{
      $de = [adsi]"LDAP://<SID=$sid>"; $cls = @($de.Properties['objectClass'])
      if ($cls -contains 'group') {{ $type = 'group' }} elseif ($cls -contains 'user' -or $cls -contains 'computer') {{ $type = 'user' }}
    }} catch {{}}
    if ($type -eq 'unknown') {{
      try {{ if (Get-LocalGroup -SID $sid -ErrorAction Stop) {{ $type = 'group' }} }}
      catch {{ try {{ if (Get-LocalUser -SID $sid -ErrorAction Stop) {{ $type = 'user' }} }} catch {{}} }}
    }}
  }}
  $res[$id] = @{{sid=$sid; type=$type}}
}}
[pscustomobject]@{{nodes=$nodes; principals=$res}} | ConvertTo-Json -Depth 6 -Compress
"""


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _win_rel(root, full):
    r = root.rstrip("\\/")
    rel = full[len(r):] if full.lower().startswith(r.lower()) else full
    return rel.replace("\\", "/").strip("/")


def parse_windows(data, root):
    principals = {}
    for name, info in (data.get("principals") or {}).items():
        sid, ptype = info.get("sid"), info.get("type") or "unknown"
        if SID_RE.match(name) and ptype != "orphan" and not sid:
            ptype = "orphan"
        if ptype in ("unknown", "user", "group") and (is_admin(name, sid) or is_broad(name, sid)):
            ptype = "wellknown" if ptype == "unknown" else ptype
        principals[name] = {"sid": sid, "type": ptype}
    nodes = []
    for n in _as_list(data.get("nodes")):
        rel = _win_rel(root, n.get("p", ""))
        if n.get("err"):
            nodes.append({"path": rel, "depth": n.get("d", 0), "error": n["err"], "aces": []})
            continue
        aces = []
        for a in _as_list(n.get("a")):
            mask = int(a.get("m", 0))
            aces.append({
                "principal": a["i"], "ptype": principals.get(a["i"], {}).get("type", "unknown"),
                "sid": principals.get(a["i"], {}).get("sid"),
                "allow": a.get("t", "Allow") == "Allow", "level": level_from_mask(mask),
                "special": not is_standard_mask(mask), "raw": hex(int(mask) & 0xFFFFFFFF),
                "scope": scope_from_flags(int(a.get("f", 0)), int(a.get("g", 0))), "inherited": bool(a.get("h")),
            })
        nodes.append({"path": rel, "depth": n.get("d", 0), "owner": n.get("o"), "protected": bool(n.get("x")),
                      "aces": aces})
    return nodes, principals


# ================================================================ POSIX (Linux, Samba, NAS)
def _uname(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _gname(gid):
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def posix_level(perms, owner=False):
    perms = perms.ljust(3, "-")[:3]
    r, w, x = perms[0] == "r", perms[1] == "w", perms[2] == "x"
    if owner and r and w and x:
        return "full"
    if w and x:
        return "modify"
    if w:
        return "write"
    if r and x:
        return "read"
    if r or x:
        return "list"
    return "none"


def _unescape_getfacl(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_getfacl(text):
    """Analyse la sortie de `getfacl --absolute-names` (plusieurs fichiers) → {chemin: {...}}."""
    out, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# file:"):
            cur = {"owner": None, "group": None, "entries": [], "default": []}
            out[_unescape_getfacl(line[7:].strip())] = cur
        elif cur is None or not line:
            continue
        elif line.startswith("# owner:"):
            cur["owner"] = _unescape_getfacl(line[8:].strip())
        elif line.startswith("# group:"):
            cur["group"] = _unescape_getfacl(line[8:].strip())
        elif line.startswith("#"):
            continue
        else:
            spec = line.split("#")[0].strip()
            eff = re.search(r"#effective:([rwx-]{3})", line)
            default = spec.startswith("default:")
            if default:
                spec = spec[8:]
            parts = spec.split(":")
            if len(parts) != 3:
                continue
            kind, qual, perms = parts
            entry = {"kind": kind, "qual": _unescape_getfacl(qual), "perms": perms,
                     "effective": eff.group(1) if eff else perms}
            (cur["default"] if default else cur["entries"]).append(entry)
    return out


def _posix_entries(path, st, facl):
    """Liste d'entrées getfacl, ou reconstruction depuis le mode si getfacl est absent."""
    if facl:
        return facl
    mode = st.st_mode
    bits = lambda s: "".join(c if mode & b else "-" for c, b in zip("rwx", s))  # noqa: E731
    return {"owner": _uname(st.st_uid), "group": _gname(st.st_gid), "default": [], "entries": [
        {"kind": "user", "qual": "", "perms": bits((0o400, 0o200, 0o100)), "effective": bits((0o400, 0o200, 0o100))},
        {"kind": "group", "qual": "", "perms": bits((0o40, 0o20, 0o10)), "effective": bits((0o40, 0o20, 0o10))},
        {"kind": "other", "qual": "", "perms": bits((0o4, 0o2, 0o1)), "effective": bits((0o4, 0o2, 0o1))}]}


def _posix_principal(entry, owner, group):
    kind, qual = entry["kind"], entry["qual"]
    if kind == "other":
        return "other", "wellknown"
    if kind == "user":
        name = qual or owner
        return f"user:{name}", "orphan" if name.isdigit() else "user"
    name = qual or group
    return f"group:{name}", "orphan" if name.isdigit() else "group"


def posix_collect(root, max_depth, cancel=None):
    root = os.path.realpath(root)
    paths = []
    stack = [(root, 0)]
    while stack:
        if cancel is not None and cancel():
            break
        p, d = stack.pop()
        paths.append((p, d))
        if d >= max_depth:
            continue
        try:
            with os.scandir(p) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append((e.path, d + 1))
        except OSError:
            pass
    paths.sort()
    facls = {}
    if system.capabilities()["getfacl"]:
        for i in range(0, len(paths), 200):
            chunk = [p for p, _ in paths[i:i + 200]]
            try:
                facls.update(parse_getfacl(system.run(["getfacl", "--absolute-names", "--"] + chunk, check=False)))
            except system.CommandError:
                pass
    nodes, principals = [], {}
    defaults = {}
    for p, d in paths:
        rel = os.path.relpath(p, root).replace("\\", "/")
        rel = "" if rel == "." else rel
        try:
            st = os.lstat(p)
        except OSError as exc:
            nodes.append({"path": rel, "depth": d, "error": str(exc), "aces": []})
            continue
        info = _posix_entries(p, st, facls.get(p))
        owner, group = info["owner"] or _uname(st.st_uid), info["group"] or _gname(st.st_gid)
        dflt = {(e["kind"], e["qual"]): e["perms"] for e in info["default"]}
        defaults[rel] = dflt
        parent = defaults.get(os.path.dirname(rel) if rel else None, {}) if rel else {}
        aces = []
        for e in info["entries"]:
            if e["kind"] == "mask":
                continue
            principal, ptype = _posix_principal(e, owner, group)
            principals.setdefault(principal, {"sid": None, "type": ptype})
            is_owner = e["kind"] == "user" and not e["qual"]
            perms = e["perms"] if is_owner or e["kind"] == "other" else e["effective"]
            aces.append({
                "principal": principal, "ptype": ptype, "sid": None, "allow": True,
                "level": posix_level(perms, owner=is_owner), "special": False, "raw": perms,
                "scope": "all" if (e["kind"], e["qual"]) in dflt else "this",
                "inherited": bool(rel) and parent.get((e["kind"], e["qual"])) == e["perms"],
                "role": "owner" if is_owner else "owner_group" if e["kind"] == "group" and not e["qual"] else None,
            })
        nodes.append({"path": rel, "depth": d, "owner": f"user:{owner}", "protected": None, "aces": aces,
                      "has_default": bool(info["default"])})
    return nodes, principals


# ================================================================ collecte + audit
def collect(root, max_depth=4, cancel=None):
    if system.IS_WINDOWS:
        data = system.powershell(windows_collect_script(root, max_depth), timeout=3600) or {}
        nodes, principals = parse_windows(data, root)
        platform = "windows"
    else:
        nodes, principals = posix_collect(root, max_depth, cancel)
        platform = "posix"
    nodes.sort(key=lambda n: (n["path"].count("/") if n["path"] else -1, n["path"]))
    return {"platform": platform, "root": root, "depth": max_depth, "created_at": time.time(),
            "nodes": nodes, "principals": principals}


def applies_to_folder(ace):
    return ace["scope"] not in ("sub_files_only", "sub_only", "files_only")


def effective_levels(node):
    """Niveau effectif par identité sur ce dossier (autorisations − refus)."""
    allow, deny = defaultdict(lambda: "none"), defaultdict(lambda: "none")
    for a in node.get("aces", []):
        if not applies_to_folder(a):
            continue
        target = allow if a["allow"] else deny
        target[a["principal"]] = max_level(target[a["principal"]], a["level"])
    out = {}
    for p, lv in allow.items():
        d = deny.get(p, "none")
        if d in ("full", "read", "list"):
            lv = "none"
        elif d in ("write", "modify") and LEVEL_RANK[lv] > LEVEL_RANK["read"]:
            lv = "read"
        out[p] = lv
    return out


def acl_signature(node):
    return tuple(sorted((a["principal"], a["allow"], a["level"], a["scope"]) for a in node.get("aces", [])
                        if not a["inherited"]))


FINDING_INFO = {
    "open_write": ("critical", "Écriture ouverte à tous",
                   "Retirez l'identité « tout le monde » et accordez l'accès à des groupes dédiés."),
    "open_read": ("warning", "Lecture ouverte à tous",
                  "Vérifiez que ces données peuvent être lues par tous ; sinon restreignez à des groupes."),
    "direct_user": ("warning", "Droit nominatif (utilisateur direct)",
                    "Placez l'utilisateur dans un groupe et accordez le droit au groupe (modèle AGDLP)."),
    "orphan": ("warning", "SID orphelin (compte supprimé)",
               "Supprimez l'entrée après avoir vérifié qu'il ne s'agit pas d'un compte d'un domaine approuvé."),
    "deny": ("warning", "Refus explicite",
             "Les refus compliquent l'administration : préférez retirer l'autorisation correspondante."),
    "full_non_admin": ("warning", "Contrôle total hors administrateurs",
                       "Le contrôle total permet de modifier les droits : limitez à « Modification »."),
    "no_system": ("critical", "SYSTÈME sans contrôle total",
                  "Le compte SYSTÈME (sauvegarde Datto, antivirus) doit avoir le contrôle total."),
    "no_admin": ("warning", "Administrateurs sans contrôle total",
                 "Accordez le contrôle total au groupe Administrateurs pour garder la maîtrise du dossier."),
    "broken_inheritance": ("info", "Héritage désactivé",
                           "Point de rupture d'héritage : vérifiez qu'il est volontaire et documenté dans la matrice."),
    "deep_explicit": ("warning", "Droits explicites en profondeur",
                      "Définissez les droits sur les premiers niveaux seulement et laissez l'héritage agir."),
    "too_many_aces": ("info", "Liste d'accès trop longue",
                      "Plus de 15 entrées explicites : regroupez les accès dans des groupes."),
    "owner_orphan": ("info", "Propriétaire inconnu", "Réattribuez la propriété au groupe Administrateurs."),
    "special_rights": ("info", "Droits spéciaux", "Remplacez les combinaisons de droits avancés par des niveaux standards."),
    "error": ("warning", "Droits illisibles", "Le compte de service VigieFS ne peut pas lire ces droits."),
}
SEV_WEIGHT = {"critical": 8, "warning": 3, "info": 1}


def audit(snapshot, matrix_depth=2):
    """Anomalies de droits du modèle normalisé."""
    findings = []

    def add(code, node, principal=None, detail=""):
        sev, title, reco = FINDING_INFO[code]
        findings.append({"code": code, "severity": sev, "title": title, "path": node["path"],
                         "principal": principal, "detail": detail, "recommendation": reco})

    posix = snapshot["platform"] == "posix"
    for node in snapshot["nodes"]:
        if node.get("error"):
            add("error", node, detail=node["error"])
            continue
        aces = node["aces"]
        explicit = [a for a in aces if not a["inherited"]]
        for a in explicit:
            p = a["principal"]
            if a["allow"] and is_broad(p, a.get("sid")) and a["level"] != "none":
                if LEVEL_RANK[a["level"]] >= LEVEL_RANK["write"]:
                    add("open_write", node, p, LEVEL_LABEL[a["level"]])
                elif a["level"] == "read" and (not posix or node["depth"] <= matrix_depth):
                    add("open_read", node, p, LEVEL_LABEL[a["level"]])
            if a["ptype"] == "orphan":
                add("orphan", node, p)
            elif a["ptype"] == "user" and a.get("role") is None and not is_admin(p, a.get("sid")) and a["allow"] \
                    and a["level"] != "none":
                add("direct_user", node, p, LEVEL_LABEL[a["level"]])
            if not a["allow"]:
                add("deny", node, p, LEVEL_LABEL.get(a["level"], a["level"]))
            if a["allow"] and a["level"] == "full" and not is_admin(p, a.get("sid")) and a.get("role") != "owner" \
                    and not is_broad(p, a.get("sid")):
                add("full_non_admin", node, p)
            if a.get("special"):
                add("special_rights", node, p, a["raw"])
        if explicit and node["depth"] > matrix_depth + 1 and not posix:
            add("deep_explicit", node, detail=f"{len(explicit)} entrée(s) au niveau {node['depth']}")
        if len(explicit) > 15:
            add("too_many_aces", node, detail=f"{len(explicit)} entrées")
        if node.get("protected") and node["depth"] > 0:
            add("broken_inheritance", node)
        if not posix and node["depth"] == 0:
            eff = effective_levels(node)
            sids = {a["principal"]: a.get("sid") for a in aces}
            if not any(lv == "full" and (short_name(p) in SYSTEM_NAMES or sids.get(p) == "S-1-5-18")
                       for p, lv in eff.items()):
                add("no_system", node)
            if not any(lv == "full" and (short_name(p) in ("administrators", "administrateurs")
                                         or sids.get(p) == "S-1-5-32-544") for p, lv in eff.items()):
                add("no_admin", node)
        owner = node.get("owner") or ""
        if owner and (SID_RE.match(owner) or owner.split(":")[-1].isdigit()):
            add("owner_orphan", node, owner)
    return findings


def summarize(snapshot, findings):
    nodes = [n for n in snapshot["nodes"] if not n.get("error")]
    sigs = {acl_signature(n) for n in nodes if acl_signature(n)}
    by_sev = defaultdict(int)
    by_code = defaultdict(int)
    for f in findings:
        by_sev[f["severity"]] += 1
        by_code[f["code"]] += 1
    penalty = sum(SEV_WEIGHT[f["severity"]] for f in findings)
    score = max(0, round(100 - 100 * penalty / max(1, penalty + 6 * len(nodes))))
    return {
        "folders": len(snapshot["nodes"]), "errors": sum(1 for n in snapshot["nodes"] if n.get("error")),
        "explicit_aces": sum(1 for n in nodes for a in n["aces"] if not a["inherited"]),
        "protected": sum(1 for n in nodes if n.get("protected")), "unique_acls": len(sigs),
        "principals": len(snapshot["principals"]), "by_severity": dict(by_sev), "by_code": dict(by_code),
        "score": score,
    }


def diff_snapshots(old, new):
    """Changements de droits explicites, d'héritage et de propriétaire entre deux instantanés."""
    o = {n["path"]: n for n in old["nodes"] if not n.get("error")}
    changes = []
    for n in new["nodes"]:
        if n.get("error") or n["path"] not in o:
            continue
        prev = o[n["path"]]
        if prev.get("protected") != n.get("protected") and n.get("protected") is not None:
            changes.append({"path": n["path"], "type": "inheritance",
                            "detail": "héritage désactivé" if n["protected"] else "héritage réactivé"})
        if (prev.get("owner") or "") != (n.get("owner") or ""):
            changes.append({"path": n["path"], "type": "owner", "detail": f"{prev.get('owner')} → {n.get('owner')}"})
        before = {(a["principal"], a["allow"], a["level"], a["scope"]) for a in prev["aces"] if not a["inherited"]}
        after = {(a["principal"], a["allow"], a["level"], a["scope"]) for a in n["aces"] if not a["inherited"]}
        for p, allow, lv, _ in sorted(after - before):
            changes.append({"path": n["path"], "type": "added", "principal": p,
                            "detail": f"{'autorisé' if allow else 'refusé'} : {LEVEL_LABEL[lv]}"})
        for p, allow, lv, _ in sorted(before - after):
            changes.append({"path": n["path"], "type": "removed", "principal": p,
                            "detail": f"{'autorisé' if allow else 'refusé'} : {LEVEL_LABEL[lv]}"})
    new_paths = {n["path"] for n in new["nodes"]}
    created = len(new_paths - set(o))
    deleted = len(set(o) - new_paths)
    return {"changes": changes, "created_folders": created, "deleted_folders": deleted}


# ================================================================ identités, appartenance, accès effectifs
def groups_of(principal):
    """Groupes (transitifs si possible) d'un utilisateur."""
    if system.IS_WINDOWS:
        script = f"""
$n = {system.ps_quote(principal)}
$out = @()
try {{
  $sid = (New-Object System.Security.Principal.NTAccount($n)).Translate([System.Security.Principal.SecurityIdentifier]).Value
  $u = [adsi]"LDAP://<SID=$sid>"; $u.RefreshCache(@('tokenGroups'))
  foreach ($b in $u.Properties['tokenGroups']) {{
    try {{ $out += (New-Object System.Security.Principal.SecurityIdentifier($b, 0)).Translate([System.Security.Principal.NTAccount]).Value }} catch {{}}
  }}
}} catch {{}}
try {{ foreach ($g in Get-LocalGroup) {{ if (Get-LocalGroupMember -Group $g -ErrorAction SilentlyContinue | Where-Object {{ $_.Name -eq $n }}) {{ $out += $g.Name }} }} }} catch {{}}
@($out | Sort-Object -Unique) | ConvertTo-Json -Compress
"""
        return [g for g in _as_list(system.powershell(script, timeout=120)) if g]
    name = principal[5:] if principal.startswith("user:") else principal
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        return []
    groups = {g.gr_name for g in grp.getgrall() if name in g.gr_mem}
    groups.add(_gname(user.pw_gid))
    return sorted(f"group:{g}" for g in groups)


def members_of(group):
    if system.IS_WINDOWS:
        script = f"""
$g = {system.ps_quote(group)}
$out = @()
if (Get-Command Get-ADGroupMember -ErrorAction SilentlyContinue) {{
  try {{ $out = @(Get-ADGroupMember -Identity ($g.Split('\\')[-1]) -Recursive | ForEach-Object {{ $_.SamAccountName }}) }} catch {{}}
}}
if (-not $out) {{ try {{ $out = @(Get-LocalGroupMember -Group ($g.Split('\\')[-1]) | ForEach-Object {{ $_.Name }}) }} catch {{}} }}
@($out) | ConvertTo-Json -Compress
"""
        return [m for m in _as_list(system.powershell(script, timeout=120)) if m]
    name = group[6:] if group.startswith("group:") else group
    try:
        g = grp.getgrnam(name)
    except KeyError:
        return []
    members = set(g.gr_mem) | {u.pw_name for u in pwd.getpwall() if u.pw_gid == g.gr_gid}
    return sorted(f"user:{m}" for m in members)


def effective_access(snapshot, principal, groups):
    """Dossiers accessibles à une identité (directement, via ses groupes ou via « tout le monde »)."""
    ident = {principal} | set(groups)
    lower = {short_name(i) for i in ident}
    rows = []
    for n in snapshot["nodes"]:
        if n.get("error"):
            continue
        best, via = "none", []
        denied = False
        for a in n["aces"]:
            if not applies_to_folder(a):
                continue
            p = a["principal"]
            match = p in ident or short_name(p) in lower or is_broad(p, a.get("sid"))
            if not match:
                continue
            if not a["allow"]:
                denied = denied or LEVEL_RANK[a["level"]] >= LEVEL_RANK["read"]
                continue
            if LEVEL_RANK[a["level"]] > LEVEL_RANK[best]:
                best, via = a["level"], [p]
            elif a["level"] == best and p not in via:
                via.append(p)
        if denied:
            best = "none"
        if best != "none":
            rows.append({"path": n["path"], "depth": n["depth"], "level": best, "via": via[:4]})
    return rows


# ================================================================ commandes d'application
def _win_principal(principal, sid=None):
    if SID_RE.match(principal):
        return "*" + principal
    return principal


def _posix_qual(principal):
    if principal == "other":
        return "o:"
    if principal.startswith("user:"):
        return "u:" + principal[5:]
    if principal.startswith("group:"):
        return "g:" + principal[6:]
    return "g:" + principal


def op_commands(op, abs_path, platform):
    """Commandes système d'une opération ({op, principal, level, …}) pour la plateforme donnée."""
    kind = op["op"]
    p = op.get("principal")
    if platform == "windows":
        if kind == "grant":
            return [["icacls", abs_path, "/grant:r", f"{_win_principal(p)}:{ICACLS_PERM[op['level']]}", "/C"]]
        if kind == "revoke":
            return [["icacls", abs_path, "/remove:g", _win_principal(p), "/C"]]
        if kind == "remove_deny":
            return [["icacls", abs_path, "/remove:d", _win_principal(p), "/C"]]
        if kind == "protect":
            return [["icacls", abs_path, "/inheritance:r" if op.get("strip") else "/inheritance:d", "/C"]]
        if kind == "inherit":
            return [["icacls", abs_path, "/inheritance:e", "/C"]]
        if kind == "owner":
            return [["icacls", abs_path, "/setowner", _win_principal(p), "/C"]]
        if kind == "deny_write":
            return [["icacls", abs_path, "/deny", f"{_win_principal(p)}:(OI)(CI)(WD,AD)", "/C"]]
        if kind == "reset_children":
            return [["icacls", abs_path.rstrip("\\") + "\\*", "/reset", "/T", "/C", "/Q"]]
        if kind == "mkdir":
            return [["cmd", "/c", "mkdir", abs_path]]
    else:
        q = _posix_qual(p) if p else None
        recursive = ["-R"] if op.get("recursive") else []
        files = (lambda *args: [["find", abs_path, "-maxdepth", "1", "-type", "f", "-exec", "setfacl", *args, "{}", "+"]]) \
            if op.get("files") else (lambda *args: [])
        if kind == "grant":
            perms = POSIX_PERM[op["level"]]
            spec = f"{q}:{perms}" if q != "o:" else f"o::{perms}"
            cmds = [["setfacl"] + recursive + ["-m", spec, abs_path]]
            if op["level"] != "list" and q != "o:":
                cmds.append(["setfacl"] + (["-R"] if op.get("recursive") else []) + ["-d", "-m", spec, abs_path])
            return cmds + files("-m", spec)
        if kind == "revoke":
            if q == "o:":
                return [["setfacl"] + recursive + ["-m", "o::---", abs_path]] + files("-m", "o::---")
            return [["setfacl"] + recursive + ["-x", q, abs_path], ["setfacl"] + recursive + ["-d", "-x", q, abs_path]] \
                + files("-x", q)
        if kind == "owner":
            return [["chown", p.split(":", 1)[-1], abs_path]]
        if kind == "deny_write":
            return [["setfacl", "-R", "-m", "m::rx", abs_path]]
        if kind == "remove_deny":
            return [["setfacl", "-R", "-m", "m::rwx", abs_path]]
        if kind == "mkdir":
            return [["mkdir", "-p", abs_path]]
        if kind in ("protect", "inherit", "reset_children"):
            return []  # pas d'héritage dynamique en POSIX : sans objet
    raise ValueError(f"Opération inconnue : {kind}")


def backup_commands(abs_path, backup_file, platform):
    if platform == "windows":
        return [["icacls", abs_path, "/save", backup_file, "/T", "/C", "/Q"]]
    return [["getfacl", "-R", "--absolute-names", abs_path]]


def backup(abs_path, backup_file, platform):
    """Sauvegarde complète des ACL d'une arborescence (avant toute modification)."""
    if platform == "windows":
        system.run(backup_commands(abs_path, backup_file, platform)[0], timeout=3600, check=False)
    else:
        out = system.run(backup_commands(abs_path, backup_file, platform)[0], timeout=3600, check=False)
        with open(backup_file, "w", encoding="utf-8") as fh:
            fh.write(out)
    return os.path.exists(backup_file) and os.path.getsize(backup_file) > 0


def restore(abs_path, backup_file, platform):
    if platform == "windows":
        parent = os.path.dirname(abs_path.rstrip("\\")) or abs_path
        return system.run(["icacls", parent, "/restore", backup_file, "/C", "/Q"], timeout=3600)
    return system.run(["setfacl", f"--restore={backup_file}"], timeout=3600)


def quote_cmd(cmd, platform):
    if platform == "windows":
        def q(a):
            return f'"{a}"' if (" " in a or any(c in a for c in "&()^,;")) and not a.startswith("/") else a
        return " ".join(q(a) for a in cmd)
    import shlex
    return " ".join(shlex.quote(a) for a in cmd)


def execute(op, abs_path, platform):
    for cmd in op_commands(op, abs_path, platform):
        check = not (platform == "posix" and ("-x" in cmd or cmd[0] == "find"))  # entrée absente : sans gravité
        system.run(cmd, timeout=600, check=check)
