"""Activité par utilisateur : qui lit, modifie, supprime quoi, depuis quel poste.

Sources (toutes facultatives, combinables) :
- journal Sécurité Windows : audit du système de fichiers (4663, 4656 en échec), des partages (5140, 5145 en
  échec), connexions (4624, 4625), verrouillages (4740), droits (4670), stratégie d'audit (4719, 4907),
  effacement du journal (1102), comptes et groupes (4720, 4726, 4728, 4732, 4756). Lecture continue avec un
  signet : rien n'est perdu ni lu deux fois, même après un redémarrage ;
- module d'audit Samba « full_audit » (Linux, NAS) ;
- échantillonnage des sessions et fichiers ouverts SMB : utile même sans audit ;
- import de journaux exportés (.evtx sous Windows, .xml de l'Observateur d'événements, journal Samba).

Chaque événement est rattaché à un dossier supervisé, compté par heure et par utilisateur (agrégats conservés
longtemps : lignes de base des comportements) et alimente le profil de l'utilisateur (postes, dossiers habituels).
"""

import fnmatch
import getpass
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from . import smb, system
from .config import merged_setting
from .rules import plausible_mtime

log = logging.getLogger("vigiefs.activity")

ACTION_LABEL = {
    "read": "Lecture", "open": "Ouverture", "write": "Modification", "create": "Création", "delete": "Suppression",
    "rename": "Renommage / déplacement", "perm": "Droits modifiés", "owner": "Propriétaire modifié",
    "denied": "Accès refusé", "share": "Connexion au partage", "logon": "Ouverture de session",
    "logon_fail": "Échec de connexion", "lockout": "Compte verrouillé", "audit_cleared": "Journal de sécurité effacé",
    "policy": "Audit modifié", "account": "Compte créé / supprimé", "group_add": "Ajout à un groupe",
}
FILE_ACTIONS = {"read", "open", "write", "create", "delete", "rename", "perm", "owner", "denied"}
COUNTERS = ["reads", "writes", "creates", "deletes", "renames", "perms", "denied", "logons", "logon_fails"]
COUNTER_OF = {"read": "reads", "open": "reads", "write": "writes", "create": "creates", "delete": "deletes",
              "rename": "renames", "perm": "perms", "owner": "perms", "denied": "denied", "logon": "logons",
              "share": "logons", "logon_fail": "logon_fails", "lockout": "logon_fails"}
LOGON_TYPES = {"2": "interactive", "3": "réseau", "7": "déverrouillage", "10": "bureau à distance",
               "11": "hors connexion (cache)"}
LOGON_FAIL_REASON = {
    "0xc000006a": "mot de passe incorrect", "0xc0000064": "compte inconnu", "0xc0000234": "compte verrouillé",
    "0xc0000072": "compte désactivé", "0xc000006f": "hors des heures autorisées", "0xc0000070": "poste non autorisé",
    "0xc0000071": "mot de passe expiré", "0xc0000193": "compte expiré", "0xc0000224": "changement de mot de passe requis",
    "0xc000015b": "type de connexion refusé", "0xc000006d": "identifiants incorrects",
}
WINDOWS_EVENT_IDS = (4663, 4660, 5140, 5145, 4624, 4625, 4740, 4670, 4719, 4907, 1102, 4720, 4726, 4728, 4732, 4756)
DEDUP_SEC = {"read": 60, "open": 60, "write": 30, "create": 30, "logon": 900, "share": 900}

# Masques d'accès NTFS (4663, 4656, 5145)
READ_DATA, WRITE_DATA, APPEND_DATA = 0x1, 0x2, 0x4
DELETE, WRITE_DAC, WRITE_OWNER = 0x10000, 0x40000, 0x80000


# ================================================================ utilisateurs
def user_key(raw):
    """Clé d'un compte : « domaine\\identifiant » en minuscules ; l'UPN « id@domaine » devient « id »."""
    s = str(raw or "").strip().strip("\0")
    if not s or s in ("-", "?"):
        return ""
    if "@" in s and "\\" not in s:
        s = s.split("@", 1)[0]
    return s.lower()


def login_of(key):
    return key.split("\\")[-1]


def _join_user(domain, name):
    name = str(name or "").strip()
    if not name or name == "-":
        return ""
    domain = str(domain or "").strip()
    return f"{domain}\\{name}" if domain and domain != "-" and "\\" not in name else name


class Exclusions:
    """Comptes ignorés : machines, identités système, comptes de service, sauvegardes, compte de VigieFS."""

    def __init__(self, patterns, service_users=()):
        self.patterns = [str(p).strip().lower() for p in patterns or [] if str(p).strip()]
        self.users = {u.lower() for u in service_users}
        try:
            me = getpass.getuser().lower()
            if me and me not in ("system", "root"):
                self.patterns.append(me)
        except Exception:  # noqa: BLE001
            pass

    def __call__(self, key):
        if not key or key in self.users:
            return True
        login = login_of(key)
        return any(fnmatch.fnmatchcase(key, p) or fnmatch.fnmatchcase(login, p) for p in self.patterns)


