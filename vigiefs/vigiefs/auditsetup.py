"""Préparation du serveur à l'analyse de l'activité : stratégie d'audit, taille du journal, audit des dossiers.

Windows : sous-catégories d'audit avancé (auditpol), taille du journal Sécurité (wevtutil) et règle d'audit
(SACL) « Tout le monde » sur chaque dossier supervisé : écriture, suppression, droits, propriétaire, et lecture
si elle est demandée. Les réglages d'une stratégie de groupe (GPO) du domaine priment sur ceux du serveur :
dans ce cas, reportez-les dans la GPO (le script généré sert de référence).

Linux / NAS Samba : module « full_audit » sur chaque partage (extrait de smb.conf fourni).
"""

from . import system
from .smb import samba_shares

# (clé, GUID, libellé, utilité, succès, échec)
SUBCATEGORIES = [
    ("file_system", "{0CCE921D-69AE-11D9-BED3-505054503030}", "Système de fichiers",
     "4663 : lectures, modifications, suppressions, droits (avec l'audit posé sur les dossiers)", True, True),
    ("file_share", "{0CCE9224-69AE-11D9-BED3-505054503030}", "Partage de fichiers",
     "5140 : connexions aux partages avec le poste et l'adresse IP", True, False),
    ("detailed_share", "{0CCE9244-69AE-11D9-BED3-505054503030}", "Partage de fichiers détaillé",
     "5145 en échec : accès refusés avec l'adresse du poste (les succès, très volumineux, sont inutiles)", False, True),
    ("logon", "{0CCE9215-69AE-11D9-BED3-505054503030}", "Ouverture de session",
     "4624 / 4625 : connexions réussies et échouées (force brute, pulvérisation)", True, True),
    ("lockout", "{0CCE9217-69AE-11D9-BED3-505054503030}", "Verrouillage du compte",
     "4625 sur compte verrouillé", False, True),
    ("policy", "{0CCE922F-69AE-11D9-BED3-505054503030}", "Modification de la stratégie d'audit",
     "4719 / 4907 : quelqu'un désactive la traçabilité", True, False),
    ("authz", "{0CCE9231-69AE-11D9-BED3-505054503030}", "Modification de la stratégie d'autorisation",
     "4670 : droits modifiés sur un dossier ou un fichier", True, False),
    ("users", "{0CCE9235-69AE-11D9-BED3-505054503030}", "Gestion des comptes d'utilisateur",
     "4720 / 4726 / 4740 : comptes créés, supprimés, verrouillés", True, False),
    ("groups", "{0CCE9237-69AE-11D9-BED3-505054503030}", "Gestion des groupes de sécurité",
     "4728 / 4732 / 4756 : ajout à un groupe (élévation de privilèges)", True, False),
]
LOG_MIN_BYTES = 1024 ** 3                 # 1 Go : plusieurs jours d'audit des fichiers sur un serveur chargé
EVERYONE = "S-1-1-0"
RIGHTS_WRITE = 0x2 | 0x4 | 0x40 | 0x10000 | 0x40000 | 0x80000   # écriture, ajout, suppressions, droits, propriétaire
RIGHT_READ = 0x1

SAMBA_SNIPPET = """[global]
   # VigieFS : audit des accès (journal lu par VigieFS)
   full_audit:prefix = %u|%I|%m|%S
   full_audit:success = connect openat renameat unlinkat mkdirat fchmod fchown fset_nt_acl
   full_audit:failure = connect openat renameat unlinkat mkdirat
   full_audit:facility = local5
   full_audit:priority = notice

[{share}]
   vfs objects = full_audit
   # (ajoutez full_audit aux modules déjà présents, ex. « vfs objects = acl_xattr full_audit »)

# /etc/rsyslog.d/samba-audit.conf
local5.notice    /var/log/samba/audit.log
& stop
"""


def _parse_setting(text):
    s = (text or "").lower()
    success = any(k in s for k in ("succ", "réussite", "reussite", "erfolg"))
    failure = any(k in s for k in ("fail", "échec", "echec", "fehler"))
    return success, failure


