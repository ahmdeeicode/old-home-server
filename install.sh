#!/usr/bin/env bash
# Old-Home: set up a server from scratch, or update an existing one.
# Safe to re-run — every step skips itself if already done.
#
#   sudo bash install.sh            # check, then install
#   sudo bash install.sh --check    # read-only report, changes nothing
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

# Read-only preflight first. `--check` stops here; otherwise blockers abort and
# warnings need confirmation (OLDHOME_YES=1 to skip the prompt).
set +e
bash "$DIR/manager/scripts/preflight.sh"
PF=$?
set -e
[ "${1:-}" = "--check" ] && exit $PF
if [ $PF -eq 2 ]; then
  echo "Aborted: fix the blockers above first."; exit 1
elif [ $PF -eq 1 ] && [ "${OLDHOME_YES:-}" != 1 ]; then
  read -r -p "توجد تنبيهات أعلاه. هل تريد المتابعة؟ (yes/no): " ans </dev/tty
  [ "$ans" = "yes" ] || { echo "Cancelled — nothing was changed."; exit 1; }
fi

step "System packages"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl git sqlite3 python3-flask python3-yaml python3-jwt python3-cryptography gunicorn openssl >/dev/null
ok "curl git sqlite3 flask gunicorn"

step "aaPanel"
if [ -d /www/server/panel ]; then
  ok "already installed"
elif [ "${OLDHOME_SKIP_AAPANEL:-}" = 1 ]; then
  ok "skipped — install it later from the manager (التثبيت page)"
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
if [ -d /www/server/panel ] && command -v btpython >/dev/null; then
  btpython "$DIR/manager/scripts/enable_aapanel_api.py"
else
  ok "aaPanel not installed yet — the manager enables the API right after installing it"
fi

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
  touch /etc/oldhome/password_is_default   # UI nags until it is changed
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

step "Direct access by IP (https://<ip>:8443)"
if [ ! -f /etc/oldhome/direct.json ]; then
  # private IP → LAN-only, public IP → public (login throttled); change in الإعدادات
  (cd "$DIR/manager" && python3 cli.py direct "${OLDHOME_DIRECT_MODE:-auto}") | sed 's/^/    /'
else
  systemctl is-enabled -q oldhome-manager-direct 2>/dev/null && systemctl restart oldhome-manager-direct
  ok "mode: $(python3 -c 'import json;print(json.load(open("/etc/oldhome/direct.json"))["mode"])')"
fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "════════════════════════════════════════════════════════════"
echo " Old-Home is ready"
echo "════════════════════════════════════════════════════════════"
if [ -d /www/server/panel ]; then
  echo " aaPanel login:"
  bt default 2>/dev/null | grep -Ei "address|username|password" | sed 's/^/   /' || true
else
  echo " aaPanel: not installed — open the manager → التثبيت → paste the official command"
fi
echo
if [ -n "$NEW_PW" ]; then
  echo " Manager password:  $NEW_PW   (save it now)"
else
  echo " Manager password:  unchanged (reset: cd $DIR/manager && python3 cli.py set-password)"
fi
DMODE=$(python3 -c 'import json;print(json.load(open("/etc/oldhome/direct.json"))["mode"])' 2>/dev/null || echo off)
if [ "$DMODE" != off ]; then
  echo " Open the manager in your browser ($DMODE):"
  for ip in $(hostname -I); do case "$ip" in *:*) ;; *) echo "   https://$ip:8443";; esac; done
  echo "   (first visit: Advanced → Proceed — self-signed certificate)"
fi
echo " Or through SSH from your PC:"
echo "   ssh -L $PORT:localhost:$PORT root@$IP"
echo "   then browse  http://localhost:$PORT"
echo
# no "ss | grep -q" here: with pipefail, grep exiting early makes ss fail (SIGPIPE)
if [ -n "$(ss -Htln 'sport = :80')" ]; then
  echo " Next: in the manager → الدومينات → ربط دومين"
else
  echo " Next: in aaPanel install LNMP (Nginx + MySQL + PHP),"
  echo "       then in the manager → الدومينات → ربط دومين"
fi
echo "════════════════════════════════════════════════════════════"
