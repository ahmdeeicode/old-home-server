#!/usr/bin/env bash
# One-click update (started by the manager via systemd-run): pull from GitHub
# and re-run install.sh, which keeps all settings and restarts the manager.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG=/var/log/oldhome-update.log
STATUS=/etc/oldhome/update.status

echo running > "$STATUS"
{
  echo "== $(date '+%F %T') updating $ROOT"
  before=$(git -C "$ROOT" rev-parse --short HEAD)
  if ! git -C "$ROOT" pull --ff-only; then
    echo "== git pull failed (local changes?) — nothing was changed"
    echo failed > "$STATUS"; exit 1
  fi
  after=$(git -C "$ROOT" rev-parse --short HEAD)
  echo "== $before → $after"
  git -C "$ROOT" log --oneline "$before..$after" 2>/dev/null | head -20
  OLDHOME_SKIP_AAPANEL=1 OLDHOME_YES=1 bash "$ROOT/install.sh"
  rc=$?
  echo "== install.sh exit code $rc"
  [ $rc -eq 0 ] && echo done > "$STATUS" || echo failed > "$STATUS"
} >>"$LOG" 2>&1
