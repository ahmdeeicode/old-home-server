#!/usr/bin/env bash
# Old-Home uninstaller — removes ONLY what this project added.
# Never touches aaPanel, websites, databases or the cloudflared package.
#
#   sudo bash /opt/oldhome/uninstall.sh            # shows the plan, asks before doing anything
#   sudo bash /opt/oldhome/uninstall.sh --full     # also remove tunnels + their DNS records
#
# Default (manager only): tunnels and published sites KEEP WORKING.
# --full: tunnels stopped & deleted, DNS records created by the manager deleted
#         (only records pointing at THIS server's tunnels) → published sites go offline.
# A backup of /etc/oldhome and /etc/cloudflared is written first.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
FULL=0; YES=0
for a in "$@"; do
  case "$a" in
    --full) FULL=1 ;;
    --yes)  YES=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0"; exit 1; }
ask() {
  [ $YES = 1 ] && return 0
  local r=""
  printf '%s (yes/no): ' "$1"
  { read -r r </dev/tty; } 2>/dev/null || { echo; return 1; }   # no terminal → treat as "no"
  [ "$r" = yes ]
}
hdr() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
item(){ printf '  • %s\n' "$*"; }

PANEL_API=/www/server/panel/config/api.json
API_BAK=$(ls "$PANEL_API".bak-* 2>/dev/null | sort | head -1)
REALIP=/www/server/panel/vhost/nginx/0.cloudflare_realip.conf
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-oldhome-root.conf
F2B_JAIL=/etc/fail2ban/jail.d/oldhome-sshd.local

# ---------------- plan ----------------
hdr "سيُحذف (لوحة Old-Home):"
item "الخدمات: oldhome-manager (8800)، oldhome-manager-direct (8443)"
item "الكود: $DIR"
item "الإعدادات: /etc/oldhome (كلمة مرور اللوحة، الشهادة، مفتاح API، state.json)"
item "قواعد الجدار الناري للمنفذ 8443"
if [ $FULL = 1 ]; then
  hdr "وأيضاً (--full) — ستتوقف المواقع المنشورة عن العمل من الإنترنت:"
  (cd "$DIR/manager" && python3 cli.py plan-cloud 2>/dev/null) | sed 's/^/  /'
  item "مجلدات التونلات في /etc/cloudflared التي أنشأتها اللوحة"
else
  hdr "يبقى يعمل:"
  item "التونلات وروابط المواقع وSSH وaaPanel (cloudflared@<حساب>) — للإزالة الكاملة: --full"
  item "(روابط اللوحة نفسها فقط تُحذف، لأنها ستتوقف بعد حذف اللوحة)"
fi
hdr "لا يُمس إطلاقاً:"
item "aaPanel، المواقع، قواعد البيانات، برنامج cloudflared"
hdr "اختياري (سأسألك):"
[ -f "$REALIP" ]      && item "إعداد عناوين IP الحقيقية في Nginx"
[ -f "$SSHD_DROPIN" ] && item "السماح بدخول root عبر SSH (أضافه أمر التثبيت)"
[ -f "$F2B_JAIL" ]    && item "حماية fail2ban لـ SSH (يُنصح بإبقائها)"
[ -n "$API_BAK" ]     && item "إرجاع API الخاصة بـ aaPanel كما كانت قبل اللوحة"

echo
ask "متابعة؟ اكتب yes للتنفيذ" || { echo "تم الإلغاء — لم يتغير شيء."; exit 0; }

# All questions first — once removal starts it must run to the end unattended.
DO_REALIP=0; DO_SSHD=0; DO_F2B=0; DO_API=0
if [ -f "$REALIP" ] || [ -f "$SSHD_DROPIN" ] || [ -f "$F2B_JAIL" ] || [ -n "$API_BAK" ]; then
  hdr "أسئلة اختيارية (الإجابة الافتراضية no):"
  [ -f "$REALIP" ]      && ask "حذف إعداد عناوين IP الحقيقية من Nginx؟" && DO_REALIP=1
  [ -f "$SSHD_DROPIN" ] && ask "إلغاء السماح بدخول root عبر SSH (يبقى مستخدمك العادي)؟" && DO_SSHD=1
  [ -f "$F2B_JAIL" ]    && ask "حذف حماية fail2ban لـ SSH؟ (يُنصح بالإجابة no)" && DO_F2B=1
  [ -n "$API_BAK" ] && [ -f "$PANEL_API" ] && ask "إرجاع API الخاصة بـ aaPanel كما كانت قبل اللوحة؟" && DO_API=1
fi

# From here on: no prompts, survive a dropped SSH session (the tunnel may
# restart) or Ctrl+C, and keep a full log.
LOGF=/root/oldhome-uninstall.log
trap '' HUP INT PIPE
exec > >(tee --output-error=warn-nopipe -a "$LOGF") 2>&1
echo "== $(date '+%F %T') uninstall started (full=$FULL)"

