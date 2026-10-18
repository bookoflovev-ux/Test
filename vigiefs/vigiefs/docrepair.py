"""Diagnostic et réparation de documents bureautiques : Word, Excel, PowerPoint (OOXML et anciens
formats binaires) et PDF.

Principes :
- l'original n'est jamais modifié : la réparation produit un nouveau fichier ;
- les réparations vont de la plus fidèle à la plus dégradée :
  reconstruction de l'archive → réparation XML → cohérence du paquet → conversion LibreOffice →
  récupération du texte seul ;
- le résultat est systématiquement re-diagnostiqué avant d'être proposé.
"""

import base64
import io
import math
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from xml.sax.saxutils import escape

try:
    from lxml import etree
except ImportError:  # pragma: no cover - lxml est dans requirements.txt
    etree = None

try:
    import pikepdf
except ImportError:  # pragma: no cover
    pikepdf = None

try:
    import olefile
except ImportError:  # pragma: no cover
    olefile = None

# ---------------------------------------------------------------- formats
OOXML = {
    "docx": ("word", "word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
    "docm": ("word", "word/document.xml", "application/vnd.ms-word.document.macroEnabled.main+xml"),
    "dotx": ("word", "word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"),
    "dotm": ("word", "word/document.xml", "application/vnd.ms-word.template.macroEnabledTemplate.main+xml"),
    "xlsx": ("excel", "xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
    "xlsm": ("excel", "xl/workbook.xml", "application/vnd.ms-excel.sheet.macroEnabled.main+xml"),
    "xltx": ("excel", "xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"),
    "xltm": ("excel", "xl/workbook.xml", "application/vnd.ms-excel.template.macroEnabled.main+xml"),
    "pptx": ("powerpoint", "ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
    "pptm": ("powerpoint", "ppt/presentation.xml", "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"),
    "potx": ("powerpoint", "ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"),
    "ppsx": ("powerpoint", "ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml"),
}
LEGACY = {"doc": ("word", "docx", "WordDocument"), "dot": ("word", "docx", "WordDocument"),
          "xls": ("excel", "xlsx", "Workbook"), "xlt": ("excel", "xlsx", "Workbook"),
          "ppt": ("powerpoint", "pptx", "PowerPoint Document"), "pps": ("powerpoint", "pptx", "PowerPoint Document")}
SUPPORTED = set(OOXML) | set(LEGACY) | {"pdf"}
FAMILY_LABEL = {"word": "Word", "excel": "Excel", "powerpoint": "PowerPoint", "pdf": "PDF"}

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OFFICE_DOC_REL = R_NS + "/officeDocument"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

CT_DEFAULTS = {
    "rels": "application/vnd.openxmlformats-package.relationships+xml", "xml": "application/xml",
    "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff", "emf": "image/x-emf", "wmf": "image/x-wmf",
    "svg": "image/svg+xml", "wdp": "image/vnd.ms-photo", "jfif": "image/jpeg",
    "bin": "application/vnd.openxmlformats-officedocument.oleObject",
    "vml": "application/vnd.openxmlformats-officedocument.vmlDrawing",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odttf": "application/vnd.openxmlformats-officedocument.obfuscatedFont",
    "fntdata": "application/x-fontdata", "mp4": "video/mp4", "wav": "audio/wav", "mp3": "audio/mpeg",
}
_W = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_S = "application/vnd.openxmlformats-officedocument.spreadsheetml."
_P = "application/vnd.openxmlformats-officedocument.presentationml."
CT_PATTERNS = [
    (r"word/styles\.xml$", _W + "styles+xml"), (r"word/settings\.xml$", _W + "settings+xml"),
    (r"word/fontTable\.xml$", _W + "fontTable+xml"), (r"word/webSettings\.xml$", _W + "webSettings+xml"),
    (r"word/numbering\.xml$", _W + "numbering+xml"), (r"word/footnotes\.xml$", _W + "footnotes+xml"),
    (r"word/endnotes\.xml$", _W + "endnotes+xml"), (r"word/comments\.xml$", _W + "comments+xml"),
    (r"word/header\d+\.xml$", _W + "header+xml"), (r"word/footer\d+\.xml$", _W + "footer+xml"),
    (r"word/glossary/document\.xml$", _W + "document.glossary+xml"),
    (r"(^|/)theme/theme\d+\.xml$", "application/vnd.openxmlformats-officedocument.theme+xml"),
    (r"docProps/core\.xml$", "application/vnd.openxmlformats-package.core-properties+xml"),
    (r"docProps/app\.xml$", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
    (r"docProps/custom\.xml$", "application/vnd.openxmlformats-officedocument.custom-properties+xml"),
    (r"docProps/thumbnail\.jpeg$", "image/jpeg"),
    (r"xl/worksheets/sheet\d+\.xml$", _S + "worksheet+xml"),
    (r"xl/chartsheets/sheet\d+\.xml$", _S + "chartsheet+xml"),
    (r"xl/sharedStrings\.xml$", _S + "sharedStrings+xml"), (r"xl/styles\.xml$", _S + "styles+xml"),
    (r"xl/tables/table\d+\.xml$", _S + "table+xml"), (r"xl/comments\d+\.xml$", _S + "comments+xml"),
    (r"xl/pivotTables/pivotTable\d+\.xml$", _S + "pivotTable+xml"),
    (r"xl/pivotCache/pivotCacheDefinition\d+\.xml$", _S + "pivotCacheDefinition+xml"),
    (r"xl/pivotCache/pivotCacheRecords\d+\.xml$", _S + "pivotCacheRecords+xml"),
    (r"xl/externalLinks/externalLink\d+\.xml$", _S + "externalLink+xml"),
    (r"/drawings/drawing\d+\.xml$", "application/vnd.openxmlformats-officedocument.drawing+xml"),
    (r"/charts/chart\d+\.xml$", "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"),
    (r"ppt/slides/slide\d+\.xml$", _P + "slide+xml"),
    (r"ppt/slideLayouts/slideLayout\d+\.xml$", _P + "slideLayout+xml"),
    (r"ppt/slideMasters/slideMaster\d+\.xml$", _P + "slideMaster+xml"),
    (r"ppt/notesSlides/notesSlide\d+\.xml$", _P + "notesSlide+xml"),
    (r"ppt/notesMasters/notesMaster\d+\.xml$", _P + "notesMaster+xml"),
    (r"ppt/handoutMasters/handoutMaster\d+\.xml$", _P + "handoutMaster+xml"),
    (r"ppt/presProps\.xml$", _P + "presProps+xml"), (r"ppt/viewProps\.xml$", _P + "viewProps+xml"),
    (r"ppt/tableStyles\.xml$", _P + "tableStyles+xml"),
    (r"ppt/commentAuthors\.xml$", _P + "commentAuthors+xml"),
    (r"vbaProject\.bin$", "application/vnd.ms-office.vbaProject"),
]
IMAGE_EXT = {"png", "jpeg", "jpg", "gif", "bmp", "tif", "tiff", "emf", "wmf", "svg", "wdp", "jfif"}
# Parties non essentielles, supprimées si endommagées (Office sait s'en passer).
EXPENDABLE = (re.compile(r"(^|/)(calcChain\.xml|printerSettings\d*\.bin|thumbnail\.\w+)$|^customXml/|^docProps/"),)
FORMATTING_PART = re.compile(r"(styles|stylesWithEffects|settings|webSettings|fontTable|numbering|theme\d*|"
                             r"presProps|viewProps|tableStyles|commentAuthors)\.xml$")
PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
# Avertissements qpdf sans gravité (le lecteur corrige seul, le contenu est intact).
PDF_MINOR_WARNINGS = ("Resources is missing", "MediaBox", "inheritable", "unknown key")
INVALID_XML_CHARS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def detect_format(path, data):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if data[:4] == b"PK\x03\x04" or (ext in OOXML and b"PK\x03\x04" in data[:4096]):
        return ext if ext in OOXML else "docx" if b"word/" in data[:65536] else \
            "xlsx" if b"xl/" in data[:65536] else "pptx" if b"ppt/" in data[:65536] else ext
    if data[:8] == OLE_MAGIC:
        return ext if (ext in LEGACY or ext in OOXML) else "doc"
    if b"%PDF-" in data[:1024]:
        return "pdf"
    return ext


def family_of(fmt):
    if fmt in OOXML:
        return OOXML[fmt][0]
    if fmt in LEGACY:
        return LEGACY[fmt][0]
    return "pdf" if fmt == "pdf" else None


def entropy(sample):
    if not sample:
        return 0.0
    counts = [0] * 256
    for b in sample:
        counts[b] += 1
    n = len(sample)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


class Report:
    def __init__(self, path, fmt, size):
        self.data = {"name": os.path.basename(path), "format": fmt, "family": family_of(fmt), "size": size,
                     "status": "ok", "issues": [], "details": {}, "repairable": False, "recommendation": ""}

    def issue(self, level, message):
        self.data["issues"].append({"level": level, "message": message})
        rank = {"ok": 0, "warning": 1, "corrupt": 2, "irrecoverable": 3}
        target = {"info": "ok", "warning": "warning", "error": "corrupt", "fatal": "irrecoverable"}[level]
        if rank.get(target, 0) > rank.get(self.data["status"], 0):
            self.data["status"] = target

    def set(self, **kw):
        self.data.update(kw)
        return self

    def finish(self):
        d = self.data
        if d["status"] in ("corrupt", "warning") and not d["recommendation"]:
            d["repairable"] = True
            d["recommendation"] = "Réparation automatique possible."
        if d["status"] == "irrecoverable" and not d["recommendation"]:
            d["recommendation"] = "Contenu irrécupérable : restaurez la dernière version saine depuis la sauvegarde Datto."
        return d


# ---------------------------------------------------------------- diagnostic
def diagnose(path, quick=False):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        data = fh.read()
    fmt = detect_format(path, data)
    rep = Report(path, fmt, size)
    if fmt not in SUPPORTED:
        return rep.set(status="unsupported", recommendation="Format non pris en charge.").finish()
    if size == 0:
        rep.issue("fatal", "Le fichier est vide (0 octet).")
        return rep.finish()
    if data.count(0) == len(data):
        rep.issue("fatal", "Le fichier ne contient que des zéros (écriture interrompue, corruption disque ou "
                           "synchronisation défaillante).")
        return rep.finish()
    if fmt in OOXML:
        _diagnose_ooxml(data, fmt, rep, quick)
    elif fmt in LEGACY:
        _diagnose_legacy(path, data, fmt, rep)
    elif fmt == "pdf":
        _diagnose_pdf(path, data, rep, quick)
    if rep.data["status"] != "ok" and rep.data["status"] != "protected" and entropy(data[:65536]) > 7.97 \
            and not data.startswith((b"PK", OLE_MAGIC, b"%PDF")):
        rep.issue("fatal", "Contenu d'apparence aléatoire sans signature de format : fichier probablement "
                           "chiffré par un rançongiciel.")
        rep.set(recommendation="Isolez le poste à l'origine, puis restaurez depuis la sauvegarde Datto.")
    return rep.finish()


def _read_zip(data):
    """Lit l'archive ; renvoie (parts, problèmes). Bascule sur un parcours brut si l'index est détruit."""
    problems, parts, damaged = [], {}, set()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                parts[info.filename] = zf.read(info.filename)
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError) as exc:
                damaged.add(info.filename)
                problems.append(f"Élément « {info.filename} » endommagé ({_short(exc)}).")
    except zipfile.BadZipFile as exc:
        problems.append(f"Index de l'archive (répertoire central) illisible : {_short(exc)}.")
    if problems:
        for name, (content, complete) in salvage_zip_entries(data).items():
            if name not in parts or name in damaged:
                if content:
                    parts[name] = content
                if not complete:
                    damaged.add(name)
    return parts, problems, damaged


def _short(exc):
    return str(exc).split("\n")[0][:160]


def salvage_zip_entries(data):
    """Parcours brut des en-têtes locaux « PK\\x03\\x04 » (archive tronquée ou index détruit)."""
    out = {}
    pos = 0
    while True:
        i = data.find(b"PK\x03\x04", pos)
        if i < 0 or i + 30 > len(data):
            break
        try:
            _, _, flag, method, _, _, _, csize, usize, nlen, xlen = struct.unpack("<IHHHHHIIIHH", data[i:i + 30])
            name = data[i + 30:i + 30 + nlen].decode("utf-8" if flag & 0x800 else "cp437", "replace")
            start = i + 30 + nlen + xlen
            known = csize not in (0, 0xFFFFFFFF) and not flag & 8
            chunk = data[start:start + csize] if known else data[start:]
            if method == 0:
                content = chunk[:usize] if known or usize else chunk
                complete = len(content) == usize and usize > 0
                consumed = len(content)
            elif method == 8:
                d = zlib.decompressobj(-15)
                try:
                    content = d.decompress(chunk)
                except zlib.error:
                    content = b""
                complete = d.eof
                consumed = len(chunk) - len(d.unused_data) if d.eof else len(chunk)
            else:
                pos = i + 4
                continue
            if name and not name.endswith("/"):
                prev = out.get(name)
                if not prev or (not prev[1] and complete):
                    out[name] = (content, complete)
            pos = start + max(consumed, 1)
        except (struct.error, ValueError):
            pos = i + 4
    return out


def _parse_xml(raw):
    """Analyse stricte ; renvoie (racine, erreur)."""
    try:
        return etree.fromstring(raw, etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)), None
    except etree.XMLSyntaxError as exc:
        return None, exc


def _recover_xml(raw):
    raw = INVALID_XML_CHARS.sub(b"", raw)
    root, err = _parse_xml(raw)
    if root is not None:
        return root, "caractères de contrôle invalides supprimés"
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        root = None
    return root, "structure XML reconstruite (balises refermées, fragments illisibles écartés)" if root is not None else None


def _rels_of(parts, rels_name):
    root, _ = _parse_xml(parts.get(rels_name, b""))
    if root is None:
        root, _ = _recover_xml(parts.get(rels_name, b"")) if rels_name in parts else (None, None)
    return root


def _rels_source_dir(rels_name):
    # word/_rels/document.xml.rels → word ; _rels/.rels → ""
    return posixpath.dirname(posixpath.dirname(rels_name))


def _resolve_target(base_dir, target):
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target)).lstrip("./") if base_dir else posixpath.normpath(target)


