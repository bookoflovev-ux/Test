"""Bibliothèque de jeux : détection (Steam, Epic, GOG, Ubisoft, Riot…), suivi du temps de jeu et
profils de performance appliqués automatiquement au lancement de chaque jeu (et retirés à la fermeture)."""
import glob
import json
import os
import re
import threading
import time

import psutil

from . import asus, config, journal, memory, processes, tweaks
from .config import DATA_DIR
from .system import IS_WINDOWS, env_path

PATH = os.path.join(DATA_DIR, "games.json")
GPU_PREF = r"Software\Microsoft\DirectX\UserGpuPreferences"
_lock = threading.Lock()

# exe (minuscules) : (nom, type, conseil). type « competitif » = latence d'abord, « solo » = fluidité/qualité.
CATALOG = {
    "cs2.exe": ("Counter-Strike 2", "competitif", "Voir la page Mode Jeu · CS2 : autoexec, Reflex + Boost, MSAA 2x/4x."),
    "valorant-win64-shipping.exe": ("VALORANT", "competitif", "Limitez les FPS à ~2x la fréquence de l'écran, Reflex « Activé + Boost », multithread activé, détails matériaux bas."),
    "league of legends.exe": ("League of Legends", "competitif", "Limite FPS 240 ou « illimité », ombres off, mode plein écran ; le jeu est très dépendant du CPU."),
    "fortniteclient-win64-shipping.exe": ("Fortnite", "competitif", "Mode « Performance » (DX11 léger), distance de vue moyenne, Reflex + Boost. En compétitif, préférez DX12 sans Nanite/Lumen."),
    "r5apex.exe": ("Apex Legends", "competitif", "fps_max via options de lancement (+fps_max 0), Reflex « Activé + Boost », ombres basses, distance de vue des modèles élevée."),
    "r5apex_dx12.exe": ("Apex Legends", "competitif", "fps_max via options de lancement, Reflex « Activé + Boost », ombres basses."),
    "cod.exe": ("Call of Duty", "competitif", "Upscaling DLSS Performance si < 120 FPS, Reflex + Boost, « On-Demand Texture Streaming » désactivé."),
    "rainbowsix.exe": ("Rainbow Six Siege", "competitif", "Vulkan si instable en DX11, Reflex + Boost, ombres basses, LOD élevé."),
    "rainbowsix_vulkan.exe": ("Rainbow Six Siege", "competitif", "Reflex + Boost, ombres basses, LOD élevé."),
    "rocketleague.exe": ("Rocket League", "competitif", "Limite FPS 240+, V-Sync off, effets mondiaux désactivés."),
    "overwatch.exe": ("Overwatch 2", "competitif", "Reduce Buffering activé, Reflex + Boost, qualité basse, échelle de rendu 100 %."),
    "dota2.exe": ("Dota 2", "competitif", "API Vulkan, limite FPS 240, ombres basses."),
    "tslgame.exe": ("PUBG: Battlegrounds", "competitif", "Anti-crénelage Ultra (lisibilité), textures moyennes, le reste bas ; DX11 amélioré."),
    "marvel-win64-shipping.exe": ("Marvel Rivals", "competitif", "DLSS Performance, Reflex + Boost, effets bas."),
    "deadlock.exe": ("Deadlock", "competitif", "Options de lancement -dx11 si saccades, ombres basses."),
    "discovery.exe": ("THE FINALS", "competitif", "Destruction en « Low » si saccades, DLSS Performance."),
    "escapefromtarkov.exe": ("Escape from Tarkov", "competitif", "Très gourmand en RAM : la purge RAM et la fermeture des applis comptent beaucoup ici."),
    "bf2042.exe": ("Battlefield 2042", "competitif", "Future Frame Rendering activé, DLSS Qualité, terrain moyen."),
    "rust.exe": ("Rust", "competitif", "Graphismes bas + « gc.buffer 2048 » en console ; profite beaucoup de 32 Go de RAM."),
    "gta5.exe": ("GTA V", "solo", "MSAA off + FXAA, distance de population moyenne, herbe « Haute »."),
    "gta5_enhanced.exe": ("GTA V Enhanced", "solo", "DLSS Qualité, ray tracing off pour la fluidité."),
    "eldenring.exe": ("Elden Ring", "solo", "Jeu limité à 60 FPS : visez 60 stables, ray tracing off."),
    "cyberpunk2077.exe": ("Cyberpunk 2077", "solo", "DLSS Qualité + génération d'images si RTX 40, ray tracing « Bas » au maximum sur portable."),
    "rdr2.exe": ("Red Dead Redemption 2", "solo", "API Vulkan, qualité des arbres moyenne, MSAA off."),
    "minecraft.windows.exe": ("Minecraft (Bedrock)", "solo", "Distance de rendu 12-16 chunks, nuages et ombres off si besoin."),
    "witcher3.exe": ("The Witcher 3", "solo", "Hairworks off, DLSS Qualité."),
    "helldivers2.exe": ("Helldivers 2", "solo", "Rendu 100 %, ombres moyennes, upscaling « Qualité »."),
    "warframe.x64.exe": ("Warframe", "solo", "DX12, limite FPS au-dessus de la fréquence de l'écran."),
    "destiny2.exe": ("Destiny 2", "solo", "Profondeur de champ off, ombres moyennes."),
    "fc25.exe": ("EA SPORTS FC 25", "solo", "Rendu fenêtre plein écran, limite FPS 60/120."),
    "fc26.exe": ("EA SPORTS FC 26", "solo", "Limite FPS 60/120."),
    "starfield.exe": ("Starfield", "solo", "FSR/DLSS Qualité, ombres moyennes, foule basse."),
    "bg3.exe": ("Baldur's Gate 3", "solo", "DX11 si DX12 instable, DLSS Qualité."),
    "bg3_dx11.exe": ("Baldur's Gate 3", "solo", "DLSS Qualité."),
    "palworld-win64-shipping.exe": ("Palworld", "solo", "DLSS Qualité, ombres moyennes."),
    "forzahorizon5.exe": ("Forza Horizon 5", "solo", "Préréglage Haut, limite FPS à la fréquence de l'écran."),
}

