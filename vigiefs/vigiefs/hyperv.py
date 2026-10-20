"""Virtualisation Hyper-V : VigieFS installé sur l'hôte voit les VM, leurs disques et leurs données.

- Inventaire : hôte, VM (état, OS, adresses, mémoire), disques virtuels (type, taille du fichier, taille maximale,
  chaîne de différenciation, fragmentation, dernière écriture), points de contrôle, volumes de l'hôte, disques
  virtuels orphelins (rattachés à aucune VM).
- Constats : point de contrôle oublié, volume de l'hôte surréservé par les disques dynamiques ou presque plein,
  disque dynamique presque plein, VM éteinte depuis longtemps, disque fragmenté, services d'intégration absents,
  données d'une VM non supervisées, espace récupérable par compactage, destination d'archive mal placée.
- Données des VM : partages SMB et volumes de chaque VM (CIM, sinon « net view »), rattachement aux dossiers
  supervisés (chemin UNC ou lien manuel), analyse en lecture seule du contenu d'une VM éteinte, compactage.
- Cohérence : les dossiers de stockage des VM deviennent des « racines protégées » (jamais archivées, nettoyées,
  dédoublonnées ni réorganisées), et l'espace libéré dans une VM n'est réellement rendu à l'hôte qu'après
  compactage de son disque dynamique : VigieFS le dit et le propose.
"""

import json
import ntpath
import re
import time

from . import system

# Partages techniques (contrôleur de domaine, autorité de certification, impression) : pas des données à superviser.
SYSTEM_SHARES = {"sysvol", "netlogon", "certenroll", "print$", "wsuscontent", "updateserviceswebservices", "users"}