def _main_part(parts, fmt):
    rels = _rels_of(parts, "_rels/.rels")
    if rels is not None:
        for r in rels:
            if r.get("Type") == OFFICE_DOC_REL and r.get("Target"):
                return _resolve_target("", r.get("Target"))
    return OOXML[fmt][1]


def _diagnose_ooxml(data, fmt, rep, quick):
    if etree is None:
        rep.issue("warning", "Module lxml absent : diagnostic limité.")
    if data[:4] != b"PK\x03\x04":
        rep.issue("error", "Données parasites en début de fichier (en-tête ZIP déplacé).")
    parts, problems, damaged = _read_zip(data)
    for p in problems:
        rep.issue("error", p)
    if not parts:
        rep.issue("fatal", "Aucun élément du document n'a pu être extrait.")
        return
    rep.data["details"]["parts"] = len(parts)
    if "[Content_Types].xml" not in parts:
        rep.issue("error", "Table des types de contenu ([Content_Types].xml) manquante.")
    if "_rels/.rels" not in parts:
        rep.issue("error", "Relations racine (_rels/.rels) manquantes.")
    main = _main_part(parts, fmt)
    if main not in parts:
        rep.issue("error" if _text_from_parts(parts, fmt) else "fatal",
                  f"Partie principale « {main} » introuvable.")
    if etree is None:
        return
    to_check = [n for n in parts if n.endswith((".xml", ".rels"))]
    if quick:
        to_check = [n for n in to_check if n.endswith(".rels") or n in (main, "[Content_Types].xml")
                    or n.startswith(("xl/worksheets/", "ppt/slides/"))]
    bad_xml = 0
    for name in to_check:
        root, err = _parse_xml(parts[name])
        if root is None:
            bad_xml += 1
            if bad_xml <= 15:
                rep.issue("error", f"XML invalide dans « {name} » : {_short(err)}.")
    if bad_xml > 15:
        rep.issue("error", f"… et {bad_xml - 15} autre(s) partie(s) XML invalide(s).")
    for rels_name in [n for n in parts if n.endswith(".rels")]:
        root = _rels_of(parts, rels_name)
        if root is None:
            continue
        base = _rels_source_dir(rels_name)
        for r in root:
            if r.get("TargetMode") == "External" or not r.get("Target"):
                continue
            target = _resolve_target(base, r.get("Target"))
            if target not in parts:
                rep.issue("warning" if _is_expendable(target) else "error",
                          f"Lien interne cassé : « {rels_name} » → « {target} » absent.")
    if fmt.startswith("xl") and "xl/calcChain.xml" in damaged:
        rep.issue("warning", "Chaîne de calcul Excel endommagée (sera reconstruite par Excel).")
    rep.data["details"]["main_part"] = main


