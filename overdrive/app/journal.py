"""Journal des actions (data/journal.jsonl) affiché dans l'interface."""
import json
import os
import threading
import time
from collections import deque

from .config import DATA_DIR

PATH = os.path.join(DATA_DIR, "journal.jsonl")
_lock = threading.Lock()
_recent = deque(maxlen=300)


def _load():
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                for line in f.readlines()[-300:]:
                    _recent.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass


_load()


def log(category: str, message: str, level: str = "info", **extra):
    entry = {"ts": time.time(), "category": category, "level": level, "message": message, **extra}
    with _lock:
        _recent.append(entry)
        try:
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return entry


def recent(limit=100):
    with _lock:
        return list(_recent)[-limit:][::-1]
