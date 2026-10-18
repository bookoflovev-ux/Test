#!/usr/bin/env bash
# Installe VigieFS en service systemd 24 h/24 (relance automatique, chien de garde, démarrage au boot).
#
#   sudo ./scripts/install-linux.sh                    installation / mise à jour
#   sudo PORT=8470 HOST=0.0.0.0 DATA=/srv/vigiefs ./scripts/install-linux.sh
#   sudo ./scripts/install-linux.sh --uninstall        suppression du service (données conservées)
#
# RUN_AS=root (défaut) est nécessaire pour lire et modifier les ACL de tous les dossiers supervisés ;
# un compte dédié suffit pour une supervision en lecture seule.
set -euo pipefail

PREFIX=${PREFIX:-/opt/vigiefs}
DATA=${DATA:-/var/lib/vigiefs}
PORT=${PORT:-8470}
HOST=${HOST:-127.0.0.1}
RUN_AS=${RUN_AS:-root}
UNIT=/etc/systemd/system/vigiefs.service
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "À lancer en root (sudo)." >&2
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now vigiefs 2>/dev/null || true
    rm -f "$UNIT" /etc/sysctl.d/90-vigiefs.conf
    systemctl daemon-reload
    echo "Service VigieFS supprimé (données conservées dans $DATA, programme dans $PREFIX)."
    exit 0
fi

echo "==> Arrêt de l'instance existante"
systemctl stop vigiefs 2>/dev/null || true

echo "==> Copie du programme dans $PREFIX"
mkdir -p "$PREFIX"
if [ "$SRC" != "$PREFIX" ]; then
    tar -C "$SRC" --exclude=.venv --exclude=data --exclude='__pycache__' -cf - . | tar -C "$PREFIX" -xf -
fi

echo "==> Environnement Python"
[ -x "$PREFIX/.venv/bin/python" ] || python3 -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/python" -m pip install --quiet --upgrade pip
"$PREFIX/.venv/bin/python" -m pip install --quiet -r "$PREFIX/requirements.txt"

echo "==> Dossier de données $DATA"
mkdir -p "$DATA"
chown -R "$RUN_AS" "$DATA"
chmod 750 "$DATA"

echo "==> Limites inotify (surveillance temps réel de grandes arborescences)"
cat > /etc/sysctl.d/90-vigiefs.conf <<SYSCTL
fs.inotify.max_user_watches = 1048576
fs.inotify.max_user_instances = 1024
SYSCTL
sysctl --quiet --load /etc/sysctl.d/90-vigiefs.conf || true

echo "==> Unité systemd"
sed -e "s#@PREFIX@#$PREFIX#g" -e "s#@DATA@#$DATA#g" -e "s#@PORT@#$PORT#g" \
    -e "s#@HOST@#$HOST#g" -e "s#@RUN_AS@#$RUN_AS#g" "$PREFIX/scripts/vigiefs.service" > "$UNIT"
systemctl daemon-reload
systemctl enable --now vigiefs

echo "==> Vérification"
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health/live" >/dev/null 2>&1; then
        echo "VigieFS est en service : http://$( [ "$HOST" = 0.0.0.0 ] && hostname -f || echo "$HOST" ):$PORT/"
        echo "État : $PREFIX/.venv/bin/python -m vigiefs status --data $DATA"
        exit 0
    fi
    sleep 2
done
echo "VigieFS ne répond pas encore : journalctl -u vigiefs -n 50 et $DATA/run/console.log" >&2
exit 1