def _diagnose_legacy(path, data, fmt, rep):
    fam, modern, stream = LEGACY[fmt]
    if data[:8] != OLE_MAGIC:
        rep.issue("fatal", "Signature du format binaire Office absente : ce n'est pas (ou plus) un document "
                           f"{FAMILY_LABEL[fam]} valide.")
        return
    if (len(data) - 512) % 512:
        rep.issue("warning", "Taille de fichier non alignée sur les secteurs (fichier probablement tronqué).")
    if olefile is None:
        rep.issue("info", "Module olefile absent : analyse de structure limitée.")
        return
    try:
        ole = olefile.OleFileIO(data)
    except Exception as exc:  # noqa: BLE001
        rep.issue("error", f"Structure interne illisible : {_short(exc)}.")
        return
    try:
        streams = ["/".join(s) for s in ole.listdir()]
        rep.data["details"]["streams"] = streams[:40]
        if "EncryptionInfo" in streams and "EncryptedPackage" in streams:
            rep.set(status="protected", recommendation="Document protégé par mot de passe : il n'est pas "
                                                       "corrompu. Ouvrez-le avec le mot de passe.")
            return
        names = {s.split("/")[-1] for s in streams}
        if stream not in names and not (fmt in ("xls", "xlt") and "Book" in names):
            rep.issue("error", f"Flux principal « {stream} » manquant.")
        for s in ole.listdir():
            try:
                ole.openstream(s).read()
            except Exception as exc:  # noqa: BLE001
                rep.issue("error", f"Flux « {'/'.join(s)} » illisible : {_short(exc)}.")
    finally:
        ole.close()