INVENTORY_SCRIPT = r"""
$r = @{ available = $false; host = $null; vms = @(); volumes = @(); orphans = @(); error = $null }
if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
  $r.error = "Le module Hyper-V n'est pas disponible sur ce serveur"; $r | ConvertTo-Json -Compress; return }
$r.available = $true
function Ts($d) { if ($d) { ([DateTimeOffset]$d).ToUnixTimeSeconds() } else { $null } }
try {
  $h = Get-VMHost -ErrorAction Stop
  $r.host = @{ name = $env:COMPUTERNAME; vhd_path = $h.VirtualHardDiskPath; vm_path = $h.VirtualMachinePath;
    cpus = $h.LogicalProcessorCount; memory = [int64]$h.MemoryCapacity }
} catch { $r.error = $_.Exception.Message }
$kvp = @{}
try {
  foreach ($c in Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_KvpExchangeComponent -ErrorAction Stop) {
    $items = @{}
    foreach ($x in $c.GuestIntrinsicExchangeItems) {
      $xml = [xml]$x
      $n = ($xml.INSTANCE.PROPERTY | Where-Object { $_.NAME -eq 'Name' }).VALUE
      $v = ($xml.INSTANCE.PROPERTY | Where-Object { $_.NAME -eq 'Data' }).VALUE
      if ($n) { $items[$n] = $v }
    }
    $kvp[[string]$c.SystemName] = $items
  }
} catch {}
$known = @{}
$r.vms = @(foreach ($vm in Get-VM) {
  $disks = @(foreach ($d in Get-VMHardDiskDrive -VM $vm) {
    $info = @{}
    if ($d.Path) {
      $known[$d.Path.ToLower()] = $true
      try {
        $v = Get-VHD -Path $d.Path -ErrorAction Stop
        $info = @{ format = [string]$v.VhdFormat; type = [string]$v.VhdType; file_size = [int64]$v.FileSize;
          size = [int64]$v.Size; min_size = [int64]$v.MinimumSize; frag = $v.FragmentationPercentage; parent = $v.ParentPath;
          mtime = (Ts (Get-Item -LiteralPath $d.Path).LastWriteTime) }
        $chain = @(); $p = $v.ParentPath
        while ($p) {
          $known[$p.ToLower()] = $true
          try { $pv = Get-VHD -Path $p -ErrorAction Stop; $chain += @{ path = $p; file_size = [int64]$pv.FileSize }; $p = $pv.ParentPath }
          catch { $chain += @{ path = $p; error = $_.Exception.Message }; $p = $null }
        }
        $info.chain = $chain
      } catch { $info = @{ error = $_.Exception.Message } }
    }
    @{ path = $d.Path; controller = [string]$d.ControllerType; vhd = $info }
  })
  $snaps = @(Get-VMSnapshot -VM $vm -ErrorAction SilentlyContinue | ForEach-Object {
    @{ name = $_.Name; created = (Ts $_.CreationTime); type = [string]$_.SnapshotType } })
  $nics = @(Get-VMNetworkAdapter -VM $vm -ErrorAction SilentlyContinue | ForEach-Object {
    @{ switch = $_.SwitchName; ips = @($_.IPAddresses); mac = $_.MacAddress } })
  $g = $kvp[[string]$vm.Id]
  @{ id = [string]$vm.Id; name = $vm.Name; state = [string]$vm.State; uptime = [int64]$vm.Uptime.TotalSeconds;
     generation = $vm.Generation; cpus = $vm.ProcessorCount; memory = [int64]$vm.MemoryAssigned;
     memory_startup = [int64]$vm.MemoryStartup; path = $vm.Path; config = $vm.ConfigurationLocation;
     snapshot_path = $vm.SnapshotFileLocation; notes = $vm.Notes; created = (Ts $vm.CreationTime);
     replication = [string]$vm.ReplicationState; heartbeat = [string]$vm.Heartbeat;
     guest = @{ fqdn = $g.FullyQualifiedDomainName; os = $g.OSName; os_version = $g.OSVersion; ips = $g.NetworkAddressIPv4 };
     disks = $disks; snapshots = $snaps; nics = $nics }
})
$roots = @{}
foreach ($vm in $r.vms) {
  if ($vm.path) { $roots[[IO.Path]::GetPathRoot($vm.path)] = $true }
  foreach ($d in $vm.disks) { if ($d.path) { $roots[[IO.Path]::GetPathRoot($d.path)] = $true } }
}
$r.volumes = @(foreach ($q in $roots.Keys) {
  try { $dr = New-Object IO.DriveInfo($q); @{ root = $q; size = [int64]$dr.TotalSize; free = [int64]$dr.AvailableFreeSpace; label = $dr.VolumeLabel } } catch {} })
$dirs = @{}
if ($r.host -and $r.host.vhd_path) { $dirs[$r.host.vhd_path] = $true }
foreach ($vm in $r.vms) { foreach ($d in $vm.disks) { if ($d.path) { $dirs[[IO.Path]::GetDirectoryName($d.path)] = $true } } }
$r.orphans = @(foreach ($dir in $dirs.Keys) {
  if (Test-Path -LiteralPath $dir) {
    Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Extension -in '.vhd', '.vhdx', '.avhd', '.avhdx' -and -not $known.ContainsKey($_.FullName.ToLower()) } |
      ForEach-Object { @{ path = $_.FullName; size = [int64]$_.Length; mtime = (Ts $_.LastWriteTime) } }
  } })
$r | ConvertTo-Json -Depth 7 -Compress
"""


def _list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


def inventory():
    """Inventaire brut de l'hôte (Windows avec le rôle Hyper-V)."""
    if not system.IS_WINDOWS:
        return {"available": False, "error": "Hyper-V : VigieFS doit être installé sur l'hôte Windows Hyper-V"}
    return system.powershell(INVENTORY_SCRIPT, timeout=900) or {"available": False, "error": "réponse vide"}


