"""Configuration persistante (data/config.json)."""
import copy
import json
import os
import secrets
import sys
import threading

if getattr(sys, "frozen", False):  # exécutable PyInstaller
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    DATA_DIR = os.path.join(os.path.dirname(sys.executable), "data")
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULTS = {
    "port": 8765,
    "allow_remote": True,          # accès depuis le téléphone / un autre PC (avec jeton)
    "token": "",
    "nexus": {
        "enabled": False,
        "url": "",                  # ex: https://nexus.local/api/ingest
        "api_key": "",
        "interval": 30,             # secondes
        "send_alerts": True,
    },
    "alerts": {
        "cpu_percent": 95,
        "ram_percent": 90,
        "gpu_temp": 87,
        "cpu_temp": 95,
        "disk_percent": 92,
    },
    "scheduler": {
        "auto_clean": False,
        "clean_every_hours": 24,
        "clean_categories": ["user_temp", "windows_temp", "crash_dumps", "thumbnails"],
        "auto_ram": False,
        "ram_threshold": 85,
        "auto_backup": False,
        "backup_every_hours": 24,
    },
    "gaming": {
        "kill_list": [
            "OneDrive.exe", "Teams.exe", "ms-teams.exe", "Spotify.exe", "EpicGamesLauncher.exe",
            "EADesktop.exe", "Widgets.exe", "PhoneExperienceHost.exe", "YourPhone.exe",
            "AdobeCollabSync.exe", "CCXProcess.exe", "Cortana.exe", "SkypeApp.exe",
        ],
        "autoexec": "",
    },
    "backup": {
        "sources": [],
        "destination": "",
    },
    "state": {"last_clean": 0, "last_backup": 0},
}

_lock = threading.Lock()
_config = None


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    global _config
    with _lock:
        if _config is None:
            data = {}
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    data = {}
            _config = _merge(DEFAULTS, data)
            if not _config["token"]:
                _config["token"] = secrets.token_urlsafe(18)
                _save_unlocked()
        return _config


def _save_unlocked():
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def get():
    return load()


def update(patch: dict) -> dict:
    load()
    global _config
    with _lock:
        _config = _merge(_config, patch)
        _save_unlocked()
        return _config