# ---------------- backup ----------------
hdr "نسخة احتياطية"
BK=/root/oldhome-backup-$(date +%Y%m%d-%H%M%S).tar.gz
tar czf "$BK" --ignore-failed-read /etc/oldhome /etc/cloudflared 2>/dev/null
chmod 600 "$BK"; ok "$BK"

# ---------------- cloud (full) ----------------
ACCOUNT_DIRS=""
if [ -f /etc/oldhome/state.json ]; then
  ACCOUNT_DIRS=$(python3 -c 'import json;print(" ".join(json.load(open("/etc/oldhome/state.json"))["accounts"]))' 2>/dev/null)
fi
if [ $FULL = 1 ]; then
  hdr "Cloudflare: سجلات DNS والتونلات"
  (cd "$DIR/manager" && python3 cli.py remove-cloud)
  for a in $ACCOUNT_DIRS; do
    rm -rf "/etc/cloudflared/$a" && ok "حُذف /etc/cloudflared/$a"
  done
  if [ -z "$(ls -A /etc/cloudflared 2>/dev/null)" ]; then
    rm -f /etc/systemd/system/cloudflared@.service; systemctl daemon-reload
    ok "حُذف قالب خدمة cloudflared@"
  fi
fi

# ---------------- manager ----------------
hdr "لوحة Old-Home"
for u in oldhome-manager oldhome-manager-direct; do
  systemctl disable --now "$u" >/dev/null 2>&1
  rm -f "/etc/systemd/system/$u.service"
done
systemctl stop oldhome-aapanel-install >/dev/null 2>&1
systemctl daemon-reload
ok "الخدمات أُوقفت وحُذفت"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  for n in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
    ufw --force delete allow from "$n" to any port 8443 proto tcp >/dev/null 2>&1
  done
  ufw --force delete allow 8443/tcp >/dev/null 2>&1
  ok "قواعد الجدار الناري للمنفذ 8443 حُذفت"
fi

# ---------------- optional ----------------
if [ $DO_REALIP = 1 ]; then
  rm -f "$REALIP"
  /www/server/nginx/sbin/nginx -t >/dev/null 2>&1 && /etc/init.d/nginx reload >/dev/null 2>&1
  ok "حُذف"
fi
if [ $DO_SSHD = 1 ]; then
  mv "$SSHD_DROPIN" "$SSHD_DROPIN.removed"
  if sshd -t 2>/dev/null; then systemctl reload ssh 2>/dev/null || systemctl reload sshd; ok "أُلغي"
  else mv "$SSHD_DROPIN.removed" "$SSHD_DROPIN"; echo "  ✗ إعداد SSH غير صالح — تم التراجع"; fi
fi
if [ $DO_F2B = 1 ]; then
  rm -f "$F2B_JAIL"; systemctl restart fail2ban >/dev/null 2>&1; ok "حُذفت"
fi
if [ $DO_API = 1 ]; then
  python3 - "$PANEL_API" "$API_BAK" <<'EOF'
import json, sys
cur, orig = (json.load(open(p)) for p in sys.argv[1:3])
cur["open"] = orig.get("open", False)
cur["limit_addr"] = [ip for ip in cur.get("limit_addr", []) if ip != "127.0.0.1" or "127.0.0.1" in orig.get("limit_addr", [])]
json.dump(cur, open(sys.argv[1], "w"))
print("  ✓ open=%s, limit_addr=%s" % (cur["open"], cur["limit_addr"]))
EOF
fi

# Links that open the manager would 502 once it is gone. Removing them restarts
# the tunnel (may drop an SSH-over-tunnel session) — so it happens last.
if [ $FULL = 0 ] && [ -f /etc/oldhome/state.json ]; then
  (cd "$DIR/manager" && python3 cli.py drop-manager-links)
fi

# last: config + code (this script lives in $DIR)
rm -rf /etc/oldhome
ok "حُذف /etc/oldhome"
cd /
rm -rf "$DIR"
ok "حُذف $DIR"

echo
echo "════════════════════════════════════════════"
echo " تمت إزالة Old-Home."
[ $FULL = 0 ] && [ -n "$ACCOUNT_DIRS" ] && echo " التونلات ما زالت تعمل: $(for a in $ACCOUNT_DIRS; do printf 'cloudflared@%s ' "$a"; done)"
echo " النسخة الاحتياطية: $BK"
echo " السجل: $LOGF"
echo " لإعادة التثبيت: curl -fsSL https://raw.githubusercontent.com/ahmdeeicode/old-home-server/main/bootstrap.sh | sudo bash"
echo "════════════════════════════════════════════"
