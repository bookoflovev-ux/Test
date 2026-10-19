"""Valeurs par défaut des paramètres globaux et listes de filtrage."""

# Extensions bloquées par défaut (filtrage de fichiers, façon FSRM).
DEFAULT_BLOCKED_EXTENSIONS = [
    ".exe", ".msi", ".bat", ".cmd", ".scr", ".pif", ".com", ".vbs", ".js", ".jse",
    ".wsf", ".hta", ".ps1", ".iso",
]

# Extensions connues de rançongiciels (liste non exhaustive, à compléter).
RANSOMWARE_EXTENSIONS = [
    ".locky", ".zepto", ".odin", ".cerber", ".cerber3", ".crypt", ".crypted", ".cryptolocker",
    ".encrypted", ".locked", ".lockbit", ".wncry", ".wnry", ".wcry", ".wannacry",
    ".ryk", ".ryuk", ".conti", ".phobos", ".dharma", ".djvu", ".maze", ".sodinokibi",
    ".revil", ".clop", ".blackcat", ".akira", ".medusa",
    ".rhysida", ".babuk", ".makop", ".zzzzz", ".ecc", ".ezz",
    ".exx", ".crinf", ".r5a", ".xrtn", ".kraken",
    ".darkness", ".nochance", ".oshit", ".lechiffre", ".crjoker", ".gws",
    ".cbf", ".rokku", ".coverton", ".petya", ".lol!", ".omg!", ".rdm", ".rrk",
    ".encryptedRSA", ".crysis",
]

# Motifs de notes de rançon (comparaison insensible à la casse, joker * autorisé).
RANSOM_NOTE_PATTERNS = [
    "*readme*decrypt*", "*how_to_decrypt*", "*how-to-decrypt*", "*decrypt_instruction*",
    "*decrypt-instructions*", "*restore_files*", "*restore-my-files*", "*help_decrypt*",
    "*recovery_key*", "*_readme_.txt", "*!readme!*", "*your_files*", "*files_encrypted*",
    "*ransom*", "*restore-files*", "*how_to_back_files*", "*read_me_now*",
]

DEFAULT_SETTINGS = {
    "smtp": {
        "enabled": False,
        "host": "",
        "port": 587,
        "starttls": True,
        "username": "",
        "password": "",
        "sender": "vigiefs@localhost",
        "recipients": [],
        "min_severity": "warning",
    },
    "webhook": {
        "enabled": False,
        "url": "",
        "secret": "",
        "min_severity": "warning",
    },
    "ransomware": {
        "enabled": True,
        "burst_threshold": 200,
        "burst_window_sec": 60,
        "extensions": RANSOMWARE_EXTENSIONS,
        "note_patterns": RANSOM_NOTE_PATTERNS,
    },
    "screening": {
        "default_blocked_extensions": DEFAULT_BLOCKED_EXTENSIONS,
        "quarantine_dir": "",
    },
    "forecast": {
        "enabled": True,
        "warn_days": 30,
        "crit_days": 7,
    },
    "retention": {
        "events_days": 90,
        "scans_per_share": 200,
        "resolved_alerts_days": 180,
    },
    "general": {
        "company_name": "Mon entreprise IT",
        "scheduler_tick_sec": 30,
        "session_hours": 12,
    },
    "recycle": {
        "dir": "",
        "retention_days": 30,
    },
    "archive": {
        "root": "",
        "verify_hash": True,
    },
    "cleanup": {
        "temp_days": 2,
        "backup_days": 30,
        "logs_days": 90,
        "empty_days": 7,
    },
    "repair": {
        "libreoffice_path": "",
        "max_size_mb": 300,
        "reparedocs_dir": "",
        "reparedocs_output": "",
    },
    "restructure": {
        "long_name": 40,
        "short_name": 30,
        "cold_years": 3,
        "archive_folder": "_Archives",
        "unsorted_folder": "_A classer",
        "robocopy_threads": 8,
        "verify_hash": False,
        "vss_snapshot": True,
    },
    "paths": {
        "warn_len": 240,
        "crit_len": 260,
        "cloud_len": 400,
        "check_cloud": True,
        "name_max": 255,
        "max_depth": 8,
        "crowded_items": 2000,
        "arch_depth": 4,
        "realtime_alert": True,
    },
    "rights": {
        "group_prefix": "GDL",
        "groups_ou": "",
        "scheduled_scans": True,
    },
    "quotas": {
        "check_interval_min": 15,
        "unlock_below_pct": 98,
        "templates": [
            {"name": "Surveillance 500 Mo", "limit_gb": 0.5, "mode": "soft", "warn_pct": 85, "crit_pct": 95},
            {"name": "Limite 2 Go", "limit_gb": 2, "mode": "hard", "warn_pct": 85, "crit_pct": 95},
            {"name": "Limite 5 Go", "limit_gb": 5, "mode": "hard", "warn_pct": 85, "crit_pct": 95},
            {"name": "Limite 10 Go (dossier personnel)", "limit_gb": 10, "mode": "hard", "warn_pct": 80, "crit_pct": 95},
            {"name": "Limite 50 Go (service)", "limit_gb": 50, "mode": "hard", "warn_pct": 85, "crit_pct": 95},
            {"name": "Limite 100 Go", "limit_gb": 100, "mode": "hard", "warn_pct": 85, "crit_pct": 95},
            {"name": "Surveillance 1 To", "limit_gb": 1024, "mode": "soft", "warn_pct": 80, "crit_pct": 90},
        ],
    },
    "service": {
        "stall_minutes": 15,
        "probe_timeout_sec": 20,
        "max_memory_mb": 4096,
        "min_free_mb": 1024,
        "db_backup_hour": 2,
        "db_backups_keep": 7,
    },
    "sensitive": {
        "max_file_mb": 20,
        "extensions": [".txt", ".csv", ".log", ".xml", ".json", ".html", ".htm", ".md", ".docx", ".xlsx", ".pptx",
                       ".odt", ".ods", ".rtf", ".eml", ".ini", ".conf", ".cfg", ".ps1", ".bat", ".sql"],
    },
}

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
ROLES = ("viewer", "operator", "admin")
ROLE_LEVEL = {r: i for i, r in enumerate(ROLES)}


def merged_setting(db, key):
    """Retourne le paramètre `key` complété par les valeurs par défaut."""
    default = DEFAULT_SETTINGS.get(key, {})
    stored = db.get_setting(key, {}) or {}
    if isinstance(default, dict):
        out = dict(default)
        out.update(stored)
        return out
    return stored if stored else default