# ================================================================ rattachement aux dossiers supervisés
def _strip_prefix(p):
    p = str(p or "").strip()
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    for pre in ("\\\\?\\", "\\??\\", "\\\\.\\"):
        if p.startswith(pre):
            p = p[len(pre):]
    return p


def _is_win(p):
    return bool(re.match(r"^[A-Za-z]:[\\/]", p)) or p.startswith("\\\\")


class ShareMapper:
    """Chemin complet (local Windows, UNC ou POSIX) → (dossier supervisé, chemin relatif « a/b/c »)."""

    def __init__(self, shares):
        self.roots = []
        for s in shares:
            for root in {s.get("path"), s.get("access_path")}:
                if root:
                    p = _strip_prefix(root).rstrip("\\/")
                    win = _is_win(p)
                    norm = p.replace("/", "\\").lower() if win else p
                    self.roots.append((norm, win, s))
        self.roots.sort(key=lambda r: len(r[0]), reverse=True)

    def map(self, path):
        if not path:
            return None, None
        p = _strip_prefix(path).rstrip("\\/")
        win = _is_win(p)
        norm = p.replace("/", "\\").lower() if win else p
        sep = "\\" if win else "/"
        for root, rwin, share in self.roots:
            if rwin != win:
                continue
            if norm == root:
                return share, ""
            if norm.startswith(root + sep):
                return share, p[len(root) + 1:].replace("\\", "/")
        return None, None


# ================================================================ journal Sécurité Windows
def _int(v):
    try:
        s = str(v).strip()
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except (TypeError, ValueError):
        return 0


def _looks_file(path):
    return bool(os.path.splitext(str(path).replace("\\", "/").rsplit("/", 1)[-1])[1])


def mask_action(mask, path):
    """Action principale d'un masque d'accès NTFS (None : simple lecture d'attributs ou parcours)."""
    if mask & DELETE:
        return "delete"
    if mask & WRITE_DAC:
        return "perm"
    if mask & WRITE_OWNER:
        return "owner"
    if mask & (WRITE_DATA | APPEND_DATA):
        return "write" if _looks_file(path) else "create"
    if mask & READ_DATA and _looks_file(path):
        return "read"
    return None


def mask_label(mask):
    names = [(READ_DATA, "lecture"), (WRITE_DATA, "écriture"), (APPEND_DATA, "ajout"), (DELETE, "suppression"),
             (WRITE_DAC, "droits"), (WRITE_OWNER, "propriétaire")]
    return ", ".join(n for b, n in names if mask & b) or hex(mask)


def parse_windows(rec):
    """Enregistrement du journal Sécurité {i, r, t, f, d} → événement normalisé (ou None si sans intérêt)."""
    i, d = int(rec.get("i") or 0), rec.get("d") or {}
    fail = bool(rec.get("f"))
    ev = {"ts": float(rec.get("t") or time.time()), "event_id": i, "source": rec.get("src") or "windows",
          "result": "fail" if fail else "ok"}
    subject = _join_user(d.get("SubjectDomainName"), d.get("SubjectUserName"))
    target = _join_user(d.get("TargetDomainName"), d.get("TargetUserName"))
    if i in (4663, 4656):
        if str(d.get("ObjectType") or "File").lower() not in ("file", "fichier"):
            return None
        obj, mask = d.get("ObjectName") or "", _int(d.get("AccessMask"))
        action = mask_action(mask, obj)
        if i == 4656:            # demande d'accès : seuls les refus sont retenus (les succès doublent 4663)
            if not fail or not action:
                return None
            return {**ev, "user": subject, "action": "denied", "abs": obj, "details": {"demande": mask_label(mask)}}
        if not action:
            return None
        return {**ev, "user": subject, "action": action, "abs": obj,
                "details": {"processus": os.path.basename(str(d.get("ProcessName") or "").replace("\\", "/"))}
                if d.get("ProcessName") else None}
    if i == 5145:                # contrôle d'accès détaillé : refus (avec l'adresse du poste) ; succès → poste
        local = _strip_prefix(d.get("ShareLocalPath") or "")
        rel = str(d.get("RelativeTargetName") or "").strip("\\")
        full = local.rstrip("\\") + ("\\" + rel if rel and rel != "\\" else "")
        client = d.get("IpAddress")
        if not fail:
            return {**ev, "user": subject, "action": None, "client": client}       # sert seulement au poste
        mask = _int(d.get("AccessMask"))
        return {**ev, "user": subject, "action": "denied", "abs": full, "client": client,
                "details": {"demande": mask_label(mask), "partage": d.get("ShareName")}}
    if i == 5140:
        return {**ev, "user": subject, "action": "share", "abs": _strip_prefix(d.get("ShareLocalPath") or ""),
                "client": d.get("IpAddress"), "details": {"partage": d.get("ShareName")}}
    if i == 4624:
        lt = str(d.get("LogonType") or "")
        if lt not in LOGON_TYPES:
            return None
        return {**ev, "user": target, "action": "logon", "client": _client(d),
                "details": {"type": LOGON_TYPES[lt]}}
    if i == 4625:
        code = str(d.get("SubStatus") or "").lower()
        if code in ("", "0x0"):
            code = str(d.get("Status") or "").lower()
        return {**ev, "result": "fail", "user": target, "action": "logon_fail", "client": _client(d),
                "details": {"motif": LOGON_FAIL_REASON.get(code, code or "inconnu"),
                            "type": LOGON_TYPES.get(str(d.get("LogonType") or ""), "")}}
    if i == 4740:                # TargetDomainName porte ici le poste à l'origine du verrouillage
        return {**ev, "result": "fail", "user": d.get("TargetUserName"), "action": "lockout",
                "client": d.get("TargetDomainName") or None}
    if i == 4670:
        if str(d.get("ObjectType") or "File").lower() not in ("file", "fichier"):
            return None
        return {**ev, "user": subject, "action": "perm", "abs": d.get("ObjectName")}
    if i == 4719:
        return {**ev, "user": subject, "action": "policy",
                "details": {"objet": "stratégie d'audit du serveur", "modification": d.get("AuditPolicyChanges")}}
    if i == 4907:
        return {**ev, "user": subject, "action": "policy", "abs": d.get("ObjectName"),
                "details": {"objet": "audit d'un dossier (SACL)"}}
    if i == 1102:
        return {**ev, "user": subject, "action": "audit_cleared"}
    if i in (4720, 4726):
        return {**ev, "user": subject, "action": "account",
                "details": {"compte": target, "operation": "création" if i == 4720 else "suppression"}}
    if i in (4728, 4732, 4756):
        member = d.get("MemberName") or d.get("MemberSid") or ""
        m = re.match(r"^CN=([^,]+)", str(member))
        return {**ev, "user": subject, "action": "group_add",
                "details": {"membre": m.group(1) if m else member, "groupe": target}}
    return None


