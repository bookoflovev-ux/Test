"""Mode Jeu & CS2 : détection Steam/CS2, profil compétitif, Game Boost, autoexec et conseils ASUS TUF."""
import glob
import os
import re
import shutil
import time
import zipfile

import psutil

from . import config, journal, memory, processes, tweaks
from .config import DATA_DIR
from .system import IS_WINDOWS, NotSupported, is_admin

CS2_APPID = "730"
CS2_TWEAKS = ("power_plan", "game_mode", "game_dvr_off", "games_priority", "network_throttling", "mouse_accel_off", "hags",
              "windowed_opt", "background_apps_off", "notifications_off")
IFEO = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\cs2.exe\PerfOptions"
GPU_PREF = r"Software\Microsoft\DirectX\UserGpuPreferences"

DEFAULT_AUTOEXEC = """// ===== Overdrive — autoexec CS2 (compétitif / FPS) =====
// Placez "+exec autoexec" dans les options de lancement Steam.
fps_max 0                                  // FPS non plafonnés (ou 3 à 5 FPS sous la limite G-Sync)
fps_max_ui 120                             // limite les FPS dans les menus (moins de chauffe)
engine_low_latency_sleep_after_client_tick true
r_player_visibility_mode 1                 // « Boost player contrast »
cl_hud_telemetry_frametime_show 2          // affiche FPS / frametime
cl_hud_telemetry_ping_show 2
cl_hud_telemetry_net_misdelivery_show 2    // perte de paquets
mm_dedicated_search_maxping 80             // serveurs à ping faible uniquement
rate 786432                                // débit réseau maximal autorisé (connexion fibre / ADSL correcte)
cl_autohelp false
cl_showloadout true
snd_mute_losefocus false
// --- vos binds / réglages perso ci-dessous ---

host_writeconfig
echo "Overdrive autoexec chargé"
"""

LAUNCH_OPTIONS = "-fullscreen +exec autoexec"
LAUNCH_NOTES = [
    "+exec autoexec charge votre configuration à chaque lancement.",
    "-fullscreen force le plein écran (plus fluide que le fenêtré sans bordure sur la plupart des PC).",
    "-allow_third_party_software seulement si OBS doit capturer le jeu (désactive le mode de confiance, peut baisser le Trust Factor).",
    "Inutile ou placebo sur CS2 : -high (Overdrive règle la priorité de façon permanente), -threads, -tickrate, -novid.",
]

VIDEO_SETTINGS = [
    ("Mode d'affichage", "Plein écran", "Latence minimale"),
    ("Booster le contraste des joueurs", "Activé", "Meilleure visibilité, coût quasi nul"),
    ("Synchronisation verticale", "Désactivée", "Supprime 1 à 3 images de latence"),
    ("NVIDIA Reflex", "Activé + Boost", "Réduit fortement l'input lag (GPU NVIDIA)"),
    ("Anticrénelage MSAA", "2x ou 4x (CMAA2 si PC faible)", "Bon compromis netteté / FPS"),
    ("Qualité des ombres globales", "Moyenne ou Haute", "Voir les ombres des adversaires"),
    ("Détails des modèles / textures", "Bas", "Gros gain de FPS"),
    ("Filtrage des textures", "Anisotrope 4x", "Impact faible"),
    ("Détails des shaders", "Bas", "Gain de FPS"),
    ("Détails des particules", "Bas", "Gain dans les fumigènes/molotovs"),
    ("Occlusion ambiante", "Désactivée", "Gain de FPS"),
    ("Rendu HDR", "Performance", "Gain de FPS"),
    ("FidelityFX Super Resolution", "Désactivé (ou Qualité si < 144 FPS)", "Évite le flou"),
]

