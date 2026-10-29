"""Compatibilité anti-triche (FACEIT AC, Vanguard, EasyAntiCheat, BattlEye, VAC).

Règles appliquées partout dans Overdrive :
- aucun accès en écriture aux processus des jeux ni des anti-triche (pas de priorité appliquée « à chaud »,
  pas de vidage mémoire, jamais fermés) : la priorité des jeux passe par le registre (Windows l'applique au lancement) ;
- seules des opérations système globales (plan d'alimentation, registre, purge de la liste standby) sont utilisées ;
- détection des logiciels connus pour être bloqués par FACEIT (pilote WinRing0 des anciens moniteurs matériels)."""
import os

import psutil

from .system import IS_WINDOWS

# Jamais fermés, jamais modifiés (en plus des processus système critiques).
PROTECTED = {
    # anti-triche
    "faceit.exe", "faceitclient.exe", "faceitservice.exe", "faceit_ac.exe", "vgc.exe", "vgtray.exe", "vgk.sys",
    "easyanticheat.exe", "easyanticheat_eos.exe", "beservice.exe", "beservice_x64.exe", "battleye.exe", "gameguard.des",
    # plateformes et communication
    "steam.exe", "steamwebhelper.exe", "steamservice.exe", "discord.exe", "discordptb.exe", "discordcanary.exe",
    "riotclientservices.exe",
}

# Logiciels qui chargent des pilotes refusés par FACEIT / Vanguard (pilote WinRing0 vulnérable).
RISKY = {
    "librehardwaremonitor.exe": "LibreHardwareMonitor",
    "openhardwaremonitor.exe": "OpenHardwareMonitor",
    "fancontrol.exe": "FanControl",
    "throttlestop.exe": "ThrottleStop",
    "cheatengine-x86_64.exe": "Cheat Engine",
}


def is_protected(name: str) -> bool:
    from .games import CATALOG
    n = (name or "").lower()
    return n in PROTECTED or n in CATALOG


def faceit_installed():
    if not IS_WINDOWS:
        return False
    for p in (r"C:\Program Files\FACEIT AC", os.path.expandvars(r"%LOCALAPPDATA%\FACEIT"), os.path.expandvars(r"%APPDATA%\FACEIT")):
        if os.path.isdir(p):
            return True
    return any((p.info["name"] or "").lower() in ("faceit.exe", "faceitservice.exe") for p in psutil.process_iter(["name"]))


def status():
    running = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
    risky = sorted({label for exe, label in RISKY.items() if exe in running})
    anticheats = sorted({label for exe, label in (("faceit.exe", "FACEIT"), ("faceitservice.exe", "FACEIT AC"), ("vgc.exe", "Vanguard"),
                                                   ("easyanticheat.exe", "EasyAntiCheat"), ("beservice.exe", "BattlEye")) if exe in running})
    return {"faceit": faceit_installed(), "running_anticheats": anticheats, "risky": risky,
            "rules": [
                "Aucune modification des processus des jeux et des anti-triche (ni mémoire, ni priorité à chaud, ni fermeture).",
                "La priorité de CS2 passe par le registre : Windows l'applique lui-même au lancement du jeu.",
                "Aucune injection, aucun overlay, aucun pilote ajouté : seulement des réglages Windows standards.",
                "Discord, Steam et FACEIT ne sont jamais fermés par le Game Boost.",
            ]}
