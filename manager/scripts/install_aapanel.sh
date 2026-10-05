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
  fi
} >>"$LOG" 2>&1

if [ -d /www/server/panel ] && [ -f /www/server/panel/data/port.pl ]; then
  echo done > "$STATUS"
else
  echo failed > "$STATUS"
fi