def _diagnose_pdf(path, data, rep, quick):
    head = data.find(b"%PDF-")
    if head < 0:
        rep.issue("fatal", "En-tête PDF absent.")
        return
    if head > 0:
        rep.issue("error", f"{head} octet(s) parasites avant l'en-tête PDF.")
    if b"%%EOF" not in data[-4096:]:
        rep.issue("error", "Marqueur de fin %%EOF absent : fichier tronqué ou écriture interrompue.")
    if pikepdf is None:
        rep.issue("info", "Module pikepdf absent : analyse approfondie impossible.")
        return
    try:
        with pikepdf.open(path) as pdf:
            warnings = [w for w in pdf.get_warnings()]
            pages = len(pdf.pages)
            rep.data["details"]["pages"] = pages
            rep.data["details"]["version"] = pdf.pdf_version
            if warnings:
                first = _short(str(warnings[0]).replace(path, "").lstrip(":, "))
                minor = all(any(m in str(w) for m in PDF_MINOR_WARNINGS) for w in warnings)
                rep.issue("warning" if minor else "error",
                          f"{'Anomalie de structure mineure' if minor else 'Structure endommagée'} "
                          f"({len(warnings)} anomalie(s) corrigée(s) à la lecture) : {first}")
            bad = []
            limit = pages if not quick else min(pages, 50)
            for i in range(limit):
                try:
                    pdf.pages[i].obj.get("/Contents") is not None and _read_contents(pdf.pages[i])
                except Exception:  # noqa: BLE001
                    bad.append(i + 1)
            if bad:
                rep.issue("error", f"Contenu illisible sur {len(bad)} page(s) : {', '.join(map(str, bad[:20]))}.")
                rep.data["details"]["bad_pages"] = bad
            if pages == 0:
                rep.issue("error", "Aucune page lisible.")
    except pikepdf.PasswordError:
        rep.set(status="protected", recommendation="PDF protégé par mot de passe : il n'est pas corrompu.")
    except pikepdf.PdfError as exc:
        rep.issue("error", f"Le PDF ne peut pas être ouvert : {_short(str(exc).replace(path, '').lstrip(':, '))}.")


def _read_contents(page):
    contents = page.obj.get("/Contents")
    if isinstance(contents, pikepdf.Array):
        for c in contents:
            c.read_bytes()
    else:
        contents.read_bytes()


# ---------------------------------------------------------------- réparation
def output_name(path, ext=None):
    base, old = os.path.splitext(os.path.basename(path))
    return f"{base} (réparé){'.' + ext if ext else old}"


