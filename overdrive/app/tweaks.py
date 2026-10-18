"""Optimisations Windows réversibles : chaque modification sauvegarde la valeur d'origine (data/tweaks_backup.json)."""
import json
import os
import re
import threading

from . import journal
from .config import DATA_DIR
from .system import IS_WINDOWS, NotSupported, is_admin, run

if IS_WINDOWS:
    import winreg

    HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    TYPES = {"DWORD": winreg.REG_DWORD, "SZ": winreg.REG_SZ, "BINARY": winreg.REG_BINARY}

BACKUP_PATH = os.path.join(DATA_DIR, "tweaks_backup.json")
_lock = threading.Lock()
ULTIMATE_PLAN = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PLAN = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED_PLAN = "381b4222-f694-41f0-9685-ff5bb260df2e"

MM = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
GAMES = MM + r"\Tasks\Games"
FSE = r"System\GameConfigStore"


def R(hive, path, name, value, typ="DWORD"):
    return {"hive": hive, "path": path, "name": name, "value": value, "type": typ}


# impact: "fps" | "latence" | "réseau" | "système" ; risk: "faible" | "moyen"
TWEAKS = [
    {"id": "power_plan", "category": "Performance", "name": "Plan d'alimentation Performances ultimes",
     "description": "Active le plan caché « Ultimate Performance » : fréquences CPU maximales, aucune mise en veille des cœurs. "
                    "Sur portable, à utiliser branché sur secteur.", "impact": "fps", "risk": "faible", "reboot": False,
     "special": "power_plan", "recommended": True},
    {"id": "game_mode", "category": "Jeu", "name": "Mode Jeu Windows",
     "description": "Priorise le jeu au premier plan et bloque les installations de Windows Update pendant les parties.",
     "impact": "fps", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1),
             R("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1)]},
    {"id": "game_dvr_off", "category": "Jeu", "name": "Désactiver la capture Xbox Game DVR",
     "description": "L'enregistrement en arrière-plan de la Game Bar coûte des FPS et de la latence.",
     "impact": "fps", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKCU", FSE, "GameDVR_Enabled", 0),
             R("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0),
             R("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", 0)]},
    {"id": "hags", "category": "Jeu", "name": "Planification GPU à accélération matérielle (HAGS)",
     "description": "Le GPU gère sa propre mémoire vidéo : moins de latence, requis pour la génération d'images DLSS 3.",
     "impact": "latence", "risk": "faible", "reboot": True, "recommended": True,
     "ops": [R("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2)]},
    {"id": "games_priority", "category": "Jeu", "name": "Priorité multimédia maximale pour les jeux",
     "description": "Planificateur MMCSS : priorité GPU 8, CPU 6, catégorie « High » pour les tâches de jeu.",
     "impact": "fps", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKLM", GAMES, "GPU Priority", 8), R("HKLM", GAMES, "Priority", 6),
             R("HKLM", GAMES, "Scheduling Category", "High", "SZ"), R("HKLM", GAMES, "SFIO Priority", "High", "SZ"),
             R("HKLM", MM, "SystemResponsiveness", 10)]},
    {"id": "network_throttling", "category": "Réseau", "name": "Désactiver le bridage réseau multimédia",
     "description": "Windows limite les paquets réseau quand un flux multimédia est actif (NetworkThrottlingIndex).",
     "impact": "réseau", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKLM", MM, "NetworkThrottlingIndex", 0xFFFFFFFF)]},
    {"id": "mouse_accel_off", "category": "Jeu", "name": "Désactiver l'accélération de la souris",
     "description": "« Améliorer la précision du pointeur » off : visée 1:1, indispensable sur CS2.",
     "impact": "latence", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKCU", r"Control Panel\Mouse", "MouseSpeed", "0", "SZ"),
             R("HKCU", r"Control Panel\Mouse", "MouseThreshold1", "0", "SZ"),
             R("HKCU", r"Control Panel\Mouse", "MouseThreshold2", "0", "SZ")]},
    {"id": "power_throttling_off", "category": "Performance", "name": "Désactiver le Power Throttling",
     "description": "Empêche Windows de brider les processus en arrière-plan (utile pour Discord / OBS en jouant). "
                    "Augmente la consommation sur batterie.", "impact": "fps", "risk": "faible", "reboot": True, "recommended": False,
     "ops": [R("HKLM", r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", 1)]},
    {"id": "background_apps_off", "category": "Système", "name": "Bloquer les applications en arrière-plan",
     "description": "Les applis du Store (Météo, Courrier, Photos…) ne tournent plus en tâche de fond.",
     "impact": "système", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKCU", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled", 1),
             R("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "BackgroundAppGlobalToggle", 0)]},
    {"id": "visual_fx", "category": "Système", "name": "Effets visuels : meilleures performances",
     "description": "Réduit les animations et la transparence de Windows. Gain surtout sur petites configurations.",
     "impact": "système", "risk": "faible", "reboot": False, "recommended": False,
     "ops": [R("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2),
             R("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0)]},
    {"id": "fso_off", "category": "Jeu", "name": "Désactiver les optimisations plein écran",
     "description": "Force le vrai plein écran exclusif. Peut réduire l'input lag dans certains jeux ; à tester en jeu.",
     "impact": "latence", "risk": "moyen", "reboot": False, "recommended": False,
     "ops": [R("HKCU", FSE, "GameDVR_FSEBehaviorMode", 2), R("HKCU", FSE, "GameDVR_HonorUserFSEBehaviorMode", 1),
             R("HKCU", FSE, "GameDVR_FSEBehavior", 2), R("HKCU", FSE, "GameDVR_DXGIHonorFSEWindowsCompatible", 1)]},
    {"id": "timer_resolution", "category": "Performance", "name": "Résolution du timer globale (Windows 11)",
     "description": "Permet aux jeux de demander une résolution de timer de 0,5 ms globalement (frame pacing plus régulier).",
     "impact": "latence", "risk": "faible", "reboot": True, "recommended": False,
     "ops": [R("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel", "GlobalTimerResolutionRequests", 1)]},
    {"id": "notifications_off", "category": "Système", "name": "Désactiver les suggestions et pubs Windows",
     "description": "Coupe les astuces, applis suggérées et contenus promotionnels du menu Démarrer.",
     "impact": "système", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKCU", r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", n, 0) for n in
             ("SubscribedContent-338388Enabled", "SubscribedContent-338389Enabled", "SubscribedContent-353694Enabled",
              "SilentInstalledAppsEnabled", "SystemPaneSuggestionsEnabled", "SoftLandingEnabled")]},
    {"id": "telemetry_low", "category": "Système", "name": "Réduire la télémétrie",
     "description": "Niveau de diagnostic minimal (données requises uniquement).",
     "impact": "système", "risk": "faible", "reboot": False, "recommended": True,
     "ops": [R("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 1),
             R("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "DoNotShowFeedbackNotifications", 1)]},
]
BY_ID = {t["id"]: t for t in TWEAKS}


def _load_backup():
    if os.path.exists(BACKUP_PATH):
        try:
            with open(BACKUP_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_backup(data):
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# --- Registre -----------------------------------------------------------------------------------------------------

def reg_read(hive, path, name):
    """Renvoie (existe, valeur, type_str)."""
    try:
        with winreg.OpenKey(HIVES[hive], path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            value, typ = winreg.QueryValueEx(k, name)
            tname = {v: k2 for k2, v in TYPES.items()}.get(typ, "SZ")
            if isinstance(value, bytes):
                value = value.hex()
            return True, value, tname
    except FileNotFoundError:
        return False, None, None


def reg_write(hive, path, name, value, typ):
    with winreg.CreateKeyEx(HIVES[hive], path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        if typ == "BINARY" and isinstance(value, str):
            value = bytes.fromhex(value)
        winreg.SetValueEx(k, name, 0, TYPES[typ], value)


def reg_delete(hive, path, name):
    try:
        with winreg.OpenKey(HIVES[hive], path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def apply_reg_ops(backup_key, ops, label):
    """Applique une liste d'opérations registre en sauvegardant l'état d'origine (une seule fois)."""
    with _lock:
        backup = _load_backup()
        if backup_key not in backup:
            saved = []
            for op in ops:
                existed, value, typ = reg_read(op["hive"], op["path"], op["name"])
                saved.append({**op, "existed": existed, "old_value": value, "old_type": typ})
            backup[backup_key] = {"kind": "reg", "ops": saved, "label": label}
            _save_backup(backup)
    for op in ops:
        reg_write(op["hive"], op["path"], op["name"], op["value"], op["type"])


def revert_backup(backup_key):
    with _lock:
        backup = _load_backup()
        entry = backup.pop(backup_key, None)
        if not entry:
            return False
        if entry["kind"] == "reg":
            for op in entry["ops"]:
                if op["existed"]:
                    reg_write(op["hive"], op["path"], op["name"], op["old_value"], op["old_type"] or op["type"])
                else:
                    reg_delete(op["hive"], op["path"], op["name"])
        elif entry["kind"] == "power_plan":
            run(["powercfg", "/setactive", entry["previous"]])
        elif entry["kind"] == "service":
            from .services import set_start_type
            set_start_type(entry["name"], entry["previous"], record=False)
        _save_backup(backup)
        return True


def has_backup(key):
    return key in _load_backup()


def store_backup(key, entry):
    with _lock:
        backup = _load_backup()
        if key not in backup:
            backup[key] = entry
            _save_backup(backup)


# --- Plan d'alimentation ------------------------------------------------------------------------------------------

def active_power_plan():
    r = run(["powercfg", "/getactivescheme"])
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\s+\((.*)\)", r["out"], re.I)
    return {"guid": m.group(1), "name": m.group(2)} if m else {"guid": None, "name": r["out"]}


def list_power_plans():
    r = run(["powercfg", "/list"])
    plans = []
    for line in r["out"].splitlines():
        m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\s+\((.*?)\)\s*(\*)?", line, re.I)
        if m:
            plans.append({"guid": m.group(1), "name": m.group(2), "active": bool(m.group(3))})
    return plans


def _ensure_ultimate_plan():
    for p in list_power_plans():
        if "ultim" in p["name"].lower() or p["guid"].lower() == ULTIMATE_PLAN:
            return p["guid"]
    r = run(["powercfg", "-duplicatescheme", ULTIMATE_PLAN])
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", r["out"], re.I)
    if m:
        return m.group(1)
    return HIGH_PLAN  # éditions où le plan ultime est indisponible


def set_power_plan(guid):
    r = run(["powercfg", "/setactive", guid])
    if not r["ok"]:
        raise RuntimeError(r["err"] or r["out"] or "powercfg a échoué")
    journal.log("tweak", f"Plan d'alimentation activé : {active_power_plan()['name']}", "success")
    return active_power_plan()


# --- API ----------------------------------------------------------------------------------------------------------

def _is_applied(t):
    if t.get("special") == "power_plan":
        cur = active_power_plan()["name"].lower()
        return "ultim" in cur or "performances élevées" in cur or "high performance" in cur
    for op in t["ops"]:
        existed, value, _ = reg_read(op["hive"], op["path"], op["name"])
        if not existed or str(value) != str(op["value"]):
            return False
    return True


def list_tweaks():
    out = []
    for t in TWEAKS:
        item = {k: v for k, v in t.items() if k != "ops"}
        if IS_WINDOWS:
            try:
                item["applied"] = _is_applied(t)
            except Exception:
                item["applied"] = False
            item["revertible"] = has_backup("tweak:" + t["id"])
        else:
            item["applied"], item["revertible"] = False, False
        out.append(item)
    return out


def apply(tweak_id):
    if not IS_WINDOWS:
        raise NotSupported("Optimisations disponibles uniquement sous Windows.")
    t = BY_ID[tweak_id]
    needs_hklm = t.get("special") or any(op["hive"] == "HKLM" for op in t.get("ops", []))
    if needs_hklm and not is_admin():
        raise PermissionError("Droits administrateur requis : relancez Overdrive via start.bat.")
    if t.get("special") == "power_plan":
        store_backup("tweak:power_plan", {"kind": "power_plan", "previous": active_power_plan()["guid"] or BALANCED_PLAN,
                                          "label": t["name"]})
        set_power_plan(_ensure_ultimate_plan())
    else:
        apply_reg_ops("tweak:" + tweak_id, t["ops"], t["name"])
    journal.log("tweak", f"Optimisation appliquée : {t['name']}" + (" (redémarrage requis)" if t["reboot"] else ""), "success")
    return {"id": tweak_id, "applied": True, "reboot": t["reboot"]}


def revert(tweak_id):
    if not IS_WINDOWS:
        raise NotSupported("Optimisations disponibles uniquement sous Windows.")
    t = BY_ID[tweak_id]
    if not revert_backup("tweak:" + tweak_id):
        raise ValueError("Aucune sauvegarde : cette optimisation n'a pas été appliquée par Overdrive.")
    journal.log("tweak", f"Optimisation annulée : {t['name']}", "warning")
    return {"id": tweak_id, "applied": False, "reboot": t["reboot"]}


def apply_recommended():
    results = []
    for t in TWEAKS:
        if t["recommended"]:
            try:
                results.append(apply(t["id"]))
            except Exception as e:
                results.append({"id": t["id"], "error": str(e)})
    return results


def revert_all():
    count = 0
    for key in list(_load_backup()):
        try:
            if revert_backup(key):
                count += 1
        except Exception as e:
            journal.log("tweak", f"Échec de restauration {key} : {e}", "error")
    journal.log("tweak", f"{count} modification(s) restaurée(s) à l'état d'origine", "warning")
    return {"reverted": count}
