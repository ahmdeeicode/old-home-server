#!/usr/bin/env bash
# Installs / updates the Old-Home Manager service on this machine.
# Safe to re-run. Usage: sudo bash install.sh
#
# The manager listens on 127.0.0.1 only. Reach it through SSH port forwarding:
#   ssh -L 8800:localhost:8800 ssh-oldhome.ssaa.site   →  http://localhost:8800
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${OLDHOME_PORT:-8800}"

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "==> packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-flask python3-yaml gunicorn curl >/dev/null

install -d -m 700 /etc/oldhome

echo "==> state"
if [ ! -f /etc/oldhome/state.json ]; then
  (cd "$DIR/manager" && python3 cli.py import)
fi
if [ ! -f /etc/oldhome/manager_password.hash ]; then
  (cd "$DIR/manager" && python3 cli.py set-password)
fi

echo "==> systemd"
cat > /etc/systemd/system/oldhome-manager.service <<EOF
[Unit]
Description=Old-Home Manager
After=network-online.target

[Service]
WorkingDirectory=$DIR/manager
ExecStart=/usr/bin/gunicorn --workers 1 --threads 8 --timeout 180 --bind 127.0.0.1:$PORT app:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable oldhome-manager >/dev/null 2>&1
systemctl restart oldhome-manager

echo "Done. Manager listening on 127.0.0.1:$PORT"
echo "Open it from your PC with:  ssh -L $PORT:localhost:$PORT ssh-oldhome.ssaa.site   then browse http://localhost:$PORT"