def _client(d):
    ws = str(d.get("WorkstationName") or "").strip()
    ip = str(d.get("IpAddress") or "").strip()
    if ws and ws != "-":
        return ws
    return ip if ip and ip not in ("-", "::1", "127.0.0.1") else None


def windows_script(bookmark=None, initial_days=7, max_events=20000, evtx=None):
    """Script PowerShell de lecture du journal Sécurité (ou d'un fichier .evtx) → JSON compact."""
    ids = " or ".join(f"EventID={i}" for i in WINDOWS_EVENT_IDS)
    if evtx:
        window = ""
    elif bookmark:
        window = f" and EventRecordID &gt; {int(bookmark)}"
    else:
        window = f" and TimeCreated[timediff(@SystemTime) &lt;= {int(initial_days) * 86400000}]"
    src = system.ps_quote("file://" + evtx) if evtx else "'Security'"
    query = (f"<QueryList><Query Id='0' Path={src}>"
             f"<Select Path={src}>*[System[({ids}){window}]]</Select>"
             f"<Select Path={src}>*[System[EventID=4656 and band(Keywords,4503599627370496){window}]]</Select>"
             "</Query></QueryList>")
    newest = "" if evtx else \
        "try { $res.newest = (Get-WinEvent -LogName Security -MaxEvents 1 -ErrorAction Stop).RecordId } catch {}\n"
    return f"""
$res = @{{ events = @(); newest = $null; error = $null }}
{newest}try {{
  $evs = @(Get-WinEvent -FilterXml {system.ps_quote(query)} -Oldest -MaxEvents {int(max_events)} -ErrorAction Stop)
}} catch {{
  if ($_.FullyQualifiedErrorId -notmatch 'NoMatchingEventsFound') {{ $res.error = $_.Exception.Message }}
  $evs = @()
}}
$res.events = @(foreach ($e in $evs) {{
  $x = [xml]$e.ToXml(); $d = @{{}}
  if ($x.Event.EventData) {{ foreach ($n in $x.Event.EventData.Data) {{ if ($n.Name) {{ $d[$n.Name] = [string]$n.'#text' }} }} }}
  if ($x.Event.UserData) {{ foreach ($n in $x.Event.UserData.FirstChild.ChildNodes) {{ $d[$n.LocalName] = [string]$n.InnerText }} }}
  [pscustomobject]@{{ i = $e.Id; r = $e.RecordId; t = ([DateTimeOffset]$e.TimeCreated).ToUnixTimeMilliseconds() / 1000;
    f = (($e.Keywords -band 0x10000000000000) -ne 0); d = $d }}
}})
$res | ConvertTo-Json -Depth 4 -Compress
"""


def _list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


