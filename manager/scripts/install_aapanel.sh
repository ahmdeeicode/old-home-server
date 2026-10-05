#!/usr/bin/env bash
# Runs the OFFICIAL aaPanel installer in the background (started by the manager
# via systemd-run, so it survives the browser closing or the manager restarting).
#   install_aapanel.sh <official-script-url> [installer args...]
# The manager has already validated the URL (aapanel.com/script/*.sh) and args.
URL="$1"; shift
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG=/var/log/oldhome-aapanel-install.log
STATUS=/etc/oldhome/aapanel_install.status
SCRIPT=/root/aapanel_install.sh

echo running > "$STATUS"
{
  echo "== $(date '+%F %T') downloading $URL"
  if ! curl -fsSL "$URL" -o "$SCRIPT"; then
    echo "== download failed"; echo failed > "$STATUS"; exit 1
  fi
  YES=""
  grep -q -- '-y)' "$SCRIPT" && YES="-y"     # unattended flag, when the script supports it
  echo "== running: bash $(basename "$SCRIPT") $* $YES"
  yes y 2>/dev/null | bash "$SCRIPT" "$@" $YES
  rc=$?
  echo "== installer exit code $rc"
  if [ -d /www/server/panel ] && command -v btpython >/dev/null; then
    echo "== enabling aaPanel API for the manager (127.0.0.1 only)"
    btpython "$HERE/enable_aapanel_api.py"
    # Hand aaPanel over to its own systemd service (btpanel) so it lives in
    # its own cgroup — not this transient installer unit — and starts on boot.
    # aaPanel's installer enables ufw with only its own ports — re-assert the
    # manager's :8443 rule for the current direct-access mode.
    if [ -f /etc/oldhome/direct.json ]; then
      (cd "$HERE/.." && python3 cli.py direct "$(python3 -c 'import json;print(json.load(open("/etc/oldhome/direct.json"))["mode"])')")
    fi
    if systemctl cat btpanel >/dev/null 2>&1; then
      echo "== starting aaPanel via its service (btpanel)"
      systemctl enable btpanel >/dev/null 2>&1
      /etc/init.d/bt stop >/dev/null 2>&1
      systemctl restart btpanel && echo "== btpanel: $(systemctl is-active btpanel)"
    fi
  fi
} >>"$LOG" 2>&1

if [ -d /www/server/panel ] && [ -f /www/server/panel/data/port.pl ]; then
  echo done > "$STATUS"
else
  echo failed > "$STATUS"
fi
