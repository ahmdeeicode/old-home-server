#!/usr/bin/env bash
# Old-Home bootstrap — the ONLY command to type on a fresh Ubuntu install.
#
#   curl -fsSL https://raw.githubusercontent.com/ahmdeeicode/old-home-server/main/bootstrap.sh | sudo bash
#
# 1. makes sure SSH is installed and running
# 2. enables the root account (asks you for its password) and allows root SSH login,
#    protected by fail2ban (5 wrong passwords → 1 hour ban)
# 3. installs the Old-Home manager (aaPanel is installed later from the manager's
#    "التثبيت" page by pasting the official aaPanel command)
#
# Safe to re-run. Your normal user keeps working as a fallback login.
set -euo pipefail

REPO="https://github.com/ahmdeeicode/old-home-server.git"
DIR=/opt/oldhome
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-oldhome-root.conf

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "شغّله بـ sudo:  curl -fsSL <url> | sudo bash"
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) die "نظام غير مدعوم: $ID (Ubuntu/Debian فقط)";; esac

step "SSH"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server git curl >/dev/null
systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1
ok "SSH يعمل"

step "حساب root"
if [ -n "${ROOT_PASSWORD:-}" ]; then
  pw="$ROOT_PASSWORD"
elif passwd -S root 2>/dev/null | awk '{print $2}' | grep -q '^P$' && [ "${RESET_ROOT_PASSWORD:-}" != 1 ]; then
  pw=""
  ok "root له كلمة مرور مسبقاً — لم تتغير (لتغييرها: RESET_ROOT_PASSWORD=1)"
else
  while :; do
    read -r -s -p "    اكتب كلمة مرور جديدة لحساب root: " pw </dev/tty; echo
    [ ${#pw} -ge 8 ] || { echo "    ✗ 8 أحرف على الأقل"; continue; }
    read -r -s -p "    أعد كتابتها للتأكيد: " pw2 </dev/tty; echo
    [ "$pw" = "$pw2" ] && break || echo "    ✗ غير متطابقتين، حاول مرة أخرى"
  done
fi
if [ -n "$pw" ]; then
  echo "root:$pw" | chpasswd
  passwd -u root >/dev/null 2>&1 || true
  ok "كلمة مرور root تم تعيينها"
fi
unset pw pw2 ROOT_PASSWORD

# Drop-in named 00-* so it wins over distro/cloud-init files (first value wins).
# Validate before reloading so a bad config can never lock you out.
cat > "$SSHD_DROPIN.tmp" <<'EOF'
# Added by Old-Home bootstrap: allow root to log in over SSH with a password.
PermitRootLogin yes
PasswordAuthentication yes
KbdInteractiveAuthentication yes
EOF
mv "$SSHD_DROPIN.tmp" "$SSHD_DROPIN"
if sshd -t 2>/tmp/sshd-check.txt; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
  ok "دخول root عبر SSH مسموح ($(sshd -T 2>/dev/null | awk '/^permitrootlogin/{print $2}'))"
else
  rm -f "$SSHD_DROPIN"
  die "إعداد SSH غير صالح، تم التراجع: $(cat /tmp/sshd-check.txt)"
fi

step "حماية SSH من التخمين (fail2ban)"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban >/dev/null
# 5 wrong passwords within 10 min → that IP is banned for 1 hour.
# 127.0.0.1 is ignored: SSH over a Cloudflare tunnel arrives from localhost.
cat > /etc/fail2ban/jail.d/oldhome-sshd.local <<'JAIL'
# Added by Old-Home bootstrap
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
ignoreip = 127.0.0.1/8 ::1
JAIL
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban
sleep 2
if fail2ban-client status sshd >/dev/null 2>&1; then
  ok "fail2ban يحمي SSH (5 محاولات خاطئة ← حظر ساعة)"
else
  echo "    ⚠ fail2ban لم يبدأ — راجع: journalctl -u fail2ban"
fi

step "تنزيل Old-Home"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q --ff-only && ok "تم التحديث"
else
  git clone -q "$REPO" "$DIR" && ok "تم التنزيل إلى $DIR"
fi

step "تثبيت اللوحة"
OLDHOME_SKIP_AAPANEL=1 OLDHOME_YES=1 bash "$DIR/install.sh"