def collect_windows(bookmark, cfg, evtx=None):
    """(enregistrements bruts, nouveau signet, dernier numéro du journal)."""
    data = system.powershell(windows_script(bookmark, cfg.get("initial_days", 7), cfg.get("max_events_per_poll", 20000),
                                            evtx), timeout=600) or {}
    if data.get("error"):
        raise system.CommandError(data["error"])
    recs = _list(data.get("events"))
    last = max((int(r.get("r") or 0) for r in recs), default=None)
    return recs, last, data.get("newest")


NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def _xml_time(s):
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?", s or "")
    if not m:
        return None
    import calendar
    secs = calendar.timegm(tuple(int(m.group(k)) for k in range(1, 7)) + (0, 0, 0))
    return secs + (float("0" + m.group(7)) if m.group(7) else 0)


def parse_windows_xml(text):
    """Export XML de l'Observateur d'événements ou de « wevtutil qe Security /f:xml » → enregistrements bruts."""
    text = text.strip()
    if not text.startswith("<Events"):
        text = "<Events>" + re.sub(r"<\?xml[^>]*\?>", "", text) + "</Events>"
    root = ET.fromstring(text)
    out = []
    for ev in root.iter(f"{NS}Event"):
        sysn = ev.find(f"{NS}System")
        if sysn is None:
            continue
        eid = int((sysn.findtext(f"{NS}EventID") or "0").strip() or 0)
        tc = sysn.find(f"{NS}TimeCreated")
        kw = _int(sysn.findtext(f"{NS}Keywords") or "0")
        d = {}
        data = ev.find(f"{NS}EventData")
        if data is not None:
            for n in data.findall(f"{NS}Data"):
                if n.get("Name"):
                    d[n.get("Name")] = (n.text or "").strip()
        ud = ev.find(f"{NS}UserData")
        if ud is not None and len(ud):
            for n in list(ud)[0]:
                d[n.tag.split("}")[-1]] = (n.text or "").strip()
        out.append({"i": eid, "r": int(sysn.findtext(f"{NS}EventRecordID") or 0),
                    "t": _xml_time(tc.get("SystemTime") if tc is not None else "") or time.time(),
                    "f": bool(kw & 0x10000000000000), "d": d})
    return out


# ================================================================ audit Samba (full_audit)
SAMBA_OPS = {
    "connect": "share", "openat": "open", "open": "open", "create_file": "create", "unlinkat": "delete",
    "unlink": "delete", "renameat": "rename", "rename": "rename", "mkdirat": "create", "mkdir": "create",
    "rmdir": "delete", "fchmod": "perm", "chmod": "perm", "fchown": "owner", "chown": "owner", "fchownat": "owner",
    "fset_nt_acl": "perm", "set_nt_acl": "perm", "pwrite": "write", "write": "write", "pwrite_send": "write",
    "pread": "read", "read": "read", "pread_send": "read",
}
SYSLOG_TS = re.compile(r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<time>\d\d:\d\d:\d\d)")
ISO_TS = re.compile(r"^(?P<iso>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:?\d\d|Z)?)")
MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def _line_time(line, now):
    m = ISO_TS.match(line)
    if m:
        from datetime import datetime
        try:
            return datetime.fromisoformat(m.group("iso").replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    m = SYSLOG_TS.match(line)
    if m:
        lt = time.localtime(now)
        hh, mm, ss = (int(x) for x in m.group("time").split(":"))
        ts = time.mktime((lt.tm_year, MONTHS.get(m.group("mon"), 1), int(m.group("day")), hh, mm, ss, 0, 0, -1))
        return ts - 365 * 86400 if ts > now + 86400 else ts     # passage d'année
    return now


def parse_samba_line(line, now=None):
    """Ligne full_audit (préfixe conseillé : %u|%I|%m|%S) → événement normalisé avec le nom du partage."""
    now = now or time.time()
    body = line.split("smbd_audit:", 1)[1] if "smbd_audit:" in line else line
    fields = [f.strip() for f in body.strip().split("|")]
    idx = next((k for k in range(1, len(fields) - 1)
                if fields[k] in SAMBA_OPS and (fields[k + 1].startswith("ok") or fields[k + 1].startswith("fail"))), None)
    if idx is None:
        return None
    prefix, op, res, args = fields[:idx], fields[idx], fields[idx + 1], fields[idx + 2:]
    user = prefix[0].split()[-1] if prefix else ""
    ip = prefix[1] if len(prefix) >= 3 else None
    machine = prefix[2] if len(prefix) >= 4 else None
    share = prefix[-1] if len(prefix) >= 2 else None
    action = SAMBA_OPS[op]
    path = dest = None
    if op in ("openat", "open"):
        mode = args[0] if args and args[0] in ("r", "w") else None
        path = args[-1] if args else None
        action = "write" if mode == "w" else "read"
    elif op == "create_file":
        disposition = args[2].lower() if len(args) >= 3 else ""
        path = args[-1] if args else None
        action = "create" if disposition in ("create", "overwrite_if", "supersede") else "open"
        if len(args) >= 2 and args[1] == "dir" and action == "open":
            return None
    elif op in ("renameat", "rename"):
        path, dest = (args[0], args[1]) if len(args) >= 2 else (args[0] if args else None, None)
    elif op == "connect":
        share = args[0] if args else share
    else:
        path = args[-1] if args else None
    failed = res.startswith("fail")
    if failed and action in FILE_ACTIONS:
        action = "denied"
    return {"ts": _line_time(line, now), "user": user, "action": action, "result": "fail" if failed else "ok",
            "share_name": share, "rel": path, "rel_dest": dest, "client": machine or ip, "source": "samba",
            "details": {"operation": op, "motif": res[4:].strip(" ()")} if failed else None}


def samba_default_log():
    for p in ("/var/log/samba/audit.log", "/var/log/samba/log.audit", "/var/log/syslog", "/var/log/messages"):
        if os.path.isfile(p):
            return p
    return None


def read_samba_log(path, bookmark, max_bytes=20 * 1024 * 1024):
    """Lit la suite du journal depuis le signet {inode, offset} (rotation détectée) → (lignes, signet)."""
    st = os.stat(path)
    bm = bookmark or {}
    if not bm:                  # premier passage : seulement la fin d'un journal système volumineux
        offset = max(0, st.st_size - 50 * 1024 * 1024)
    else:
        offset = bm.get("offset", 0) if bm.get("inode") == st.st_ino and bm.get("offset", 0) <= st.st_size else 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        if offset and not bm:
            fh.readline()       # ligne coupée
            offset = fh.tell()
        chunk = fh.read(max_bytes)
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], {"inode": st.st_ino, "offset": offset}
    lines = chunk[:end].decode("utf-8", "replace").splitlines()
    return [ln for ln in lines if "smbd_audit" in ln or ln.count("|") >= 4], \
        {"inode": st.st_ino, "offset": offset + end + 1}


