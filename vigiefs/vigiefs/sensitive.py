"""Découverte de données sensibles (RGPD) : NIR, IBAN, cartes bancaires, identifiants en clair, santé…

Chaque détection numérique est validée (clé NIR modulo 97, IBAN ISO 7064, cartes par algorithme de
Luhn) pour limiter les faux positifs. Les valeurs trouvées ne sont jamais stockées en clair : seuls des
extraits masqués sont conservés. L'exposition est croisée avec les droits (dossiers ouverts à tous).
"""

import os
import re
import zipfile
from collections import defaultdict

from . import acl
from .rules import long_path, relpath
from .storage import is_within

CATEGORIES = {
    "nir": ("Numéro de sécurité sociale (NIR)", 10),
    "card": ("Carte bancaire", 10),
    "credentials": ("Identifiants / mots de passe en clair", 10),
    "health": ("Données de santé (art. 9 RGPD)", 6),
    "iban": ("IBAN / coordonnées bancaires", 6),
    "email": ("Adresses e-mail (liste)", 1),
    "phone": ("Numéros de téléphone (liste)", 1),
}
HIGH = {"nir", "card", "credentials", "health"}

NIR_RE = re.compile(r"(?<![\d])([12])[ .]?(\d{2})[ .]?(0[1-9]|1[0-2]|[2-9]\d)[ .]?(2[AB]|\d{2})[ .]?(\d{3})[ .]?(\d{3})[ .]?(\d{2})(?![\d])")
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)\b")
CARD_RE = re.compile(r"(?<![\d])(?:\d[ -]?){12,18}\d(?![\d])")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<![\d])(?:\+33[ .]?|0)[1-9](?:[ .-]?\d{2}){4}(?![\d])")
CRED_RE = re.compile(r"(?i)\b(mot de passe|password|passwd|pwd|mdp|pass)\s*[:=]\s*(\S{4,})")
HEALTH_RE = re.compile(r"(?i)\b(arrêt maladie|arret maladie|diagnostic|pathologie|ordonnance|traitement médical|"
                       r"dossier médical|handicap|séropositi\w*|affection longue durée|mutuelle santé)\b")
IBAN_LEN = {"FR": 27, "DE": 22, "BE": 16, "ES": 24, "IT": 27, "LU": 20, "CH": 21, "MC": 27, "GB": 22, "NL": 18, "PT": 25}


def nir_valid(m):
    sexe, aa, mm, dep, com, ordre, cle = m.groups()
    dep_num = {"2A": "19", "2B": "18"}.get(dep, dep)
    num = int(sexe + aa + mm + dep_num + com + ordre)
    return 97 - num % 97 == int(cle)


def iban_valid(raw):
    s = raw.replace(" ", "")
    if IBAN_LEN.get(s[:2], len(s)) != len(s) or not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def luhn(digits):
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def card_valid(raw):
    d = re.sub(r"\D", "", raw)
    if not 13 <= len(d) <= 19 or len(set(d)) < 3 or not luhn(d):
        return False
    return d[0] == "4" or d[:2] in ("34", "37") or 51 <= int(d[:2]) <= 55 or 2221 <= int(d[:4]) <= 2720 \
        or d[:4] == "6011" or d[:2] == "65"


def mask(value, keep=4):
    v = re.sub(r"\s", "", value)
    return v[:2] + "•" * max(0, len(v) - 2 - keep) + v[-keep:]