def normalize(raw, now=None):
    """Inventaire brut → modèle VigieFS (listes garanties, totaux calculés, cibles réseau de chaque VM)."""
    now = now or time.time()
    inv = {"available": bool(raw.get("available")), "error": raw.get("error"), "host": raw.get("host") or {},
           "collected_at": now, "vms": [], "volumes": [], "orphans": []}
    for vm in _list(raw.get("vms")):
        disks = []
        for d in _list(vm.get("disks")):
            v = d.get("vhd") or {}
            chain = _list(v.get("chain"))
            disks.append({"path": d.get("path"), "controller": d.get("controller"), "format": v.get("format"),
                          "type": v.get("type"), "file_size": int(v.get("file_size") or 0), "size": int(v.get("size") or 0),
                          "min_size": int(v.get("min_size") or 0), "frag": v.get("frag"), "parent": v.get("parent"),
                          "mtime": v.get("mtime"), "chain": chain, "error": v.get("error"),
                          "chain_size": sum(int(c.get("file_size") or 0) for c in chain)})
        snaps = sorted(_list(vm.get("snapshots")), key=lambda s: s.get("created") or 0)
        guest = vm.get("guest") or {}
        ips = []
        for n in _list(vm.get("nics")):
            ips += [ip for ip in _list(n.get("ips")) if ip and ":" not in ip]
        for ip in str(guest.get("ips") or "").split(";"):
            if ip and ":" not in ip and ip not in ips:
                ips.append(ip)
        last_write = max((d["mtime"] or 0 for d in disks), default=0) or None
        targets = [t for t in dict.fromkeys([guest.get("fqdn"), vm.get("name")] + ips) if t]
        inv["vms"].append({
            "id": vm.get("id"), "name": vm.get("name"), "state": vm.get("state") or "Unknown",
            "running": (vm.get("state") or "").lower() == "running", "uptime": vm.get("uptime"),
            "generation": vm.get("generation"), "cpus": vm.get("cpus"), "memory": vm.get("memory"),
            "memory_startup": vm.get("memory_startup"), "path": vm.get("path"), "config": vm.get("config"),
            "snapshot_path": vm.get("snapshot_path"), "notes": vm.get("notes"), "created": vm.get("created"),
            "replication": vm.get("replication"), "heartbeat": vm.get("heartbeat"),
            "os": guest.get("os"), "os_version": guest.get("os_version"), "fqdn": guest.get("fqdn"), "ips": ips,
            "targets": targets, "disks": disks, "snapshots": snaps,
            "file_size": sum(d["file_size"] + d["chain_size"] for d in disks),
            "max_size": sum(d["size"] for d in disks), "last_write": last_write,
            "oldest_snapshot": snaps[0].get("created") if snaps else None})
    inv["vms"].sort(key=lambda v: (not v["running"], (v["name"] or "").lower()))
    for vol in _list(raw.get("volumes")):
        root = (vol.get("root") or "").upper()
        on = [d for vm in inv["vms"] for d in vm["disks"] if (d["path"] or "").upper().startswith(root)]
        vm_files = sum(d["file_size"] + d["chain_size"] for d in on)
        growth = sum(max(0, d["size"] - d["file_size"]) for d in on if (d["type"] or "").lower() in ("dynamic", "differencing"))
        inv["volumes"].append({"root": vol.get("root"), "label": vol.get("label"), "size": int(vol.get("size") or 0),
                               "free": int(vol.get("free") or 0), "vm_files": vm_files, "potential_growth": growth,
                               "disks": len(on)})
    inv["orphans"] = sorted(_list(raw.get("orphans")), key=lambda o: -(o.get("size") or 0))
    return inv


def protected_paths(inv):
    """Dossiers de stockage des VM (configuration, disques, points de contrôle) à ne jamais toucher."""
    out = set()
    host = inv.get("host") or {}
    for p in (host.get("vhd_path"), host.get("vm_path")):
        if p:
            out.add(p.rstrip("\\"))
    for vm in inv.get("vms", []):
        for p in (vm.get("path"), vm.get("config"), vm.get("snapshot_path")):
            if p:
                out.add(p.rstrip("\\"))
        for d in vm.get("disks", []):
            if d.get("path"):
                out.add(ntpath.dirname(d["path"]))
            for c in d.get("chain", []):
                if c.get("path"):
                    out.add(ntpath.dirname(c["path"]))
    return sorted(p for p in out if len(p) > 3)       # jamais un volume entier (« D:\ »)


# ================================================================ données des VM
def _unc_host(path):
    m = re.match(r"^\\\\([^\\]+)\\", path or "")
    return m.group(1).lower() if m else None


def links(inv, shares, manual=None):
    """{id de VM: [id de dossier supervisé]} : chemin UNC vers la VM (nom, nom complet, adresse) ou lien manuel."""
    out = {vm["id"]: [] for vm in inv.get("vms", [])}
    names = {}
    for vm in inv.get("vms", []):
        for t in vm.get("targets", []):
            names[t.lower()] = vm["id"]
            names[t.lower().split(".")[0]] = vm["id"]
    for s in shares:
        host = _unc_host(s.get("path")) or _unc_host(s.get("access_path"))
        vid = names.get(host) or (names.get(host.split(".")[0]) if host else None)
        if vid:
            out[vid].append(s["id"])
    for vid, sid in manual or []:
        if vid in out and sid not in out[vid]:
            out[vid].append(sid)
    return out