# ================================================================ stockage, agrégats, profils
class ActivityStore:
    def __init__(self, db):
        self.db = db
        self._recent = {}           # (user, action, share, path) -> ts : dédoublonnage
        self._client = {}           # user -> (poste, ts) : poste des événements sans adresse (4663)
        self._logins = {}           # identifiant seul -> clés complètes connues (base, rafraîchie)
        self._logins_at = 0
        self._seen = defaultdict(set)   # identifiant seul -> clés complètes vues depuis le démarrage
        self.lock = threading.Lock()

    # --- utilisateurs ---
    def resolve(self, raw):
        """Clé normalisée ; un identifiant sans domaine est rattaché au compte « domaine\\identifiant » connu
        s'il n'y en a qu'un (verrouillages 4740, Samba, dossiers personnels)."""
        key = user_key(raw)
        if not key:
            return key
        if "\\" in key:
            self._seen[login_of(key)].add(key)
            return key
        now = time.time()
        if now - self._logins_at > 600:
            known = defaultdict(set)
            for r in self.db.query("SELECT user FROM user_profiles WHERE user LIKE '%\\%'"):
                known[login_of(r["user"])].add(r["user"])
            self._logins = known
            self._logins_at = now
        full = self._logins.get(key, set()) | self._seen.get(key, set())
        return next(iter(full)) if len(full) == 1 else key

    def settings(self):
        return merged_setting(self.db, "security")

    def exclusions(self, cfg=None):
        cfg = cfg or self.settings()
        service = [r["user"] for r in self.db.query("SELECT user FROM user_profiles WHERE service=1")]
        return Exclusions(cfg.get("excluded_accounts"), service)

    # --- ingestion ---
    def ingest(self, events, mapper, cfg=None, samba_shares=None):
        """Normalise, rattache, dédoublonne et enregistre un lot. Renvoie le nombre d'événements retenus."""
        cfg = cfg or self.settings()
        excluded = self.exclusions(cfg)
        reads = cfg.get("include_reads", True)
        rows, agg, prof = [], defaultdict(Counter), {}
        with self.lock:
            for e in sorted(events, key=lambda x: x.get("ts") or 0):
                raw_user = e.get("user")
                user = self.resolve(raw_user)
                if not user or excluded(user):
                    continue
                ts = float(e.get("ts") or time.time())
                if not plausible_mtime(ts):
                    continue                # date absurde (enregistrement abîmé) : hors de toute analyse
                client = (str(e.get("client")).strip() or None) if e.get("client") else None
                if client in ("-", "::1", "127.0.0.1"):
                    client = None
                if client:
                    self._client[user] = (client, ts)
                action = e.get("action")
                if not action:
                    continue
                if action in ("read", "open") and not reads:
                    continue
                if not client and action in FILE_ACTIONS:
                    known = self._client.get(user)
                    client = known[0] if known and ts - known[1] < 12 * 3600 else None
                share, rel, dest = None, None, None
                if e.get("abs"):
                    share, rel = mapper.map(e["abs"])
                    rel = rel if share else _strip_prefix(e["abs"])
                elif e.get("share_name") is not None and samba_shares is not None:
                    local = samba_shares.get(str(e["share_name"]).lower())
                    if local:
                        share, base = mapper.map(local)
                        if share is not None:
                            parts = [p for p in (base, str(e.get("rel") or "").lstrip("./")) if p]
                            rel = "/".join(parts)
                            if e.get("rel_dest"):
                                dest = "/".join(p for p in (base, str(e["rel_dest"]).lstrip("./")) if p)
                    if share is None:
                        rel = e.get("rel")
                if e.get("abs_dest"):
                    dshare, drel = mapper.map(e["abs_dest"])
                    dest = drel if dshare else e["abs_dest"]
                sid = share["id"] if share else None
                window = DEDUP_SEC.get(action)
                if window:
                    k = (user, action, sid, rel if action in FILE_ACTIONS else client)
                    last = self._recent.get(k)
                    if last is not None and 0 <= ts - last < window:
                        continue
                    self._recent[k] = ts
                result = e.get("result") or "ok"
                details = e.get("details")
                rows.append((ts, user, action, result, sid, rel, dest, client, e.get("source") or "inconnu",
                             e.get("event_id"), json.dumps(details, ensure_ascii=False) if details else None))
                hour = int(ts // 3600)
                col = COUNTER_OF.get(action)
                c = agg[(user, hour, sid or 0)]
                if col:
                    c[col] += 1
                c["total"] += 1
                p = prof.setdefault(user, {"display": raw_user if isinstance(raw_user, str) else user, "first": ts,
                                           "last": ts, "clients": {}, "folders": {}})
                p["first"], p["last"] = min(p["first"], ts), max(p["last"], ts)
                if client:
                    cur = p["clients"].get(client)
                    p["clients"][client] = [min(cur[0], ts), max(cur[1], ts)] if cur else [ts, ts]
                if sid and action in FILE_ACTIONS and rel is not None:
                    top = f"{sid}:{rel.split('/', 1)[0] if '/' in rel else ''}"
                    cur = p["folders"].get(top)
                    p["folders"][top] = [min(cur[0], ts), max(cur[1], ts)] if cur else [ts, ts]
            if len(self._recent) > 200000:
                cutoff = max(self._recent.values()) - 3600
                self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        if not rows:
            return 0
        self.db.executemany(
            "INSERT INTO user_events(ts, user, action, result, share_id, path, dest_path, client, source, event_id, "
            "details) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        cols = COUNTERS + ["total"]
        self.db.executemany(
            f"INSERT INTO user_activity(user, hour, share_id, {', '.join(cols)}) VALUES(?,?,?,{','.join('?' * len(cols))}) "
            f"ON CONFLICT(user, hour, share_id) DO UPDATE SET "
            + ", ".join(f"{c} = {c} + excluded.{c}" for c in cols),
            [(u, h, s, *[c.get(k, 0) for k in cols]) for (u, h, s), c in agg.items()])
        self._update_profiles(prof)
        return len(rows)

    def _update_profiles(self, prof):
        users = list(prof)
        existing = {}
        for k in range(0, len(users), 400):
            chunk = users[k:k + 400]
            for r in self.db.query(f"SELECT * FROM user_profiles WHERE user IN ({','.join('?' * len(chunk))})", chunk):
                existing[r["user"]] = r
        out = []
        for user, p in prof.items():
            old = existing.get(user) or {}
            clients = json.loads(old.get("clients") or "{}")
            folders = json.loads(old.get("folders") or "{}")
            for target, new in ((clients, p["clients"]), (folders, p["folders"])):
                for k, (a, b) in new.items():
                    cur = target.get(k)
                    target[k] = [min(cur[0], a), max(cur[1], b)] if cur else [a, b]
            if len(clients) > 200:
                clients = dict(sorted(clients.items(), key=lambda kv: kv[1][1])[-200:])
            if len(folders) > 500:
                folders = dict(sorted(folders.items(), key=lambda kv: kv[1][1])[-500:])
            display = old.get("display") or p["display"]
            if "\\" in str(p["display"]) and "\\" not in str(display):
                display = p["display"]
            out.append((user, display, min(old.get("first_seen") or p["first"], p["first"]),
                        max(old.get("last_seen") or 0, p["last"]), json.dumps(clients), json.dumps(folders)))
        self.db.executemany(
            "INSERT INTO user_profiles(user, display, first_seen, last_seen, clients, folders) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(user) DO UPDATE SET display=excluded.display, first_seen=excluded.first_seen, "
            "last_seen=excluded.last_seen, clients=excluded.clients, folders=excluded.folders", out)

    # --- sources : signets et état ---
    def source(self, name):
        row = self.db.query_one("SELECT * FROM log_sources WHERE name=?", (name,))
        if row:
            row["bookmark"] = json.loads(row["bookmark"]) if row.get("bookmark") else None
            row["info"] = json.loads(row.get("info") or "{}")
        return row

    def source_update(self, name, ok, count=0, bookmark=None, error=None, info=None):
        now = time.time()
        prev = self.source(name) or {}
        self.db.execute(
            "INSERT INTO log_sources(name, bookmark, last_run, last_ok, last_error, events_total, last_count, info) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET bookmark=excluded.bookmark, "
            "last_run=excluded.last_run, last_ok=excluded.last_ok, last_error=excluded.last_error, "
            "events_total=excluded.events_total, last_count=excluded.last_count, info=excluded.info",
            (name, json.dumps(bookmark if bookmark is not None else prev.get("bookmark")), now,
             now if ok else prev.get("last_ok"), None if ok else str(error)[:500],
             (prev.get("events_total") or 0) + count, count, json.dumps(info or prev.get("info") or {})))

    def purge(self, cfg=None):
        cfg = cfg or self.settings()
        now = time.time()
        raw = now - int(cfg.get("raw_retention_days", 45)) * 86400
        agg = now - int(cfg.get("aggregate_retention_days", 400)) * 86400
        self.db.execute("DELETE FROM user_events WHERE ts < ?", (raw,))
        self.db.execute("DELETE FROM smb_sessions WHERE last_seen < ?", (raw,))
        self.db.execute("DELETE FROM user_activity WHERE hour < ?", (int(agg // 3600),))
        self.db.execute("DELETE FROM user_findings WHERE ts < ?", (agg,))
        cap = int(cfg.get("max_raw_events", 5000000) or 0)
        if cap:
            cut = self.db.query_one("SELECT id FROM user_events ORDER BY id DESC LIMIT 1 OFFSET ?", (cap,))
            if cut:
                self.db.execute("DELETE FROM user_events WHERE id <= ?", (cut["id"],))


# ================================================================ sessions et fichiers ouverts SMB
class SmbSampler:
    """Échantillonne les sessions et fichiers ouverts : présence des utilisateurs et ouvertures de fichiers."""

    def __init__(self, store):
        self.store = store
        self.prev = None

    def sample(self, interval=60):
        now = time.time()
        sessions = smb.sessions()
        files = smb.open_files()
        db = self.store.db
        for s in sessions:
            user = self.store.resolve(s.get("user"))
            if not user:
                continue
            client = s.get("client") or None
            row = db.query_one("SELECT id, max_opens FROM smb_sessions WHERE user=? AND COALESCE(client,'')=? "
                               "AND last_seen >= ? ORDER BY id DESC LIMIT 1", (user, client or "", now - 3 * interval))
            opens = int(s.get("opens") or 0)
            if row:
                db.execute("UPDATE smb_sessions SET last_seen=?, max_opens=? WHERE id=?",
                           (now, max(opens, row["max_opens"] or 0), row["id"]))
            else:
                db.execute("INSERT INTO smb_sessions(user, client, started, last_seen, max_opens, dialect) "
                           "VALUES(?,?,?,?,?,?)", (user, client, now - (s.get("duration") or 0), now, opens,
                                                   s.get("dialect")))
        clients = {str(s.get("id")): s.get("client") for s in sessions}
        current = {}
        for f in files:
            user = user_key(f.get("user"))
            if user and f.get("path"):
                current[(user, f["path"])] = f.get("client") or clients.get(str(f.get("session"))) or None
        new = [k for k in current if self.prev is None or k not in self.prev]
        self.prev = set(current)
        return [{"ts": now, "user": u, "action": "open", "abs": p, "client": current[(u, p)], "source": "smb"}
                for u, p in new]


# ================================================================ service de collecte (thread)
class SecurityService:
    """Collecte continue, analyse comportementale périodique, rétention. Surveillé par la garde."""

    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.store = ActivityStore(self.db)
        self.sampler = SmbSampler(self.store)
        self.analyzer = None
        self._stop = threading.Event()
        self._thread = None
        self._last_sample = 0
        self._last_analysis = 0
        self._last_purge = 0
        self.last_cycle = None

    def start(self):
        from .ueba import Analyzer
        self.analyzer = self.analyzer or Analyzer(self.engine, self.store)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="vigiefs-securite", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _loop(self):
        self._stop.wait(5)          # laisse démarrer le planificateur et la surveillance
        while not self._stop.is_set():
            cfg = merged_setting(self.db, "security")
            try:
                if cfg.get("enabled", True) and not self.engine.maintenance():
                    self.run_once(cfg)
            except Exception:  # noqa: BLE001
                log.exception("Cycle de collecte de sécurité en échec")
            self.last_cycle = time.time()
            self._stop.wait(max(15, int(cfg.get("poll_sec", 60))))

    def mapper(self):
        return ShareMapper(self.engine.shares())

    def run_once(self, cfg=None):
        cfg = cfg or merged_setting(self.db, "security")
        from .ueba import Analyzer
        self.analyzer = self.analyzer or Analyzer(self.engine, self.store)
        mapper = self.mapper()
        total = 0
        if system.IS_WINDOWS and cfg.get("collect_windows", True):
            total += self._collect_windows(cfg, mapper)
        elif not system.IS_WINDOWS and cfg.get("collect_samba", True):
            total += self._collect_samba(cfg, mapper)
        now = time.time()
        if cfg.get("smb_sampling", True) and now - self._last_sample >= max(30, int(cfg.get("poll_sec", 60))):
            self._last_sample = now
            try:
                events = self.sampler.sample(int(cfg.get("poll_sec", 60)))
                n = self.store.ingest(events, mapper, cfg)
                total += n
                self.store.source_update("smb", True, n)
            except Exception as exc:  # noqa: BLE001
                self.store.source_update("smb", False, error=exc)
        if total or now - self._last_analysis >= 300:
            self._last_analysis = now
            self.analyzer.run()
        if now - self._last_purge > 86400:
            self._last_purge = now
            self.store.purge(cfg)
        return total

    def _collect_windows(self, cfg, mapper):
        src = self.store.source("windows") or {}
        bookmark = (src.get("bookmark") or {}).get("record")
        total = 0
        for _ in range(10):                     # rattrapage d'un arriéré : plusieurs lots par cycle
            try:
                recs, last, newest = collect_windows(bookmark, cfg)
            except system.CommandError as exc:
                self.store.source_update("windows", False, error=exc)
                return total
            if bookmark and newest and int(newest) < int(bookmark):
                bookmark = None                 # journal effacé ou recréé : reprise au début
                continue
            n = self.store.ingest([e for e in (parse_windows(r) for r in recs) if e], mapper, cfg)
            total += n
            bookmark = last or bookmark or newest
            self.store.source_update("windows", True, n, {"record": bookmark}, info={"newest": newest})
            if len(recs) < int(cfg.get("max_events_per_poll", 20000)):
                break
        return total

    def _collect_samba(self, cfg, mapper):
        path = cfg.get("samba_log") or samba_default_log()
        if not path or not os.path.isfile(path):
            return 0
        src = self.store.source("samba") or {}
        try:
            lines, bookmark = read_samba_log(path, src.get("bookmark"))
            shares = {s["name"].lower(): s["path"] for s in smb.samba_shares() if s.get("path")}
            now = time.time()
            events = [e for e in (parse_samba_line(ln, now) for ln in lines) if e]
            n = self.store.ingest(events, mapper, cfg, samba_shares=shares)
            self.store.source_update("samba", True, n, bookmark, info={"file": path})
            return n
        except OSError as exc:
            self.store.source_update("samba", False, error=exc, info={"file": path})
            return 0

    # --- import de journaux exportés ---
    def import_file(self, name, data, actor):
        """Analyse un journal exporté → {format, lus, retenus, période}. Les détections portent sur sa période."""
        cfg = merged_setting(self.db, "security")
        mapper = self.mapper()
        lower = name.lower()
        shares = None
        if lower.endswith(".evtx"):
            if not system.IS_WINDOWS:
                raise ValueError("Les fichiers .evtx se lisent sur un serveur Windows. Ailleurs, exportez le journal en "
                                 "XML (Observateur d'événements › Enregistrer sous › XML).")
            import tempfile
            fd, path = tempfile.mkstemp(suffix=".evtx", prefix="vigiefs-import-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            try:
                recs, _, _ = collect_windows(None, {**cfg, "max_events_per_poll": 2000000}, evtx=path)
            finally:
                os.remove(path)
            events, fmt = [parse_windows({**r, "src": "import"}) for r in recs], "Journal Windows (.evtx)"
        else:
            text = data.decode("utf-8-sig", "replace")
            if "<Event" in text[:4096] and "schemas.microsoft.com/win/2004/08/events" in text[:8192]:
                recs = parse_windows_xml(text)
                events, fmt = [parse_windows({**r, "src": "import"}) for r in recs], "Journal Windows (XML)"
            else:
                now = time.time()
                events = [parse_samba_line(ln, now) for ln in text.splitlines()]
                events = [{**e, "source": "import"} for e in events if e]
                shares = {s["name"].lower(): s["path"] for s in smb.samba_shares() if s.get("path")}
                for s in self.engine.shares():
                    shares.setdefault(s["name"].lower(), s["path"])
                fmt, recs = "Journal d'audit Samba", events
        events = [e for e in events if e]
        kept = self.store.ingest(events, mapper, cfg, samba_shares=shares)
        span = (min((e["ts"] for e in events), default=None), max((e["ts"] for e in events), default=None))
        self.db.audit(actor, "import d'un journal", name, f"{kept} événement(s) retenu(s)")
        found = []
        if kept and span[0]:
            from .ueba import Analyzer
            self.analyzer = self.analyzer or Analyzer(self.engine, self.store)
            found = self.analyzer.run(span[0], span[1] + 1)
        return {"format": fmt, "read": len(recs), "events": len(events), "kept": kept, "start": span[0],
                "end": span[1], "findings": len(found)}
