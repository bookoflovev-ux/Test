#!/usr/bin/env bash
# Essai de VigieFS sur un poste macOS / Linux : jeu de données fictif (dossier data-demo).
# Le navigateur s'ouvre sur http://localhost:8470/ ; Ctrl+C pour arrêter.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    PY=""
    for v in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$v" >/dev/null 2>&1 && "$v" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)'; then
            PY="$v"; break
        fi
    done
    if [ -z "$PY" ]; then
        echo "Python 3.10 à 3.13 introuvable (macOS : brew install python@3.13 ; Linux : paquet python3)." >&2
        exit 1
    fi
    echo "Création de l'environnement Python (première fois : 1 à 2 minutes)…"
    "$PY" -m venv .venv
    .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
fi
exec .venv/bin/python -m vigiefs demo --data "$PWD/data-demo" "$@"