def discover_script(target):
    t = system.ps_quote(target)
    return f"""
$res = @{{ target = {t}; shares = @(); volumes = @(); method = $null; error = $null }}
try {{
  $o = New-CimSessionOption -Protocol Wsman
  $s = New-CimSession -ComputerName {t} -OperationTimeoutSec 20 -SessionOption $o -ErrorAction Stop
  try {{
    $res.shares = @(Get-SmbShare -CimSession $s -ErrorAction Stop | Where-Object {{ -not $_.Special }} | ForEach-Object {{
      @{{ name = $_.Name; path = $_.Path; description = $_.Description }} }})
    $res.volumes = @(Get-CimInstance -CimSession $s -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop |
      ForEach-Object {{ @{{ drive = $_.DeviceID; size = [int64]$_.Size; free = [int64]$_.FreeSpace; label = $_.VolumeName }} }})
    $res.method = 'cim'
  }} finally {{ Remove-CimSession $s }}
}} catch {{
  $res.error = $_.Exception.Message
  $res.netview = (net view ('\\\\' + {t}) /all 2>&1 | Out-String)
}}
$res | ConvertTo-Json -Depth 4 -Compress
"""


def parse_net_view(text):
    """Partages disque de « net view \\\\machine /all » (Windows français ou anglais)."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^(.+?)\s{2,}(Disk|Disque)\b\s*(.*)$", line.rstrip())
        if m and not m.group(1).strip().endswith("$") and m.group(1).strip().lower() not in SYSTEM_SHARES:
            out.append({"name": m.group(1).strip(), "path": None, "description": m.group(3).strip()})
    return out


def discover(vm):
    """Partages et volumes d'une VM en marche → {target, shares, volumes, method, error, checked_at}."""
    last = None
    for target in vm.get("targets") or []:
        try:
            res = system.powershell(discover_script(target), timeout=120) or {}
        except system.CommandError as exc:
            last = str(exc)
            continue
        shares = [s for s in _list(res.get("shares")) if s.get("name") and not s["name"].endswith("$")
                  and s["name"].lower() not in SYSTEM_SHARES]
        if not shares and res.get("netview"):
            shares = parse_net_view(res["netview"])
            res["method"] = "net view" if shares else None
        if res.get("method"):
            return {"target": target, "shares": shares, "volumes": _list(res.get("volumes")), "method": res["method"],
                    "error": None, "checked_at": time.time()}
        last = res.get("error") or last
    return {"target": None, "shares": [], "volumes": [], "method": None, "checked_at": time.time(),
            "error": last or "aucune adresse connue (services d'intégration de la VM ?)"}


# ================================================================ VM éteinte : contenu, compactage
def mount_script(path):
    return f"""
$p = {system.ps_quote(path)}
$m = Mount-VHD -Path $p -ReadOnly -NoDriveLetter -Passthru -ErrorAction Stop
$disk = $m | Get-Disk
$vols = @($disk | Get-Partition | Get-Volume -ErrorAction SilentlyContinue | Where-Object {{ $_.FileSystem }} | ForEach-Object {{
  @{{ path = $_.Path; label = $_.FileSystemLabel; fs = $_.FileSystem; size = [int64]$_.Size; free = [int64]$_.SizeRemaining }} }})
@{{ disk = $disk.Number; offline = [bool]$disk.IsOffline; reason = [string]$disk.OfflineReason; volumes = $vols }} |
  ConvertTo-Json -Depth 4 -Compress
"""


def scan_offline(vm, ctx=None, skip_roots=()):
    """Monte en lecture seule les disques d'une VM éteinte, analyse chaque volume, démonte toujours."""
    from .scanner import scan_tree
    if vm.get("running") or (vm.get("state") or "").lower() not in ("off", "saved"):
        raise ValueError("La VM doit être éteinte : un disque en cours d'utilisation ne peut pas être monté.")
    out = []
    for d in vm.get("disks", []):
        if not d.get("path"):
            continue
        res = {"disk": d["path"], "volumes": [], "error": None}
        try:
            info = system.powershell(mount_script(d["path"]), timeout=300) or {}
            if info.get("offline"):
                res["error"] = f"disque hors ligne après montage ({info.get('reason') or 'collision de signature'})"
            for vol in _list(info.get("volumes")):
                if ctx:
                    ctx.progress(phase=f"analyse de {d['path']}")
                rep = scan_tree(vol["path"], stale_days=730, skip_roots=skip_roots)
                rep.pop("_folder_stats", None)
                rep.pop("_owners", None)
                res["volumes"].append({**vol, "report": {k: rep.get(k) for k in (
                    "total_size", "file_count", "dir_count", "stale_count", "stale_size", "by_extension", "ages",
                    "top_folders", "largest", "newest_mtime")}})
        except (system.CommandError, OSError) as exc:
            res["error"] = str(exc)
        finally:
            try:
                system.powershell(f"Dismount-VHD -Path {system.ps_quote(d['path'])} -ErrorAction SilentlyContinue; '{{}}'",
                                  timeout=120)
            except system.CommandError:
                pass
        out.append(res)
    return {"vm": vm["name"], "disks": out, "scanned_at": time.time()}