NOT_GAMES = re.compile(r"(crash|unins|launcher|redist|setup|install|helper|update|easyanticheat|eac|battleye|vc_redist|dxsetup|"
                       r"ue4prereq|uninstall|report|webhelper|cefprocess|overlay|steamservice|bootstrap)", re.I)

PROFILES = {
    "competitif": {"label": "Compétitif", "priority": "high", "kill_bloat": True, "purge_ram": True, "power_plan": True, "gpu_high": True,
                   "description": "Latence minimale : priorité haute, applis inutiles fermées, RAM libérée, plan Performances ultimes."},
    "equilibre": {"label": "Équilibré", "priority": "above", "kill_bloat": False, "purge_ram": True, "power_plan": True, "gpu_high": True,
                  "description": "Priorité supérieure, RAM libérée, plan performances ; vos applis (Discord, Spotify…) restent ouvertes."},
    "qualite": {"label": "Qualité / solo", "priority": "normal", "kill_bloat": False, "purge_ram": True, "power_plan": True, "gpu_high": True,
                "description": "Pour les jeux solo : GPU dédié et plan performances, sans toucher aux autres applis."},
    "off": {"label": "Désactivé", "priority": None, "kill_bloat": False, "purge_ram": False, "power_plan": False, "gpu_high": False,
            "description": "Overdrive ne fait rien pour ce jeu."},
}
DEFAULT_PROFILE = {"competitif": "competitif", "solo": "qualite", "inconnu": "equilibre"}

_state = {"running": {}, "previous_plan": None}


# --- Persistance ---------------------------------------------------------------------------------------------------

def _load():
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"library": {}, "scanned": 0}


def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, PATH)


def _key(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# --- Détection des bibliothèques ------------------------------------------------------------------------------------

def _reg_subkeys(path):
    import winreg
    out = []
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | view) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    vals = {}
                    try:
                        with winreg.OpenKey(k, sub) as sk:
                            j = 0
                            while True:
                                try:
                                    n, v, _ = winreg.EnumValue(sk, j)
                                except OSError:
                                    break
                                vals[n] = v
                                j += 1
                    except OSError:
                        pass
                    out.append((sub, vals))
        except OSError:
            pass
    return out