def extract_text(path, max_bytes):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"):
            with zipfile.ZipFile(path) as zf:
                chunks = []
                for n in zf.namelist():
                    if re.match(r"(word/document|word/header\d*|word/footer\d*|xl/sharedStrings|xl/worksheets/sheet\d+|"
                                r"ppt/slides/slide\d+|content)\.xml$", n):
                        chunks.append(zf.read(n)[:max_bytes].decode("utf-8", "replace"))
                xml = " ".join(chunks)
                xml = re.sub(r"</(w:p|a:p|text:p|row)>", "\n", xml)
                return re.sub(r"<[^>]+>", " ", xml)
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes)
        for enc in ("utf-8", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", "replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""


def scan_text(text):
    found, samples = defaultdict(int), defaultdict(list)

    def add(cat, sample):
        found[cat] += 1
        if len(samples[cat]) < 3:
            samples[cat].append(sample)

    for m in NIR_RE.finditer(text):
        if nir_valid(m):
            add("nir", m.group(1) + " •• •• •• ••• ••• " + m.group(7))
    for m in IBAN_RE.finditer(text):
        if iban_valid(m.group(1)):
            add("iban", mask(m.group(1)))
    for m in CARD_RE.finditer(text):
        if card_valid(m.group(0)):
            add("card", "•••• •••• •••• " + re.sub(r"\D", "", m.group(0))[-4:])
    for m in CRED_RE.finditer(text):
        add("credentials", f"{m.group(1)} : {m.group(2)[:1]}{'•' * 6}")
    emails = {e.lower() for e in EMAIL_RE.findall(text)}
    if len(emails) >= 5:
        found["email"] = len(emails)
        samples["email"] = [mask(e, 6) for e in list(emails)[:3]]
    phones = set(PHONE_RE.findall(text))
    if len(phones) >= 5:
        found["phone"] = len(phones)
        samples["phone"] = [mask(p, 2) for p in list(phones)[:3]]
    health = HEALTH_RE.findall(text)
    if len(health) >= 3:
        found["health"] = len(health)
        samples["health"] = sorted({h.lower() for h in health})[:3]
    return dict(found), dict(samples)


def _exposure(snapshot):
    """Chemin → identités « tout le monde » ayant au moins la lecture."""
    if not snapshot:
        return {}
    out = {}
    for n in snapshot["nodes"]:
        if n.get("error"):
            continue
        broad = [p for p, lv in acl.effective_levels(n).items()
                 if acl.is_broad(p) and acl.LEVEL_RANK[lv] >= acl.LEVEL_RANK["read"]]
        out[n["path"]] = broad
    return out


def _nearest(expo, rel_dir):
    p = rel_dir
    while True:
        if p in expo:
            return expo[p]
        if not p:
            return []
        p = p.rsplit("/", 1)[0] if "/" in p else ""


def scan_share(share, settings, ctx, snapshot=None, skip_roots=()):
    root = os.path.realpath(share["path"])
    exts = tuple(e.lower() for e in settings.get("extensions", []))
    max_bytes = int(settings.get("max_file_mb", 20)) * 1024 * 1024
    expo = _exposure(snapshot)
    results, totals = [], defaultdict(lambda: {"files": 0, "hits": 0})
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(long_path(root)):
        ctx.check()
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                       and not any(r and is_within(os.path.join(dirpath, d), r) for r in skip_roots)]
        for name in filenames:
            if not name.lower().endswith(exts) or name.startswith("~$"):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            scanned += 1
            found, samples = scan_text(extract_text(full, max_bytes))
            ctx.progress(scanned=scanned, flagged=len(results))
            if not found:
                continue
            rel = relpath(root, full)
            exposed_to = _nearest(expo, os.path.dirname(rel).replace("\\", "/"))
            risk = sum(CATEGORIES[c][1] * min(n, 10) for c, n in found.items())
            level = "high" if HIGH & set(found) else "medium" if "iban" in found or found.get("email", 0) >= 50 else "low"
            results.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime, "found": found, "samples": samples,
                            "risk": risk, "level": level, "exposed_to": exposed_to})
            for c, n in found.items():
                totals[c]["files"] += 1
                totals[c]["hits"] += n
    order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: (order[r["level"]], not r["exposed_to"], -r["risk"]))
    return {"scanned": scanned, "flagged": len(results), "results": results[:2000],
            "totals": {c: totals[c] for c in CATEGORIES if c in totals},
            "high": sum(1 for r in results if r["level"] == "high"),
            "exposed_high": sum(1 for r in results if r["level"] == "high" and r["exposed_to"]),
            "labels": {k: v[0] for k, v in CATEGORIES.items()}, "acl_snapshot": bool(snapshot)}
