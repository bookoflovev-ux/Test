"""Services Windows non essentiels (liste sûre et commentée, toujours réversible)."""
from . import journal, tweaks
from .system import IS_WINDOWS, NotSupported, as_list, is_admin, ps, ps_json

# nom: (libellé, description, démarrage conseillé, risque)
SAFE_SERVICES = {
    "DiagTrack": ("Télémétrie (Expériences utilisateur connectées)", "Envoie des données de diagnostic à Microsoft.", "Disabled", "faible"),
    "dmwappushservice": ("Routage des messages WAP", "Lié à la télémétrie.", "Disabled", "faible"),
    "SysMain": ("SysMain (Superfetch)", "Préchargement en RAM. Inutile sur SSD NVMe, peut causer des accès disque pendant le jeu.", "Manual", "faible"),
    "WSearch": ("Windows Search (indexation)", "Indexation des fichiers. La recherche du menu Démarrer sera plus lente.", "Manual", "moyen"),
    "MapsBroker": ("Cartes téléchargées", "Gestion des cartes hors-ligne.", "Disabled", "faible"),
    "RetailDemo": ("Mode démonstration magasin", "Inutile sur un PC personnel.", "Disabled", "faible"),
    "Fax": ("Télécopie", "Service de fax.", "Disabled", "faible"),
    "RemoteRegistry": ("Registre à distance", "Sécurité : désactivé recommandé.", "Disabled", "faible"),
    "WerSvc": ("Rapport d'erreurs Windows", "Envoi des rapports de plantage.", "Manual", "faible"),
    "PcaSvc": ("Assistant compatibilité des programmes", "Surveille les vieux programmes.", "Manual", "faible"),
    "Spooler": ("Spouleur d'impression", "À désactiver uniquement si vous n'avez pas d'imprimante.", "Manual", "moyen"),
    "TabletInputService": ("Clavier tactile et écriture manuscrite", "Inutile sans écran tactile.", "Manual", "moyen"),
    "XblGameSave": ("Sauvegarde Xbox Live", "Requis seulement pour les jeux Xbox / Game Pass.", "Manual", "moyen"),
    "lfsvc": ("Géolocalisation", "Service de localisation.", "Manual", "faible"),
    "wisvc": ("Windows Insider", "Inutile hors programme Insider.", "Disabled", "faible"),
}
START_TYPES = {"Automatic", "Manual", "Disabled"}


def list_services():
    if not IS_WINDOWS:
        return []
    names = ",".join(f"'{n}'" for n in SAFE_SERVICES)
    data = as_list(ps_json(f"Get-Service -Name {names} -EA SilentlyContinue | Select Name,@{{n='Status';e={{[string]$_.Status}}}},"
                           f"@{{n='StartType';e={{[string]$_.StartType}}}}"))
    out = []
    for s in data:
        label, desc, advised, risk = SAFE_SERVICES.get(s["Name"], (s["Name"], "", "Manual", "moyen"))
        out.append({"name": s["Name"], "label": label, "description": desc, "status": s["Status"], "start_type": s["StartType"],
                    "advised": advised, "risk": risk, "optimized": s["StartType"] == advised or s["StartType"] == "Disabled",
                    "revertible": tweaks.has_backup("service:" + s["Name"])})
    return out


def set_start_type(name, start_type, record=True):
    if not IS_WINDOWS:
        raise NotSupported("Disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis.")
    if name not in SAFE_SERVICES or start_type not in START_TYPES:
        raise ValueError("Service ou type de démarrage non autorisé.")
    if record:
        cur = ps(f"[string](Get-Service -Name '{name}').StartType")["out"].strip() or "Manual"
        if cur == "AutomaticDelayedStart":
            cur = "Automatic"
        tweaks.store_backup("service:" + name, {"kind": "service", "name": name, "previous": cur, "label": SAFE_SERVICES[name][0]})
    script = f"Set-Service -Name '{name}' -StartupType {start_type}"
    if start_type == "Disabled":
        script += f"; Stop-Service -Name '{name}' -Force -EA SilentlyContinue"
    r = ps(script)
    if not r["ok"]:
        raise RuntimeError(r["err"] or "Échec de la modification du service")
    journal.log("service", f"Service {name} → {start_type}", "success")
    return {"name": name, "start_type": start_type}


def optimize(name):
    return set_start_type(name, SAFE_SERVICES[name][2])


def restore(name):
    if not tweaks.revert_backup("service:" + name):
        raise ValueError("Aucune sauvegarde pour ce service.")
    journal.log("service", f"Service {name} restauré", "warning")
    return {"name": name}