TUF_TIPS = [
    {"title": "Mode Turbo / Performance", "text": "Dans Armoury Crate (ou G-Helper, alternative gratuite et légère), "
     "passez en mode Turbo branché sur secteur : +15 à 30 % de puissance CPU/GPU. Raccourci : Fn + F5."},
    {"title": "MUX Switch : mode GPU Ultimate", "text": "Si votre TUF a un MUX switch, activez « Ultimate » (dGPU direct) : "
     "l'écran est relié directement à la carte NVIDIA, souvent +5 à 15 % de FPS et moins de latence (redémarrage requis)."},
    {"title": "Toujours brancher le secteur", "text": "Sur batterie, le GPU est fortement bridé (souvent -50 % de FPS)."},
    {"title": "Écran à fréquence max", "text": "Paramètres > Affichage > Avancé : réglez 144/165/240 Hz. Activez G-Sync dans le panneau NVIDIA."},
    {"title": "Températures", "text": "Au-delà de 90 °C le CPU se bride. Surélevez l'arrière du portable, nettoyez les ventilateurs "
     "tous les 6 à 12 mois ; un support ventilé gagne souvent 5 à 10 °C."},
    {"title": "Panneau NVIDIA", "text": "Mode de gestion de l'alimentation : « Performances maximales » ; Mode faible latence : "
     "Activé (Reflex prioritaire en jeu) ; Taille du cache de shaders : 10 Go ; Processeur graphique préféré : NVIDIA haute performance."},
    {"title": "RAM en dual channel", "text": "Beaucoup de TUF sont livrés avec une seule barrette : passer en 2 barrettes identiques "
     "peut apporter +20 à 30 % de FPS sur CS2, très sensible au CPU/RAM."},
]