def repair(path, out_dir, libreoffice=None, out_name=None):
    """Répare `path` vers `out_dir`. Renvoie un dict : statut, actions, chemin de sortie, diagnostics."""
    before = diagnose(path)
    fmt = before["format"]
    result = {"before": before, "actions": [], "output": None, "status": "failed", "after": None}
    if before["status"] == "ok":
        result.update(status="not_needed", message="Aucune anomalie détectée : réparation inutile.")
        return result
    if before["status"] == "irrecoverable":
        result.update(message=before["recommendation"])
        return result
    if before["status"] in ("protected", "unsupported"):
        result.update(status="not_needed" if before["status"] == "protected" else "failed",
                      message=before["recommendation"])
        return result
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "rb") as fh:
        data = fh.read()
    lo = libreoffice or find_libreoffice()

    if fmt in OOXML:
        out = os.path.join(out_dir, out_name or output_name(path))
        ok = _repair_ooxml(data, fmt, out, result["actions"])
        if ok and diagnose(out)["status"] in ("ok", "warning") and _has_content(out, fmt):
            result.update(status="repaired", output=out)
        elif lo:
            conv = _libreoffice_convert(path, out_dir, fmt if fmt in ("docx", "xlsx", "pptx") else
                                        {"word": "docx", "excel": "xlsx", "powerpoint": "pptx"}[family_of(fmt)], lo)
            if conv:
                result["actions"].append("Document reconstruit par le moteur LibreOffice.")
                result.update(status="repaired", output=conv)
        if result["status"] == "failed" and os.path.exists(out):
            os.remove(out)
        if result["status"] == "failed":
            salvage = _salvage_text(data, fmt, out_dir, path)
            if salvage:
                result["actions"].append("Structure irrécupérable : le texte a été extrait dans un nouveau document.")
                result.update(status="text_salvaged", output=salvage)
    elif fmt in LEGACY:
        modern = LEGACY[fmt][1]
        if lo:
            conv = _libreoffice_convert(path, out_dir, modern, lo)
            if conv:
                result["actions"].append(f"Document relu et converti au format moderne .{modern} "
                                         "(moteur LibreOffice).")
                result.update(status="repaired", output=conv)
        if result["status"] == "failed":
            salvage = _salvage_legacy_text(data, fmt, out_dir, path)
            if salvage:
                result["actions"].append("Texte extrait des flux binaires dans un nouveau document Word "
                                         "(mise en forme perdue).")
                result.update(status="text_salvaged", output=salvage)
    elif fmt == "pdf":
        out = os.path.join(out_dir, out_name or output_name(path))
        if _repair_pdf(data, out, result["actions"], before["details"].get("bad_pages", [])):
            after = diagnose(out)
            result.update(status="repaired" if after["status"] in ("ok", "warning") else "partial", output=out)

    if result["output"]:
        result["after"] = diagnose(result["output"])
        if result["status"] == "repaired" and result["after"]["status"] not in ("ok", "warning"):
            result["status"] = "partial"
    if result["status"] == "failed":
        result["message"] = "Réparation impossible : restaurez le fichier depuis la sauvegarde Datto."
    return result