def _steam_games():
    from .gaming import _libraries, _steam_paths
    found = []
    skip = re.compile(r"(redistributable|steamworks|proton|steam linux runtime|soundtrack|dedicated server|sdk)", re.I)
    for steam in _steam_paths():
        for lib in set(_libraries(steam)):
            for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
                try:
                    with open(acf, encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                except OSError:
                    continue
                name = re.search(r'"name"\s+"([^"]+)"', txt)
                inst = re.search(r'"installdir"\s+"([^"]+)"', txt)
                appid = re.search(r'"appid"\s+"(\d+)"', txt)
                if not (name and inst) or skip.search(name.group(1)):
                    continue
                found.append({"name": name.group(1), "platform": "Steam", "dir": os.path.join(lib, "steamapps", "common", inst.group(1)),
                              "appid": appid.group(1) if appid else None})
    return found


def _epic_games():
    found = []
    for item in glob.glob(env_path("%ProgramData%\\Epic\\EpicGamesLauncher\\Data\\Manifests\\*.item")):
        try:
            with open(item, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("bIsApplication", True) and d.get("InstallLocation"):
            exe = os.path.join(d["InstallLocation"], d.get("LaunchExecutable", "")) if d.get("LaunchExecutable") else None
            found.append({"name": d.get("DisplayName") or d.get("AppName"), "platform": "Epic Games", "dir": d["InstallLocation"], "exe": exe})
    return found


def _registry_games():
    found = []
    for sub, v in _reg_subkeys(r"SOFTWARE\GOG.com\Games"):
        if v.get("path"):
            found.append({"name": v.get("gameName") or sub, "platform": "GOG", "dir": v["path"],
                          "exe": v.get("exe") or v.get("launchCommand")})
    for sub, v in _reg_subkeys(r"SOFTWARE\Ubisoft\Launcher\Installs"):
        d = v.get("InstallDir")
        if d:
            found.append({"name": os.path.basename(os.path.normpath(d)), "platform": "Ubisoft Connect", "dir": d})
    # Programmes désinstallables publiés par les éditeurs / launchers de jeux
    publishers = re.compile(r"(riot games|blizzard|electronic arts|ea games|rockstar|bethesda|mojang|epic games|ubisoft|activision|bandai|cd projekt|xbox game studios)", re.I)
    for sub, v in _reg_subkeys(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"):
        name, pub, loc = v.get("DisplayName"), v.get("Publisher", ""), v.get("InstallLocation")
        if name and loc and publishers.search(pub) and not NOT_GAMES.search(name) and not re.search(r"(app|client|launcher|battle\.net$)", name, re.I):
            plat = "Riot" if "riot" in pub.lower() else "Battle.net" if "blizzard" in pub.lower() else "EA" if "electronic arts" in pub.lower() else pub.split()[0]
            found.append({"name": name, "platform": plat, "dir": loc})
    return found


def _known_paths():
    """Jeux du catalogue installés hors launchers classiques (Riot, Battle.net…)."""
    found = []
    roots = [r"C:\Riot Games", r"C:\Program Files\Riot Games", r"C:\Program Files (x86)\Overwatch", r"C:\Program Files (x86)\Call of Duty",
             r"C:\Program Files\EA Games", r"C:\XboxGames"]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for sub in os.listdir(root):
            d = os.path.join(root, sub)
            if os.path.isdir(d) and not NOT_GAMES.search(sub) and "riot client" not in sub.lower():
                found.append({"name": sub, "platform": "Riot" if "riot" in root.lower() else "Xbox" if "xbox" in root.lower() else "PC", "dir": d})
    return found


def scan(job=None):
    """Recense les jeux installés et fusionne avec l'historique de jeu existant."""
    detected = []
    sources = [("Steam", _steam_games), ("Epic Games", _epic_games)]
    if IS_WINDOWS:
        sources += [("GOG / Ubisoft / éditeurs", _registry_games), ("Riot / Battle.net / Xbox", _known_paths)]
    for i, (label, fn) in enumerate(sources):
        if job:
            job.update(i / len(sources), f"Recherche des jeux {label}…")
        try:
            detected += fn()
        except Exception as e:
            journal.log("games", f"Détection {label} : {e}", "warning")
    with _lock:
        data = _load()
        lib = data["library"]
        for g in detected:
            k = _key(g["name"])
            entry = lib.setdefault(k, {"name": g["name"], "playtime": 0, "sessions": 0, "last_played": None, "profile": None, "auto": True})
            entry.update({"platform": g["platform"], "dir": g.get("dir"), "installed": True})
            if g.get("exe"):
                entry["exe"] = g["exe"]
            if g.get("appid"):
                entry["appid"] = g["appid"]
            if not entry.get("kind"):
                entry["kind"] = _guess_kind(g["name"])
        data["scanned"] = time.time()
        _save(data)
    journal.log("games", f"{len(detected)} jeu(x) détecté(s) sur le PC", "success")
    return {"count": len(detected)}


def _guess_kind(name):
    low = name.lower()
    for exe, (n, kind, _) in CATALOG.items():
        if n.lower() == low or n.lower() in low or low in n.lower():
            return kind
    return "inconnu"


# --- Identification d'un processus de jeu --------------------------------------------------------------------------

def _match_process(p, lib):
    """Renvoie (clé, nom, exe) si le processus est un jeu connu ou se trouve dans le dossier d'un jeu installé."""
    name = (p.info["name"] or "").lower()
    if name in CATALOG:
        gname = CATALOG[name][0]
        return _key(gname), gname, p.info.get("exe") or name
    exe = p.info.get("exe") or ""
    if not exe or NOT_GAMES.search(os.path.basename(exe)):
        return None
    low = os.path.normcase(exe)
    for k, g in lib.items():
        d = g.get("dir")
        if d and low.startswith(os.path.normcase(os.path.join(d, ""))):
            return k, g["name"], exe
    return None


# --- Application des profils -----------------------------------------------------------------------------------------

def profile_of(entry):
    pid = entry.get("profile") or DEFAULT_PROFILE.get(entry.get("kind") or "inconnu", "equilibre")
    return pid, PROFILES[pid]


def _set_gpu_pref(exe):
    if IS_WINDOWS and exe and os.path.isabs(exe):
        try:
            tweaks.apply_reg_ops(f"gpupref:{exe.lower()}", [tweaks.R("HKCU", GPU_PREF, exe, "GpuPreference=2;", "SZ")], f"GPU dédié : {exe}")
        except OSError:
            pass


IFEO = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\{}\PerfOptions"
IFEO_CLASS = {"high": 3, "above": 6}


def set_registry_priority(exe, level):
    """Priorité CPU appliquée par Windows au lancement du jeu (clé PerfOptions) : aucun accès au processus du jeu,
    donc compatible FACEIT / Vanguard / EAC. « normal » ou None retire le réglage."""
    if not IS_WINDOWS or not exe:
        return False
    name = os.path.basename(exe).lower()
    key = f"ifeo:{name}"
    if level in IFEO_CLASS:
        tweaks.apply_reg_ops(key, [tweaks.R("HKLM", IFEO.format(name), "CpuPriorityClass", IFEO_CLASS[level])], f"Priorité {name}")
        return True
    tweaks.revert_backup(key)
    return False


def _apply(key, name, pid, exe, entry):
    prof_id, prof = profile_of(entry)
    if prof_id == "off" or not entry.get("auto", True):
        return
    done = []
    if prof["gpu_high"]:
        _set_gpu_pref(exe)
    try:
        if set_registry_priority(exe, prof["priority"]):
            done.append(f"priorité {prof['priority']} (registre)")
    except OSError:
        pass
    if prof["kill_bloat"]:
        killed = processes.kill_by_names(config.get()["gaming"]["kill_list"])
        if killed:
            done.append(f"{len(killed)} appli(s) fermée(s)")
    if IS_WINDOWS and prof["power_plan"]:
        try:
            if _state["previous_plan"] is None:
                _state["previous_plan"] = tweaks.active_power_plan()["guid"]
            tweaks.set_power_plan(tweaks._ensure_ultimate_plan())
            done.append("plan Performances ultimes")
        except Exception:
            pass
    if asus.game_started():
        done.append("mode ASUS Turbo")
    if IS_WINDOWS and prof["purge_ram"]:
        try:
            r = memory.optimize(purge_standby=True)
            done.append(f"{r['freed'] / 1024 ** 2:.0f} Mo de RAM libérés")
        except Exception:
            pass
    journal.log("games", f"{name} lancé → profil {prof['label']}" + (f" ({', '.join(done)})" if done else ""), "success")


def _restore_after_games():
    asus.game_ended()
    prev = _state["previous_plan"]
    _state["previous_plan"] = None
    if IS_WINDOWS and prev:
        try:
            tweaks.set_power_plan(prev)
            journal.log("games", "Plus aucun jeu ouvert : plan d'alimentation d'origine restauré")
        except Exception:
            pass


def _watch_once():
    with _lock:
        lib = _load()["library"]
    seen = {}
    for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            m = _match_process(p, lib)
        except (psutil.Error, OSError):
            continue
        if m and m[0] not in seen:
            seen[m[0]] = (m[1], p.pid, m[2], p.info.get("create_time") or time.time())
    now = time.time()
    updates = {}
    for key, (name, pid, exe, started) in seen.items():
        run = _state["running"].get(key)
        if run is None:
            _state["running"][key] = {"pid": pid, "since": started, "tick": now}
            updates[key] = {"name": name, "exe": exe, "delta": 0, "new": True}
            if config.get().get("games", {}).get("auto_profiles", True):
                entry = lib.get(key) or {"name": name, "kind": _guess_kind(name)}
                threading.Thread(target=_apply, args=(key, name, pid, exe, dict(entry)), daemon=True).start()
        else:
            updates[key] = {"name": name, "exe": exe, "delta": now - run["tick"], "new": False}
            run["tick"] = now
    for key in list(_state["running"]):
        if key not in seen:
            run = _state["running"].pop(key)
            journal.log("games", f"{lib.get(key, {}).get('name', key)} fermé — session de {int((now - run['since']) // 60)} min")
    if not seen and (_state["previous_plan"] or asus._game_state["previous"]):
        _restore_after_games()
    if updates:
        with _lock:
            data = _load()
            for key, u in updates.items():
                g = data["library"].setdefault(key, {"name": u["name"], "playtime": 0, "sessions": 0, "profile": None, "auto": True,
                                                     "platform": "PC", "installed": True, "kind": _guess_kind(u["name"])})
                g["playtime"] = g.get("playtime", 0) + u["delta"]
                g["sessions"] = g.get("sessions", 0) + (1 if u["new"] else 0)
                g["last_played"] = now
                if u["exe"] and os.path.isabs(u["exe"]):
                    g["exe"] = u["exe"]
            _save(data)


def _loop():
    data = _load()
    if not data.get("scanned"):
        try:
            scan()
        except Exception:
            pass
    while True:
        try:
            _watch_once()
        except Exception as e:
            journal.log("games", f"Surveillance des jeux : {e}", "warning")
        time.sleep(5)


def start():
    threading.Thread(target=_loop, daemon=True, name="games").start()


# --- API ---------------------------------------------------------------------------------------------------------------

def library():
    with _lock:
        data = _load()
    out = []
    for key, g in data["library"].items():
        prof_id, prof = profile_of(g)
        tip = next((t for e, (n, _, t) in CATALOG.items() if n.lower() == g["name"].lower()), None)
        out.append({"key": key, "name": g["name"], "platform": g.get("platform", "PC"), "kind": g.get("kind", "inconnu"),
                    "playtime": int(g.get("playtime", 0)), "sessions": g.get("sessions", 0), "last_played": g.get("last_played"),
                    "profile": prof_id, "profile_custom": bool(g.get("profile")), "auto": g.get("auto", True),
                    "running": key in _state["running"], "tip": tip, "installed": g.get("installed", True)})
    out.sort(key=lambda x: (not x["running"], -x["playtime"], x["name"].lower()))
    return {"games": out, "scanned": data.get("scanned"), "profiles": PROFILES,
            "auto_profiles": config.get().get("games", {}).get("auto_profiles", True)}


def set_profile(key, profile=None, auto=None):
    if profile is not None and profile not in PROFILES:
        raise ValueError("Profil inconnu")
    with _lock:
        data = _load()
        g = data["library"].get(key)
        if not g:
            raise KeyError("Jeu inconnu")
        if profile is not None:
            g["profile"] = profile
        if auto is not None:
            g["auto"] = bool(auto)
        _save(data)
    journal.log("games", f"{g['name']} : profil {PROFILES[profile_of(g)[0]]['label']}" + ("" if g.get("auto", True) else " (désactivé)"), "success")
    if key in _state["running"]:
        r = _state["running"][key]
        threading.Thread(target=_apply, args=(key, g["name"], r["pid"], g.get("exe"), dict(g)), daemon=True).start()
    return {"key": key, "profile": profile_of(g)[0], "auto": g.get("auto", True)}


def current():
    """Jeu(x) en cours, pour le tableau de bord et Nexus."""
    if not _state["running"]:
        return None
    data = _load()["library"]
    key = next(iter(_state["running"]))
    g = data.get(key, {"name": key})
    return {"name": g["name"], "profile": PROFILES[profile_of(g)[0]]["label"],
            "minutes": int((time.time() - _state["running"][key]["since"]) // 60)}