def _steam_paths():
    paths = []
    if IS_WINDOWS:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            paths.append(os.path.normpath(winreg.QueryValueEx(k, name)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
        paths.append(r"C:\Program Files (x86)\Steam")
    else:
        paths += [os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/.local/share/Steam")]
    seen, out = set(), []
    for p in paths:
        if p.lower() not in seen and os.path.isdir(p):
            seen.add(p.lower())
            out.append(p)
    return out


def _libraries(steam):
    libs = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="ignore") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                libs.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return libs


def find_cs2():
    for steam in _steam_paths():
        for lib in _libraries(steam):
            game = os.path.join(lib, "steamapps", "common", "Counter-Strike Global Offensive")
            exe = os.path.join(game, "game", "bin", "win64", "cs2.exe")
            if os.path.exists(exe) or os.path.isdir(game):
                return {"installed": True, "steam": steam, "game_dir": game, "exe": exe,
                        "cfg_dir": os.path.join(game, "game", "csgo", "cfg"),
                        "user_cfgs": glob.glob(os.path.join(steam, "userdata", "*", CS2_APPID, "local", "cfg"))}
    return {"installed": False}


def status():
    cs2 = find_cs2()
    running = processes.find("cs2.exe")
    info = {"cs2": cs2, "running": bool(running), "pid": running[0].pid if running else None}
    if cs2.get("installed"):
        auto = os.path.join(cs2["cfg_dir"], "autoexec.cfg")
        info["autoexec_exists"] = os.path.exists(auto)
    if IS_WINDOWS and cs2.get("installed"):
        info["priority_set"] = tweaks.reg_read("HKLM", IFEO, "CpuPriorityClass")[1] == 3
        info["gpu_pref_set"] = "GpuPreference=2" in str(tweaks.reg_read("HKCU", GPU_PREF, cs2["exe"])[1] or "")
        info["profile_active"] = tweaks.has_backup("cs2:profile")
    try:
        b = psutil.sensors_battery()
        info["on_battery"] = bool(b and not b.power_plugged)
    except Exception:
        info["on_battery"] = False
    return info


def apply_cs2_profile():
    """Profil compétitif : optimisations recommandées + CS2 en priorité haute permanente + GPU dédié forcé."""
    if not IS_WINDOWS:
        raise NotSupported("Profil disponible uniquement sous Windows.")
    if not is_admin():
        raise PermissionError("Droits administrateur requis : relancez Overdrive avec l'icône du bureau (ou « Installer Overdrive.bat »).")
    steps = []
    for tid in CS2_TWEAKS:
        try:
            tweaks.apply(tid)
            steps.append({"label": tweaks.BY_ID[tid]["name"], "ok": True})
        except Exception as e:
            steps.append({"label": tweaks.BY_ID[tid]["name"], "ok": False, "detail": str(e)})
    cs2 = find_cs2()
    ops = [tweaks.R("HKLM", IFEO, "CpuPriorityClass", 3)]  # 3 = High, appliqué par Windows au lancement (sans toucher au jeu)
    if cs2.get("installed"):
        ops.append(tweaks.R("HKCU", GPU_PREF, cs2["exe"], "GpuPreference=2;", "SZ"))
    tweaks.apply_reg_ops("cs2:profile", ops, "Profil CS2")
    steps += [{"label": "CS2 en priorité CPU haute (via le registre, compatible FACEIT)", "ok": True},
              {"label": "CS2 forcé sur la carte graphique dédiée", "ok": cs2.get("installed", False)}]
    try:
        from .asus import is_asus
        if is_asus():
            config.update({"asus": {"turbo_in_games": True}})
            steps.append({"label": "ASUS : mode Turbo automatique pendant CS2 (sur secteur)", "ok": True})
    except Exception:
        pass
    try:
        v = optimize_video()
        steps.append({"label": f"Réglages vidéo CS2 ({len(v['changed'])} modifié(s))" if v["changed"] else "Réglages vidéo CS2 déjà optimaux",
                      "ok": True, "detail": ", ".join(v["changed"])})
    except Exception as e:
        steps.append({"label": "Réglages vidéo CS2", "ok": False, "detail": str(e)})
    journal.log("gaming", "Profil CS2 compétitif appliqué", "success")
    return {"steps": steps, "applied": [s["label"] for s in steps if s["ok"]], "reboot": True}


# Réglages vidéo modifiés dans cs2_video.txt (uniquement s'ils existent déjà dans le fichier, CS2 fermé, avec copie .bak).
VIDEO_TARGETS = {
    "setting.mat_vsync": ("0", "V-Sync désactivée"),
    "setting.r_low_latency": ("2", "NVIDIA Reflex Activé + Boost"),
    "setting.r_aoproxy_enable": ("0", "Occlusion ambiante désactivée"),
    "setting.shaderquality": ("0", "Détails des shaders bas"),
    "setting.fullscreen": ("1", "Plein écran"),
}


def optimize_video():
    cs2 = find_cs2()
    if not cs2.get("installed"):
        raise FileNotFoundError("CS2 introuvable.")
    if processes.find("cs2.exe"):
        raise RuntimeError("Fermez CS2 d'abord : il réécrit ses réglages en quittant.")
    files = [os.path.join(d, "cs2_video.txt") for d in cs2["user_cfgs"] if os.path.exists(os.path.join(d, "cs2_video.txt"))]
    if not files:
        raise FileNotFoundError("Lancez CS2 une première fois pour qu'il crée ses réglages vidéo.")
    changed = set()
    for path in files:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        new = text
        for key, (value, label) in VIDEO_TARGETS.items():
            rx = re.compile(r'("' + re.escape(key) + r'"\s+")([^"]*)(")')
            m = rx.search(new)
            if m and m.group(2) != value:
                new = rx.sub(lambda mm: mm.group(1) + value + mm.group(3), new, count=1)
                changed.add(label)
        if new != text:
            shutil.copy2(path, path + f".{time.strftime('%Y%m%d-%H%M%S')}.bak")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    if changed:
        journal.log("gaming", "Réglages vidéo CS2 optimisés : " + ", ".join(sorted(changed)), "success")
    return {"changed": sorted(changed), "files": len(files)}


def revert_cs2_profile():
    tweaks.revert_backup("cs2:profile")
    for tid in ("game_dvr_off", "games_priority", "network_throttling", "hags", "power_plan"):
        try:
            tweaks.revert(tid)
        except Exception:
            pass
    journal.log("gaming", "Profil CS2 désactivé (paramètres d'origine restaurés)", "warning")
    return {"ok": True}


def game_boost(kill=True):
    """Boost immédiat avant une partie : ferme les applis inutiles (jamais Discord, Steam ni FACEIT), libère la RAM,
    plan d'alimentation maximal."""
    report = {"killed": [], "ram": None, "power_plan": None, "cs2_priority": False}
    if kill:
        report["killed"] = processes.kill_by_names(config.get()["gaming"]["kill_list"])
    if IS_WINDOWS:
        time.sleep(1)
        try:
            report["ram"] = memory.optimize(purge_standby=True)
        except Exception as e:
            report["ram"] = {"error": str(e)}
        try:
            report["power_plan"] = tweaks.set_power_plan(tweaks._ensure_ultimate_plan())["name"]
        except Exception as e:
            report["power_plan"] = f"erreur : {e}"
        # Pas de priorité appliquée « à chaud » sur cs2.exe (compatibilité FACEIT) : la clé de registre du profil CS2 suffit.
        report["cs2_priority"] = tweaks.reg_read("HKLM", IFEO, "CpuPriorityClass")[1] == 3
    journal.log("gaming", f"Game Boost : {len(report['killed'])} application(s) fermée(s)", "success")
    return report


def get_autoexec():
    cs2 = find_cs2()
    if cs2.get("installed"):
        path = os.path.join(cs2["cfg_dir"], "autoexec.cfg")
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="ignore") as f:
                return {"content": f.read(), "path": path, "exists": True}
        return {"content": DEFAULT_AUTOEXEC, "path": path, "exists": False}
    return {"content": DEFAULT_AUTOEXEC, "path": None, "exists": False}


def save_autoexec(content: str):
    cs2 = find_cs2()
    if not cs2.get("installed"):
        raise FileNotFoundError("CS2 introuvable : installez-le via Steam.")
    os.makedirs(cs2["cfg_dir"], exist_ok=True)
    path = os.path.join(cs2["cfg_dir"], "autoexec.cfg")
    if os.path.exists(path):
        shutil.copy2(path, path + f".{time.strftime('%Y%m%d-%H%M%S')}.bak")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(content)
    journal.log("gaming", f"autoexec.cfg enregistré : {path}", "success")
    return {"path": path}


def backup_cs2_config():
    cs2 = find_cs2()
    if not cs2.get("installed"):
        raise FileNotFoundError("CS2 introuvable.")
    dest_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"cs2-config-{time.strftime('%Y%m%d-%H%M%S')}.zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in [cs2["cfg_dir"]] + cs2["user_cfgs"]:
            for root, _, files in os.walk(folder):
                for f in files:
                    if f.endswith((".cfg", ".vcfg", ".txt")):
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, os.path.dirname(os.path.dirname(folder))))
    journal.log("backup", f"Configuration CS2 sauvegardée : {dest}", "success")
    return {"path": dest}