def windows_check_script(paths):
    guids = ",".join(f"'{g}'" for _, g, *_ in SUBCATEGORIES)
    plist = ",".join(system.ps_quote(p) for p in paths) or "''"
    return f"""
$r = @{{ policy = @{{}}; log = @{{}}; sacl = @(); admin = $false }}
$r.admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
foreach ($g in @({guids})) {{
  $lines = @(auditpol /get /subcategory:"$g" /r 2>$null | Where-Object {{ $_ -and $_ -match ',' }})
  if ($lines.Count -ge 2) {{ $r.policy[$g] = ($lines[1] -split ',')[4] }} else {{ $r.policy[$g] = $null }}
}}
foreach ($l in @(wevtutil gl Security 2>$null)) {{
  if ($l -match '^\\s*(maxSize|retention|autoBackup):\\s*(.+)$') {{ $r.log[$matches[1]] = $matches[2].Trim() }}
}}
foreach ($p in @({plist})) {{
  if (-not $p) {{ continue }}
  try {{
    $acl = (Get-Item -LiteralPath $p -ErrorAction Stop).GetAccessControl('Audit')
    $rules = @($acl.GetAuditRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {{
      @{{ sid = $_.IdentityReference.Value; rights = [int64]$_.FileSystemRights; flags = [int]$_.AuditFlags;
         inh = [int]$_.InheritanceFlags; inherited = $_.IsInherited }} }})
    $r.sacl += @{{ path = $p; rules = $rules }}
  }} catch {{ $r.sacl += @{{ path = $p; error = $_.Exception.Message }} }}
}}
$r | ConvertTo-Json -Depth 5 -Compress
"""


def evaluate_windows(data, shares, include_reads=True):
    items = []
    for key, guid, label, why, succ, fail in SUBCATEGORIES:
        current = (data.get("policy") or {}).get(guid)
        s, f = _parse_setting(current)
        ok = (s or not succ) and (f or not fail)
        want = " et ".join(x for x, w in (("succès", succ), ("échec", fail)) if w)
        items.append({"key": f"policy:{key}", "group": "Stratégie d'audit", "label": label, "detail": why,
                      "status": "ok" if ok else "missing", "current": current or "inconnu", "expected": want,
                      "command": f'auditpol /set /subcategory:"{guid}"'
                                 + (" /success:enable" if succ else "") + (" /failure:enable" if fail else "")})
    log = data.get("log") or {}
    size = int(log.get("maxSize") or 0)
    items.append({"key": "log_size", "group": "Journal Sécurité", "label": "Taille du journal Sécurité",
                  "detail": "Trop petit, il s'écrase en quelques heures : les traces disparaissent avant d'être lues.",
                  "status": "ok" if size >= LOG_MIN_BYTES else "missing",
                  "current": f"{size / 1048576:.0f} Mo" if size else "inconnue", "expected": "au moins 1 Go",
                  "command": f"wevtutil sl Security /ms:{LOG_MIN_BYTES}"})
    want = RIGHTS_WRITE | (RIGHT_READ if include_reads else 0)
    sacl = {s["path"]: s for s in _as_list(data.get("sacl"))}
    for share in shares:
        info = sacl.get(share["path"]) or {}
        covered = 0
        for r in _as_list(info.get("rules")):
            if r.get("sid") in (EVERYONE, "S-1-5-11") and int(r.get("flags") or 0) & 1 and int(r.get("inh") or 0) == 3:
                covered |= int(r.get("rights") or 0)
        ok = covered & want == want
        status = "error" if info.get("error") else "ok" if ok else "partial" if covered else "missing"
        items.append({"key": f"sacl:{share['id']}", "group": "Audit des dossiers", "label": share["name"],
                      "detail": share["path"] + (f" — {info['error']}" if info.get("error") else ""), "status": status,
                      "current": _rights_label(covered) if covered else "aucun audit",
                      "expected": _rights_label(want), "command": sacl_command(share["path"], include_reads)})
    return items


def _as_list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


def _rights_label(mask):
    names = [(RIGHT_READ, "lecture"), (0x2, "écriture"), (0x10000, "suppression"), (0x40000, "droits"),
             (0x80000, "propriétaire")]
    return ", ".join(n for b, n in names if mask & b) or "—"


def sacl_command(path, include_reads=True):
    rights = "WriteData,AppendData,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership" + \
        (",ReadData" if include_reads else "")
    return (f"$i = Get-Item -LiteralPath {system.ps_quote(path)}; $a = $i.GetAccessControl('Audit'); "
            f"$a.AddAuditRule((New-Object System.Security.AccessControl.FileSystemAuditRule("
            f"(New-Object System.Security.Principal.SecurityIdentifier('{EVERYONE}')), '{rights}', "
            "'ContainerInherit,ObjectInherit', 'None', 'Success,Failure'))); $i.SetAccessControl($a)")