def _repair_ooxml(data, fmt, out, actions):
    if etree is None:
        actions.append("lxml indisponible : réparation XML impossible.")
        return False
    if data[:4] != b"PK\x03\x04":
        actions.append("En-tête ZIP réaligné.")
    parts, problems, damaged = _read_zip(data)
    if problems:
        actions.append(f"Archive reconstruite à partir des éléments récupérables ({len(parts)} élément(s)).")
    if not parts:
        return False

    main = _main_part(parts, fmt)

    # 1. XML : caractères invalides, balises non refermées, troncature.
    fixed_xml, dropped = 0, []
    for name in list(parts):
        if not name.endswith((".xml", ".rels")):
            continue
        root, _ = _parse_xml(parts[name])
        if root is not None and name not in damaged:
            continue
        root, _ = _recover_xml(parts[name])
        if root is None:
            if name == main:
                return False
            dropped.append(name)  # les liens vers cette partie seront nettoyés à l'étape 4
            del parts[name]
            continue
        parts[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        fixed_xml += 1
    if fixed_xml:
        actions.append(f"{fixed_xml} partie(s) XML réparée(s) (caractères invalides, balises, troncature).")

    # 2. Parties binaires endommagées non essentielles.
    for name in list(parts):
        if name in damaged and not name.endswith((".xml", ".rels")) and name in parts:
            if name.split(".")[-1].lower() in IMAGE_EXT:
                parts[name] = PLACEHOLDER_PNG
                actions.append(f"Image endommagée « {name} » remplacée par une image neutre.")
            elif _is_expendable(name):
                dropped.append(name)
                del parts[name]
    if fmt.startswith("xl") and "xl/calcChain.xml" in parts and ("xl/calcChain.xml" in damaged or problems):
        del parts["xl/calcChain.xml"]
        dropped.append("xl/calcChain.xml")
    if dropped:
        actions.append("Éléments non essentiels endommagés retirés : " + ", ".join(sorted(set(dropped))[:10]) + ".")

    if main not in parts:
        return False

    # 3. Relations racine.
    if "_rels/.rels" not in parts or _rels_of(parts, "_rels/.rels") is None:
        parts["_rels/.rels"] = _root_rels(main, parts)
        actions.append("Relations racine (_rels/.rels) régénérées.")

    # 4. Liens internes cassés.
    broken, lost = 0, []
    for rels_name in [n for n in parts if n.endswith(".rels")]:
        root = _rels_of(parts, rels_name)
        if root is None:
            continue
        base = _rels_source_dir(rels_name)
        source = posixpath.join(base, posixpath.basename(rels_name)[:-5]) if base else posixpath.basename(rels_name)[:-5]
        changed = False
        for r in list(root):
            if r.get("TargetMode") == "External" or not r.get("Target"):
                continue
            target = _resolve_target(base, r.get("Target"))
            if target in parts:
                continue
            ext = target.rsplit(".", 1)[-1].lower()
            if ext in IMAGE_EXT:
                ph = posixpath.join(posixpath.dirname(target), "vigiefs-image-manquante.png")
                parts[ph] = PLACEHOLDER_PNG
                r.set("Target", posixpath.relpath(ph, base) if base else ph)
            else:
                root.remove(r)
                _drop_rid_references(parts, source, r.get("Id"))
                if not _is_expendable(target):
                    lost.append(target)
            broken += 1
            changed = True
        if changed:
            parts[rels_name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if broken:
        actions.append(f"{broken} lien(s) interne(s) cassé(s) corrigé(s) (images manquantes remplacées, "
                       "références orphelines retirées).")
    styling = sorted({t for t in lost if FORMATTING_PART.search(t)})
    content = sorted(set(lost) - set(styling))
    if styling:
        actions.append("Parties de mise en forme perdues, remplacées par les réglages par défaut d'Office : "
                       + ", ".join(posixpath.basename(t) for t in styling[:10]) + ".")
    if content:
        actions.append("Contenu irrécupérable retiré du document : " + ", ".join(content[:10]) + ".")

    # 5. Types de contenu.
    ct, ct_changed = _content_types(parts, fmt, main)
    parts["[Content_Types].xml"] = ct
    if ct_changed:
        actions.append("Table des types de contenu reconstruite / complétée.")

    # 6. Réécriture propre de l'archive.
    order = ["[Content_Types].xml", "_rels/.rels"] + sorted(n for n in parts if n not in ("[Content_Types].xml", "_rels/.rels"))
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in order:
            zf.writestr(name, parts[name])
    os.replace(tmp, out)
    actions.append("Paquet Office réécrit et vérifié.")
    return True


def _has_content(path, fmt):
    """Un paquet valide mais vide (classeur sans feuille, document sans corps) n'est pas une réparation."""
    try:
        with zipfile.ZipFile(path) as zf:
            parts = {n: zf.read(n) for n in zf.namelist() if n.endswith(".xml")}
    except (zipfile.BadZipFile, OSError):
        return False
    main = parts.get(OOXML[fmt][1], b"")
    fam = family_of(fmt)
    if fam == "excel":
        return b"<sheet " in main or b":sheet " in main
    if fam == "word":
        return b"body" in main
    return bool(main)


def _is_expendable(name):
    return any(p.search(name) for p in EXPENDABLE)


def _drop_rid_references(parts, source, rid):
    raw = parts.get(source)
    if not raw or not rid:
        return
    root, _ = _parse_xml(raw)
    if root is None:
        return
    changed = False
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if any(k.startswith("{" + R_NS + "}") and v == rid for k, v in el.attrib.items()):
            parent = el.getparent()
            if parent is None:
                continue
            if etree.QName(el).localname == "hyperlink":
                idx = parent.index(el)
                for child in list(el):
                    parent.insert(idx, child)
                    idx += 1
            parent.remove(el)
            changed = True
    if changed:
        parts[source] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _root_rels(main, parts):
    rels = [("rId1", OFFICE_DOC_REL, main)]
    if "docProps/core.xml" in parts:
        rels.append(("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                     "docProps/core.xml"))
    if "docProps/app.xml" in parts:
        rels.append(("rId3", R_NS + "/extended-properties", "docProps/app.xml"))
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{escape(g)}"/>' for i, t, g in rels)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{REL_NS}">'
            f"{body}</Relationships>").encode()


def _content_types(parts, fmt, main):
    existing_defaults, existing_overrides, changed = {}, {}, False
    raw = parts.get("[Content_Types].xml")
    root = None
    if raw:
        root, _ = _parse_xml(raw)
        if root is None:
            root, _ = _recover_xml(raw)
            changed = True
    if root is not None:
        for el in root:
            if not isinstance(el.tag, str):
                continue
            tag = etree.QName(el).localname
            if tag == "Default" and el.get("Extension"):
                existing_defaults[el.get("Extension").lower()] = el.get("ContentType")
            elif tag == "Override" and el.get("PartName"):
                existing_overrides[el.get("PartName").lstrip("/")] = el.get("ContentType")
    else:
        changed = True
    defaults = dict(existing_defaults)
    overrides = {k: v for k, v in existing_overrides.items() if k in parts}
    if len(overrides) != len(existing_overrides):
        changed = True
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if name in overrides:
            continue
        if name == main:
            overrides[name] = OOXML[fmt][2]
            changed = True
            continue
        specific = next((ct for pat, ct in CT_PATTERNS if re.search(pat, name)), None)
        if specific and ext in ("xml", "bin", "jpeg"):
            if defaults.get(ext) != specific:
                overrides[name] = specific
                changed = True
            continue
        if ext not in defaults:
            defaults[ext] = CT_DEFAULTS.get(ext, "application/octet-stream")
            changed = True
    if overrides.get(main) not in (OOXML[fmt][2],) and main in overrides and not overrides[main].endswith("main+xml"):
        overrides[main] = OOXML[fmt][2]
        changed = True
    body = "".join(f'<Default Extension="{escape(e)}" ContentType="{escape(c)}"/>' for e, c in sorted(defaults.items()))
    body += "".join(f'<Override PartName="/{escape(p)}" ContentType="{escape(c)}"/>' for p, c in sorted(overrides.items()))
    xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="{CT_NS}">{body}</Types>'
    return xml.encode(), changed


def _repair_pdf(data, out, actions, bad_pages):
    if pikepdf is None:
        actions.append("pikepdf indisponible.")
        return False
    head = data.find(b"%PDF-")
    if head < 0:
        return False
    cleaned = data[head:]
    if head:
        actions.append(f"{head} octet(s) parasites retirés avant l'en-tête.")
    if b"%%EOF" not in cleaned[-4096:]:
        cleaned += b"\n%%EOF\n"
        actions.append("Fin de fichier manquante restaurée.")
    tmp_in = out + ".src.pdf"
    with open(tmp_in, "wb") as fh:
        fh.write(cleaned)
    try:
        pikepdf.open(tmp_in, attempt_recovery=True).close()
    except pikepdf.PdfError:
        rebuilt = _rebuild_page_tree(cleaned)
        if rebuilt is None:
            os.remove(tmp_in)
            actions.append("Aucune page exploitable : le catalogue et l'arborescence des pages sont perdus.")
            return False
        with open(tmp_in, "wb") as fh:
            fh.write(rebuilt[0])
        actions.append(f"Catalogue du document perdu : arborescence reconstruite à partir des {rebuilt[1]} "
                       "page(s) retrouvée(s) dans le fichier.")
    try:
        with pikepdf.open(tmp_in, attempt_recovery=True) as pdf:
            if pdf.get_warnings():
                actions.append("Table des objets (xref) reconstruite par analyse complète du fichier.")
            blank = 0
            for i, page in enumerate(pdf.pages):
                try:
                    if page.obj.get("/Contents") is not None:
                        _read_contents(page)
                except Exception:  # noqa: BLE001
                    page.obj.Contents = pdf.make_stream(b"")
                    blank += 1
            if blank:
                actions.append(f"{blank} page(s) au contenu irrécupérable remplacée(s) par une page blanche "
                               "(mise en page et numérotation conservées).")
            pdf.remove_unreferenced_resources()
            pdf.save(out, fix_metadata_version=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
            actions.append(f"Document réécrit proprement ({len(pdf.pages)} page(s)).")
        return True
    except pikepdf.PdfError as exc:
        actions.append(f"Échec de la reconstruction : {_short(exc)}.")
        return False
    finally:
        if os.path.exists(tmp_in):
            os.remove(tmp_in)


PDF_OBJ = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
PDF_PAGE = re.compile(rb"(\d+)\s+0\s+obj\s*<<(?:(?!endobj).){0,4000}?/Type\s*/Page(?![a-zA-Z])", re.S)


def _rebuild_page_tree(data):
    """PDF tronqué sans catalogue ni trailer : recrée /Pages, /Catalog et trailer à partir des pages trouvées."""
    pages = []
    for m in PDF_PAGE.finditer(data):
        if m.group(1) not in pages:
            pages.append(m.group(1))
    if not pages:
        return None
    max_obj = max(int(m.group(1)) for m in PDF_OBJ.finditer(data))
    tree, cat = max_obj + 1, max_obj + 2
    kids = b" ".join(p + b" 0 R" for p in pages)
    tail = (b"\n%d 0 obj\n<< /Type /Pages /Kids [ %s ] /Count %d >>\nendobj\n" % (tree, kids, len(pages))
            + b"%d 0 obj\n<< /Type /Catalog /Pages %d 0 R >>\nendobj\n" % (cat, tree)
            + b"trailer\n<< /Size %d /Root %d 0 R >>\n%%%%EOF\n" % (cat + 1, cat))
    body = data.rsplit(b"%%EOF", 1)[0] if data.rstrip().endswith(b"%%EOF") else data
    return body + tail, len(pages)


# ---------------------------------------------------------------- LibreOffice
def find_libreoffice(configured=None):
    candidates = [configured] if configured else []
    candidates += [shutil.which("soffice"), shutil.which("libreoffice")]
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def _libreoffice_convert(path, out_dir, target_ext, lo):
    work = tempfile.mkdtemp(prefix="vigiefs-lo-")
    try:
        profile = "file:///" + os.path.join(work, "profil").replace("\\", "/").lstrip("/")
        cmd = [lo, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nolockcheck",
               "--convert-to", target_ext, "--outdir", work, path]
        subprocess.run(cmd, capture_output=True, timeout=180)
        produced = os.path.join(work, os.path.splitext(os.path.basename(path))[0] + "." + target_ext)
        if not os.path.isfile(produced) or os.path.getsize(produced) == 0:
            return None
        dest = os.path.join(out_dir, output_name(path, target_ext))
        shutil.move(produced, dest)
        if diagnose(dest)["status"] not in ("ok", "warning"):
            return None
        return dest
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- récupération du texte
def _text_from_parts(parts, fmt):
    fam = family_of(fmt)
    if fam == "word":
        raw = parts.get("word/document.xml", b"")
        return _word_paragraphs(raw)
    if fam == "excel":
        return _excel_rows(parts)
    if fam == "powerpoint":
        slides = sorted((n for n in parts if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        out = []
        for n in slides:
            texts = [_unescape(t) for t in re.findall(rb"<a:t>([^<]*)</a:t>", parts[n])]
            if texts:
                out.append(f"— Diapositive {re.findall(r'[0-9]+', n)[-1]} —")
                out.extend(texts)
        return out
    return []


def _unescape(b):
    s = b.decode("utf-8", "replace")
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _word_paragraphs(raw):
    paras = []
    for p in re.split(rb"</w:p>", raw):
        texts = re.findall(rb"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", p)
        if texts:
            paras.append("".join(_unescape(t) for t in texts))
    return paras


def _excel_rows(parts):
    shared = [_unescape(b"".join(re.findall(rb"<t[^>]*>([^<]*)</t>", si)))
              for si in re.findall(rb"<si>(.*?)</si>", parts.get("xl/sharedStrings.xml", b""), re.S)]
    sheets = {}
    for name in sorted(n for n in parts if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        rows = []
        for row in re.findall(rb"<row[^>]*>(.*?)</row>", parts[name], re.S):
            cells = []
            for attrs, body in re.findall(rb"<c([^>]*)>(.*?)</c>", row, re.S):
                ref = re.search(rb'r="([A-Z]+)\d+"', attrs)
                col = _col_index(ref.group(1).decode()) if ref else len(cells)
                v = re.search(rb"<v>([^<]*)</v>", body)
                t = re.search(rb'\bt="(\w+)"', attrs)
                val = ""
                if t and t.group(1) == b"s" and v:
                    idx = int(v.group(1))
                    val = shared[idx] if idx < len(shared) else ""
                elif t and t.group(1) == b"inlineStr":
                    val = _unescape(b"".join(re.findall(rb"<t[^>]*>([^<]*)</t>", body)))
                elif v:
                    val = _unescape(v.group(1))
                while len(cells) < col:
                    cells.append("")
                cells.append(val)
            rows.append(cells)
        sheets[name.rsplit("/", 1)[-1][:-4]] = rows
    return sheets


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _salvage_text(data, fmt, out_dir, path):
    parts, _, _ = _read_zip(data)
    content = _text_from_parts(parts, fmt)
    if family_of(fmt) == "excel" and isinstance(content, dict) and any(content.values()):
        out = os.path.join(out_dir, output_name(path, "xlsx").replace("(réparé)", "(données récupérées)"))
        write_minimal_xlsx(out, content)
        return out
    if content and isinstance(content, list):
        out = os.path.join(out_dir, output_name(path, "docx").replace("(réparé)", "(texte récupéré)"))
        write_minimal_docx(out, content, title=f"Texte récupéré de « {os.path.basename(path)} »")
        return out
    return None


def _salvage_legacy_text(data, fmt, out_dir, path):
    stream_data = data
    if olefile is not None:
        try:
            ole = olefile.OleFileIO(data)
            name = LEGACY[fmt][2]
            for s in ole.listdir():
                if s[-1] in (name, "Book"):
                    stream_data = ole.openstream(s).read()
                    break
            ole.close()
        except Exception:  # noqa: BLE001
            pass
    utf16 = [m.decode("utf-16-le") for m in re.findall(rb"(?:[\x20-\x7e\xa0-\xff]\x00){6,}",
                                                       stream_data)]
    ansi = [m.decode("cp1252", "replace") for m in re.findall(rb"[\x20-\x7e\x80-\xff\r\t]{8,}", stream_data)]
    lines = utf16 if sum(map(len, utf16)) >= sum(map(len, ansi)) else ansi
    lines = [ln.strip() for chunk in lines for ln in re.split(r"[\r\x07]", chunk) if len(ln.strip()) > 2]
    if not lines:
        return None
    out = os.path.join(out_dir, output_name(path, "docx").replace("(réparé)", "(texte récupéré)"))
    write_minimal_docx(out, lines, title=f"Texte récupéré de « {os.path.basename(path)} »")
    return out


# ---------------------------------------------------------------- écriture minimale OOXML
def write_minimal_docx(path, paragraphs, title=None):
    def para(text, bold=False):
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(_clean(text))}</w:t></w:r></w:p>'

    body = (para(title, True) if title else "") + "".join(para(p) for p in paragraphs)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}<w:sectPr/></w:body></w:document>")
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<Types xmlns="{CT_NS}"><Default Extension="rels" ContentType="{CT_DEFAULTS["rels"]}"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          f'<Override PartName="/word/document.xml" ContentType="{OOXML["docx"][2]}"/></Types>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", _root_rels("word/document.xml", {}))
        zf.writestr("word/document.xml", doc)


def write_minimal_xlsx(path, sheets):
    names = list(sheets) or ["Feuille1"]
    ct = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="{CT_NS}">'
          f'<Default Extension="rels" ContentType="{CT_DEFAULTS["rels"]}"/><Default Extension="xml" ContentType="application/xml"/>'
          f'<Override PartName="/xl/workbook.xml" ContentType="{OOXML["xlsx"][2]}"/>'
          f'<Override PartName="/xl/styles.xml" ContentType="{_S}styles+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="{_S}worksheet+xml"/>'
                    for i in range(len(names))) + "</Types>")
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          f'xmlns:r="{R_NS}"><sheets>'
          + "".join(f'<sheet name="{escape(n[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
          + "</sheets></workbook>")
    wb_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{REL_NS}">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="{R_NS}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                         for i in range(len(names)))
               + f'<Relationship Id="rId{len(names) + 1}" Type="{R_NS}/styles" Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
              '</styleSheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", _root_rels("xl/workbook.xml", {}))
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", styles)
        for i, n in enumerate(names):
            rows = []
            for r, cells in enumerate(sheets.get(n, []) if sheets else []):
                cs = []
                for c, val in enumerate(cells):
                    if val == "":
                        continue
                    ref = f"{_col_letters(c)}{r + 1}"
                    if re.fullmatch(r"-?\d+(\.\d+)?(E[+-]?\d+)?", str(val)):
                        cs.append(f'<c r="{ref}"><v>{val}</v></c>')
                    else:
                        cs.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(_clean(val))}</t></is></c>')
                rows.append(f'<row r="{r + 1}">{"".join(cs)}</row>')
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        f'<sheetData>{"".join(rows)}</sheetData></worksheet>')


def _col_letters(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _clean(text):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))