DISCORD_TIPS = [
    {"title": "Superposition (overlay) en jeu : désactivée", "text": "Paramètres utilisateur → Superposition de jeu → désactiver. Gain de FPS et rien d'injecté dans CS2."},
    {"title": "Accélération matérielle : désactivée sur portable", "text": "Paramètres → Avancés → Accélération matérielle off : Discord n'utilise plus la carte graphique de CS2 (laisser activée si vous streamez)."},
    {"title": "Voix : qualité de service désactivée si ping instable", "text": "Voix et vidéo → « Activer la priorité de paquets élevée (QoS) » à désactiver si des micro-coupures apparaissent avec votre box."},
    {"title": "Discord n'est jamais fermé", "text": "Overdrive le protège : ni Game Boost, ni profil de jeu ne le ferment, et sa mémoire n'est pas vidée pendant la partie (pas de coupure de voix)."},
]


def info():
    from .anticheat import status as ac_status
    return {"launch_options": LAUNCH_OPTIONS, "launch_notes": LAUNCH_NOTES,
            "video_settings": [{"setting": a, "value": b, "why": c} for a, b, c in VIDEO_SETTINGS],
            "tuf_tips": TUF_TIPS, "kill_list": config.get()["gaming"]["kill_list"],
            "discord_tips": DISCORD_TIPS, "anticheat": ac_status(),
            "profile_steps": [tweaks.BY_ID[t]["name"] for t in CS2_TWEAKS] + [
                "CS2 en priorité CPU haute (registre)", "CS2 sur la carte graphique dédiée", "Mode ASUS Turbo pendant CS2 (portables ASUS)",
                "Réglages vidéo : V-Sync off, Reflex + Boost, shaders bas, occlusion ambiante off, plein écran"]}