def compact(vm, ctx=None):
    """Compactage (Optimize-VHD -Mode Full) des disques dynamiques d'une VM éteinte : rend à l'hôte l'espace libéré
    dans la VM (archivage, nettoyage)."""
    if (vm.get("state") or "").lower() != "off":
        raise ValueError("La VM doit être arrêtée (pas en pause ni enregistrée) pour compacter ses disques.")
    out = []
    for d in vm.get("disks", []):
        if (d.get("type") or "").lower() != "dynamic" or not d.get("path"):
            continue
        if ctx:
            ctx.progress(phase=f"compactage de {d['path']}")
        p = system.ps_quote(d["path"])
        try:
            res = system.powershell(
                f"$b = (Get-Item -LiteralPath {p}).Length; Optimize-VHD -Path {p} -Mode Full -ErrorAction Stop; "
                f"@{{ before = $b; after = (Get-Item -LiteralPath {p}).Length }} | ConvertTo-Json -Compress", timeout=7200)
            out.append({"disk": d["path"], **(res or {})})
        except system.CommandError as exc:
            out.append({"disk": d["path"], "error": str(exc)})
    return {"vm": vm["name"], "disks": out, "freed": sum((r.get("before") or 0) - (r.get("after") or 0) for r in out
                                                         if not r.get("error"))}