def samba_items(shares):
    configured = {s["name"].lower(): s for s in samba_shares()}
    text = ""
    try:
        text = system.run(["testparm", "-s", "--suppress-prompt"], timeout=30, check=False)
    except system.CommandError:
        pass
    audited, current = set(), None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].lower()
        elif current and line.lower().startswith("vfs objects") and "full_audit" in line:
            audited.add(current)
    items = []
    for share in shares:
        match = [n for n, s in configured.items() if s.get("path") and share["path"].startswith(s["path"].rstrip("/"))]
        name = match[0] if match else share["name"]
        ok = any(m in audited for m in match)
        items.append({"key": f"samba:{share['id']}", "group": "Audit Samba", "label": share["name"],
                      "detail": f"partage Samba « {name} »" if match else "aucun partage Samba trouvé pour ce dossier",
                      "status": "ok" if ok else "missing", "current": "full_audit actif" if ok else "non audité",
                      "expected": "vfs objects = full_audit", "command": SAMBA_SNIPPET.replace("{share}", name)})
    return items


def check(shares, include_reads=True):
    """Liste des réglages à vérifier, avec leur état et la commande de correction."""
    if not system.IS_WINDOWS:
        return {"platform": "posix", "admin": None, "items": samba_items(shares), "snippet": SAMBA_SNIPPET}
    data = system.powershell(windows_check_script([s["path"] for s in shares]), timeout=180) or {}
    return {"platform": "windows", "admin": bool(data.get("admin")),
            "items": evaluate_windows(data, shares, include_reads)}


def apply(keys, shares, include_reads=True):
    """Applique les corrections demandées (Windows). Renvoie [{key, ok, error}]."""
    if not system.IS_WINDOWS:
        raise system.CommandError("Sous Linux, ajoutez le module full_audit dans smb.conf (extrait fourni).")
    by_id = {str(s["id"]): s for s in shares}
    subs = {k: (g, s, f) for k, g, _l, _w, s, f in SUBCATEGORIES}
    out = []
    for key in keys:
        try:
            kind, _, ref = key.partition(":")
            if kind == "policy" and ref in subs:
                guid, succ, fail = subs[ref]
                cmd = ["auditpol", "/set", f"/subcategory:{guid}"]
                if succ:
                    cmd.append("/success:enable")
                if fail:
                    cmd.append("/failure:enable")
                system.run(cmd, timeout=60)
            elif key == "log_size":
                system.run(["wevtutil", "sl", "Security", f"/ms:{LOG_MIN_BYTES}"], timeout=60)
            elif kind == "sacl" and ref in by_id:
                system.powershell(sacl_command(by_id[ref]["path"], include_reads) + "; '{}'", timeout=300)
            else:
                raise ValueError("réglage inconnu")
            out.append({"key": key, "ok": True})
        except (system.CommandError, ValueError) as exc:
            out.append({"key": key, "ok": False, "error": str(exc)})
    return out


def script(shares, include_reads=True):
    """Script PowerShell complet (à relire, à exécuter en administrateur ou à reporter dans une GPO)."""
    lines = ["# VigieFS — préparation de l'audit des accès aux fichiers",
             "# À exécuter en administrateur. Avec une GPO d'audit avancé dans le domaine, reportez-y ces réglages.",
             "", "# 1. Stratégie d'audit avancé"]
    for _k, guid, label, why, succ, fail in SUBCATEGORIES:
        lines.append(f"# {label} — {why}")
        lines.append(f'auditpol /set /subcategory:"{guid}"' + (" /success:enable" if succ else "")
                     + (" /failure:enable" if fail else ""))
    lines += ["", "# 2. Journal Sécurité d'au moins 1 Go", f"wevtutil sl Security /ms:{LOG_MIN_BYTES}", "",
              "# 3. Audit des dossiers supervisés (règle « Tout le monde », héritée par les sous-dossiers et fichiers)"]
    for s in shares:
        lines.append(f"# {s['name']}")
        lines.append(sacl_command(s["path"], include_reads))
    return "\r\n".join(lines) + "\r\n"
