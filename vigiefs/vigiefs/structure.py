"""Analyse d'architecture d'un dossier supervisé : structure, chemins, nommage, droits, hygiène.

Produit un score global (0-100), des notes par catégorie, des recommandations chiffrées et une
arborescence annotée des premiers niveaux.
"""

from collections import Counter

from . import acl
from .pathcheck import ISSUE_LABEL

WEIGHTS = {"structure": 25, "paths": 25, "naming": 15, "rights": 25, "hygiene": 10}
CAT_LABEL = {"structure": "Structure", "paths": "Longueur des chemins", "naming": "Nommage",
             "rights": "Droits d'accès", "hygiene": "Hygiène des données"}


def grade(score):
    return "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "E"


def _clamp(v):
    return max(0, min(100, round(v)))


def analyze(share, report, folder_stats, acl_snapshot=None, acl_summary=None, acl_findings=None):
    paths = (report or {}).get("paths") or {}
    rules = paths.get("rules", {})
    files = max(1, (report or {}).get("file_count", 0))
    dirs = max(1, (report or {}).get("dir_count", 0))
    recos = []

    def reco(sev, title, detail, action=None, link=None):
        recos.append({"severity": sev, "title": title, "detail": detail, "action": action, "link": link})

    # ------------------------------------------------------------ structure
    s = 100
    max_depth = paths.get("max_depth", 0)
    limit = rules.get("max_depth", 8)
    if max_depth > limit:
        s -= min(30, 5 * (max_depth - limit))
        reco("warning", "Arborescence trop profonde",
             f"{max_depth} niveaux (recommandé : {limit} au maximum) ; {paths.get('deep_items', 0)} élément(s) "
             "au-delà. Aplatissez les branches profondes : les utilisateurs s'y perdent et les chemins s'allongent.")
    crowded = paths.get("crowded", [])
    if crowded:
        s -= min(20, 5 * len(crowded))
        top = crowded[0]
        reco("info", "Dossiers surchargés",
             f"{len(crowded)} dossier(s) contiennent plus de {rules.get('crowded_items', 2000)} éléments "
             f"(ex. « {top['path'] or '(racine)'} » : {top['items']}). Répartissez par année, client ou projet.")
    root_files = paths.get("root_files", 0)
    if root_files > 20:
        s -= 10
        reco("info", "Fichiers déposés à la racine",
             f"{root_files} fichiers sont posés directement à la racine du partage : rangez-les dans des dossiers "
             "métier pour clarifier les droits et la navigation.")
    level1 = [f for f in folder_stats if f["depth"] == 1]
    if len(level1) > 30:
        s -= 10
        reco("info", "Trop de dossiers au premier niveau",
             f"{len(level1)} dossiers au premier niveau : regroupez-les (services, activités) pour une matrice "
             "de droits lisible.")
    empty = (report or {}).get("empty_dirs", {}).get("count", 0)
    if empty / dirs > 0.1 and empty > 10:
        s -= 10
        reco("info", "Nombreux dossiers vides", f"{empty} dossier(s) vides.", "Nettoyer", "cleanup")
    structure = _clamp(s)

    # ------------------------------------------------------------ chemins
    s = 100
    crit, warn, cloud = paths.get("over_crit", 0), paths.get("over_warn", 0), paths.get("over_cloud", 0)
    if crit:
        s -= 40 + min(40, crit * 2000 / files)
    elif warn:
        s -= min(30, 10 + warn * 1000 / files)
    if cloud:
        s -= 10
    if crit or warn:
        heavy = Counter(i["path"].split("/", 1)[0] for i in paths.get("longest", []))
        worst = ", ".join(f"« {k} » ({v})" for k, v in heavy.most_common(3))
        reco("critical" if crit else "warning", "Chemins trop longs",
             f"{crit} chemin(s) dépassent {rules.get('crit_len', 260)} caractères (MAX_PATH) et {warn} dépassent "
             f"{rules.get('warn_len', 240)}. Branches les plus concernées : {worst or '—'}. Raccourcissez d'abord "
             "les noms des dossiers de premier et deuxième niveau : le gain s'applique à tous les chemins en dessous.")
        reco("info", "Réduire le chemin vu par les utilisateurs",
             "Mappez un lecteur réseau ou un partage directement sur les sous-dossiers profonds (ex. P: → "
             "\\\\serveur\\Projets\\2024) et activez LongPathsEnabled sur le serveur. L'Explorateur Windows et Office "
             "restent toutefois limités à 260 caractères.")
    if cloud:
        reco("warning", "Incompatible OneDrive / SharePoint",
             f"{cloud} chemin(s) dépassent {rules.get('cloud_len', 400)} caractères : bloquant pour une migration "
             "vers SharePoint ou une synchronisation OneDrive.")
    paths_score = _clamp(s)

    # ------------------------------------------------------------ nommage
    counts = paths.get("naming", {}).get("counts", {})
    total_issues = sum(counts.values())
    naming = _clamp(100 - min(60, total_issues * 3000 / files) - (15 if total_issues else 0))
    if total_issues:
        detail = ", ".join(f"{ISSUE_LABEL.get(k, k)} : {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        reco("warning", "Noms à corriger", f"{total_issues} nom(s) non conformes — {detail}.")

    # ------------------------------------------------------------ droits
    rights = None
    if acl_summary:
        rights = acl_summary["score"]
        codes = acl_summary.get("by_code", {})
        if codes.get("open_write"):
            reco("critical", "Dossiers modifiables par tout le monde",
                 f"{codes['open_write']} entrée(s) donnent l'écriture à « tout le monde » : risque majeur en cas de "
                 "rançongiciel. Construisez une matrice de droits.", "Matrice", "matrix")
        if codes.get("direct_user"):
            reco("warning", "Droits nominatifs",
                 f"{codes['direct_user']} droit(s) accordé(s) directement à des utilisateurs : passez par des groupes "
                 "(modèle AGDLP).", "Matrice", "matrix")
        if codes.get("orphan"):
            reco("warning", "SID orphelins", f"{codes['orphan']} entrée(s) de comptes supprimés à nettoyer.", "Droits", "rights")
        if acl_summary.get("unique_acls", 0) > 25:
            reco("info", "Droits trop fragmentés",
                 f"{acl_summary['unique_acls']} listes d'accès différentes : une matrice limitée aux deux premiers "
                 "niveaux simplifie l'administration.", "Matrice", "matrix")
        if codes.get("no_system"):
            reco("critical", "SYSTÈME sans contrôle total",
                 "Le compte SYSTÈME doit avoir le contrôle total pour que la sauvegarde Datto et l'antivirus "
                 "accèdent à tous les fichiers.", "Droits", "rights")

    # ------------------------------------------------------------ hygiène
    total = max(1, (report or {}).get("total_size", 0))
    junk = (report or {}).get("junk_total", {}).get("size", 0)
    stale = (report or {}).get("stale_size", 0)
    hygiene = _clamp(100 - min(40, junk * 400 / total) - min(40, stale * 60 / total))
    if stale / total > 0.4:
        reco("info", "Beaucoup de données froides",
             f"{round(stale * 100 / total)} % du volume n'a pas été modifié depuis longtemps : archivez-le.",
             "Archiver", "archive")

    cats = {"structure": structure, "paths": paths_score, "naming": naming, "rights": rights, "hygiene": hygiene}
    avail = {k: v for k, v in cats.items() if v is not None}
    score = _clamp(sum(v * WEIGHTS[k] for k, v in avail.items()) / sum(WEIGHTS[k] for k in avail)) if avail else 0

    # ------------------------------------------------------------ arborescence annotée
    nodes = {n["path"]: n for n in (acl_snapshot or {}).get("nodes", []) if not n.get("error")}
    find_by_path = Counter()
    for f in acl_findings or []:
        if f["severity"] in ("critical", "warning"):
            find_by_path[f["path"]] += 1
    tree = []
    for f in sorted(folder_stats, key=lambda x: x["rel_path"]):
        node = nodes.get(f["rel_path"])
        tree.append({**f, "protected": node.get("protected") if node else None,
                     "explicit": sum(1 for a in node["aces"] if not a["inherited"]) if node else None,
                     "acl_issues": find_by_path.get(f["rel_path"], 0),
                     "long": f["max_path_len"] > rules.get("warn_len", 240)})

    order = {"critical": 0, "warning": 1, "info": 2}
    recos.sort(key=lambda r: order[r["severity"]])
    return {
        "score": score, "grade": grade(score),
        "categories": [{"key": k, "label": CAT_LABEL[k], "score": v, "weight": WEIGHTS[k]} for k, v in cats.items()],
        "metrics": {"files": (report or {}).get("file_count", 0), "dirs": (report or {}).get("dir_count", 0),
                    "max_depth": max_depth, "deep_items": paths.get("deep_items", 0), "over_crit": crit,
                    "over_warn": warn, "over_cloud": cloud, "naming_issues": total_issues, "root_files": root_files,
                    "level1": len(level1), "crowded": len(crowded), "empty_dirs": empty,
                    "depth_files": paths.get("depth_files", {}), "acl_unique": (acl_summary or {}).get("unique_acls"),
                    "acl_protected": (acl_summary or {}).get("protected")},
        "recommendations": recos, "tree": tree,
        "longest": paths.get("longest", [])[:100], "naming": paths.get("naming", {"counts": {}, "samples": []}),
        "rules": rules, "prefix": paths.get("prefix"),
        "level_labels": acl.LEVEL_LABEL,
    }