# ================================================================ constats
def analyze(inv, cfg, guests=None, vm_links=None, archive_root=None, now=None):
    """Constats sur l'hôte et les VM → [{key, severity, title, message, vm, details}]."""
    now = now or time.time()
    guests, vm_links = guests or {}, vm_links or {}
    out = []

    def add(key, severity, title, message, vm=None, **details):
        out.append({"key": key, "severity": severity, "title": title, "message": message,
                    "vm": vm["name"] if vm else None, "vm_id": vm["id"] if vm else None, "details": details})

    gb = 1024 ** 3
    cp_days = float(cfg.get("checkpoint_days", 7))
    for vm in inv.get("vms", []):
        if vm["snapshots"]:
            age = (now - (vm["oldest_snapshot"] or now)) / 86400
            avhd = sum(d["file_size"] for d in vm["disks"] if (d["type"] or "").lower() == "differencing")
            if age >= cp_days:
                add(f"checkpoint:{vm['id']}", "critical" if age >= 4 * cp_days else "warning",
                    f"Point de contrôle oublié — {vm['name']}",
                    f"{len(vm['snapshots'])} point(s) de contrôle, le plus ancien a {age:.0f} jour(s) ; les écritures "
                    f"s'accumulent dans les fichiers de différenciation ({_fmt(avhd)}) et dégradent les performances. "
                    "Supprimez-les (fusion) ou vérifiez qu'un logiciel de sauvegarde ne les a pas laissés.", vm,
                    snapshots=[s.get("name") for s in vm["snapshots"]][:10], age_days=round(age), avhd=avhd)
        for d in vm["disks"]:
            if (d["type"] or "").lower() == "dynamic" and d["size"] and d["file_size"] >= d["size"] * float(
                    cfg.get("dynamic_full_pct", 90)) / 100:
                add(f"dynfull:{vm['id']}:{d['path']}", "warning", f"Disque presque plein — {vm['name']}",
                    f"Le disque {ntpath.basename(d['path'] or '')} occupe {_fmt(d['file_size'])} sur {_fmt(d['size'])} "
                    "maximum : la VM va manquer de place. Agrandissez-le ou libérez de l'espace dans la VM.", vm,
                    disk=d["path"])
            if d.get("frag") and d["frag"] >= float(cfg.get("fragmentation_pct", 30)):
                add(f"frag:{vm['id']}:{d['path']}", "info", f"Disque virtuel fragmenté — {vm['name']}",
                    f"{ntpath.basename(d['path'] or '')} est fragmenté à {d['frag']} %.", vm, disk=d["path"])
        idle = (now - vm["last_write"]) / 86400 if vm["last_write"] else None
        if not vm["running"] and idle is not None and idle >= float(cfg.get("inactive_days", 90)):
            add(f"inactive:{vm['id']}", "info", f"VM inactive — {vm['name']}",
                f"Éteinte, aucune écriture sur ses disques depuis {idle:.0f} jours ({_fmt(vm['file_size'])} sur l'hôte). "
                "Analysez son contenu avant de l'archiver ou de la supprimer.", vm, idle_days=round(idle),
                size=vm["file_size"])
        if vm["running"] and not (vm.get("fqdn") or vm.get("os")):
            add(f"kvp:{vm['id']}", "info", f"Informations de la VM indisponibles — {vm['name']}",
                "Les services d'intégration (échange de données) ne répondent pas : nom, système et adresses de la VM "
                "inconnus, la découverte de ses données est limitée.", vm)
        g = guests.get(vm["id"]) or {}
        shares = g.get("shares") or []
        if shares and not vm_links.get(vm["id"]):
            add(f"unsupervised:{vm['id']}", "info", f"Données non supervisées — {vm['name']}",
                f"{len(shares)} partage(s) découvert(s) ({', '.join(s['name'] for s in shares[:5])}) sans dossier supervisé "
                "correspondant : ni quotas, ni archivage, ni détection pour ces données.", vm,
                shares=[s["name"] for s in shares])
        used = sum((v.get("size") or 0) - (v.get("free") or 0) for v in g.get("volumes") or [])
        dyn = sum(d["file_size"] + d["chain_size"] for d in vm["disks"] if (d["type"] or "").lower() in ("dynamic", "differencing"))
        if used and dyn and dyn - used >= 20 * gb and dyn - used >= 0.3 * dyn:
            add(f"reclaim:{vm['id']}", "info", f"Espace récupérable sur l'hôte — {vm['name']}",
                f"Ses disques dynamiques occupent {_fmt(dyn)} sur l'hôte pour {_fmt(used)} de données dans la VM : environ "
                f"{_fmt(dyn - used)} récupérables par compactage (VM arrêtée), par exemple après un archivage.", vm,
                reclaimable=dyn - used)
    for vol in inv.get("volumes", []):
        if not vol["size"]:
            continue
        free_pct = vol["free"] * 100 / vol["size"]
        if free_pct < 10:
            add(f"hostfree:{vol['root']}", "critical" if free_pct < 5 else "warning",
                f"Volume de l'hôte presque plein — {vol['root']}",
                f"Plus que {_fmt(vol['free'])} libres ({free_pct:.0f} %) sur le volume qui porte {vol['disks']} disque(s) "
                "virtuel(s) : un volume plein met les VM en pause critique.", None, root=vol["root"])
        if vol["potential_growth"] > vol["free"]:
            add(f"overcommit:{vol['root']}", "warning", f"Surréservation des disques dynamiques — {vol['root']}",
                f"Les disques dynamiques peuvent encore grandir de {_fmt(vol['potential_growth'])} alors qu'il ne reste que "
                f"{_fmt(vol['free'])} sur {vol['root']} : si les VM se remplissent, le volume saturera.", None,
                root=vol["root"], growth=vol["potential_growth"], free=vol["free"])
        if archive_root and archive_root.upper().startswith((vol["root"] or "?").upper()):
            add(f"archive-volume:{vol['root']}", "info", "Archives sur le volume des VM",
                f"La destination d'archive ({archive_root}) est sur {vol['root']}, comme les disques des VM : archiver les "
                "données d'une VM les déplace sur le même volume sans y libérer de place. Préférez un NAS ou un autre disque.",
                None, root=vol["root"])
    if inv.get("orphans"):
        total = sum(o.get("size") or 0 for o in inv["orphans"])
        add("orphans", "info", "Disques virtuels orphelins",
            f"{len(inv['orphans'])} disque(s) virtuel(s) ({_fmt(total)}) ne sont rattachés à aucune VM : anciennes VM "
            "supprimées, copies, modèles ? Vérifiez puis archivez ou supprimez-les depuis Hyper-V.", None,
            files=[o["path"] for o in inv["orphans"][:20]], size=total)
    return out


