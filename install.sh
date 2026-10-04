#!/usr/bin/env bash
# Old-Home: set up a server from scratch, or update an existing one.
# Safe to re-run — every step skips itself if already done.
#
#   sudo bash install.sh
#
# Steps: packages → aaPanel → cloudflared → tunnel service template →
#        aaPanel API (localhost only) → manager state/password → manager service
#
# The manager listens on 127.0.0.1 only. Reach it through SSH port forwarding:
#   ssh -L 8800:localhost:8800 root@<server>   →  http://localhost:8800
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${OLDHOME_PORT:-8800}"
AAPANEL_URL="https://www.aapanel.com/script/install_7.0_en.sh"
LOG=/var/log/oldhome-install.log

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0"; exit 1; }
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) echo "Unsupported OS: $ID (Ubuntu/Debian only)"; exit 1;; esac

step "System packages"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl git sqlite3 python3-flask python3-yaml gunicorn >/dev/null
ok "curl git sqlite3 flask gunicorn"

step "aaPanel"
if [ -d /www/server/panel ]; then
  ok "already installed"
else
  echo "    installing (5–10 min, log: $LOG)…"
  curl -fsSL "$AAPANEL_URL" -o /tmp/aapanel_install.sh
  bash /tmp/aapanel_install.sh aapanel -y >>"$LOG" 2>&1
  ok "installed"
fi

step "cloudflared"
if command -v cloudflared >/dev/null; then
  ok "already installed ($(cloudflared --version 2>&1 | awk '{print $3}'))"
else
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    > /etc/apt/sources.list.d/cloudflared.list
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflared >/dev/null
  ok "installed"
fi

step "Tunnel service template (cloudflared@<account>)"
install -d -m 700 /etc/cloudflared
cat > /etc/systemd/system/cloudflared@.service <<'EOF'
[Unit]
Description=Cloudflare Tunnel (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/%i/config.yml tunnel run
Restart=on-failure
RestartSec=5s
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
ok "ready"

step "aaPanel API (127.0.0.1 only)"
install -d -m 700 /etc/oldhome
btpython "$DIR/manager/scripts/enable_aapanel_api.py"

step "Manager state"
if [ ! -f /etc/oldhome/state.json ]; then
  (cd "$DIR/manager" && python3 cli.py import)
else
  ok "state.json exists"
fi
NEW_PW=""
if [ ! -f /etc/oldhome/manager_password.hash ]; then
  NEW_PW=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')
  (cd "$DIR/manager" && OLDHOME_PASSWORD="$NEW_PW" python3 cli.py set-password >/dev/null)
fi

step "Manager service"
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
ok "running on 127.0.0.1:$PORT"

IP=$(hostname -I | awk '{print $1}')
echo
echo "════════════════════════════════════════════════════════════"
echo " Old-Home is ready"
echo "════════════════════════════════════════════════════════════"
echo " aaPanel login:"
bt default 2>/dev/null | grep -Ei "address|username|password" | sed 's/^/   /' || true
echo
if [ -n "$NEW_PW" ]; then
  echo " Manager password:  $NEW_PW   (save it now)"
else
  echo " Manager password:  unchanged (reset: cd $DIR/manager && python3 cli.py set-password)"
fi
echo " Open the manager from your PC:"
echo "   ssh -L $PORT:localhost:$PORT root@$IP"
echo "   then browse  http://localhost:$PORT"
echo
echo " Next: in aaPanel install LNMP (Nginx + MySQL + PHP),"
echo "       then in the manager → الدومينات → ربط دومين"
echo "════════════════════════════════════════════════════════════"
