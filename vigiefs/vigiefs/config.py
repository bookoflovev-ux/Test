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