def _fmt(n):
    from .engine import fmt_bytes
    return fmt_bytes(n)


# ================================================================ service (planification, historique, alertes)
class HyperVService:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.alerts = engine.alerts
        self.running = False
        self.last_run = 0

    def settings(self):
        from .config import merged_setting
        return merged_setting(self.db, "hyperv")

    def latest(self):
        row = self.db.query_one("SELECT * FROM hyperv_snapshots ORDER BY id DESC LIMIT 1")
        if not row:
            return None
        row["data"] = json.loads(row["data"])
        row["findings"] = json.loads(row["findings"] or "[]")
        return row

    def guests(self):
        return {r["vm_id"]: json.loads(r["data"]) for r in self.db.query("SELECT * FROM hyperv_guests")}

    def manual_links(self):
        return [(r["vm_id"], r["share_id"]) for r in self.db.query("SELECT vm_id, share_id FROM hyperv_links")]

    def due(self, now):
        cfg = self.settings()
        return (system.IS_WINDOWS and cfg.get("enabled", True) and not cfg.get("demo") and not self.running
                and now - self.last_run >= float(cfg.get("interval_h", 6)) * 3600)

    def refresh(self, ctx=None, with_guests=None, inventory_fn=None, discover_fn=None):
        """Inventaire, découverte des données des VM en marche, constats et alertes."""
        cfg = self.settings()
        if cfg.get("demo"):
            self.running = False
            raise ValueError("Inventaire de démonstration : rien à actualiser sur ce poste.")
        self.running = True
        try:
            self.last_run = time.time()
            if ctx:
                ctx.progress(phase="inventaire des VM")
            inv = normalize((inventory_fn or inventory)())
            if not inv["available"]:
                self.db.set_setting("hyperv_state", {"available": False, "error": inv.get("error"),
                                                     "checked_at": time.time()})
                return {"available": False, "error": inv.get("error")}
            if cfg.get("discover", True) if with_guests is None else with_guests:
                for vm in inv["vms"]:
                    if vm["running"]:
                        if ctx:
                            ctx.progress(phase=f"données de {vm['name']}")
                        self.save_guest(vm["id"], vm["name"], (discover_fn or discover)(vm))
            return self.store(inv)
        finally:
            self.running = False

    def save_guest(self, vm_id, name, data):
        self.db.execute("INSERT INTO hyperv_guests(vm_id, name, checked_at, data) VALUES(?,?,?,?) ON CONFLICT(vm_id) "
                        "DO UPDATE SET name=excluded.name, checked_at=excluded.checked_at, data=excluded.data",
                        (vm_id, name, time.time(), json.dumps(data, ensure_ascii=False, default=str)))

    def store(self, inv):
        """Enregistre l'inventaire, met à jour les racines protégées, lève / résout les alertes."""
        from .config import merged_setting
        shares = self.engine.shares()
        vm_links = links(inv, shares, self.manual_links())
        archive_root = merged_setting(self.db, "archive").get("root")
        findings = analyze(inv, self.settings(), self.guests(), vm_links, archive_root)
        self.db.execute("INSERT INTO hyperv_snapshots(created_at, data, findings) VALUES(?,?,?)",
                        (time.time(), json.dumps(inv, ensure_ascii=False, default=str),
                         json.dumps(findings, ensure_ascii=False, default=str)))
        self.db.execute("DELETE FROM hyperv_snapshots WHERE id NOT IN (SELECT id FROM hyperv_snapshots ORDER BY id DESC "
                        "LIMIT 120)")
        self.db.set_setting("vm_protected_paths", protected_paths(inv))
        self.db.set_setting("hyperv_state", {"available": True, "checked_at": time.time(), "vms": len(inv["vms"])})
        active = set()
        for f in findings:
            fp = f"hyperv:{f['key']}"
            active.add(fp)
            self.alerts.raise_alert(fp, f["severity"], "virtualization", f["title"], f["message"],
                                    details={"vm": f["vm"], **f["details"]}, notify=f["severity"] != "info")
        for r in self.db.query("SELECT fingerprint FROM alerts WHERE fingerprint LIKE 'hyperv:%' AND status IN "
                               "('open','acknowledged')"):
            if r["fingerprint"] not in active:
                self.alerts.resolve(r["fingerprint"])
        return {"available": True, "vms": len(inv["vms"]), "findings": len(findings)}
