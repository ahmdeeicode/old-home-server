#!/usr/bin/env bash
# Read-only pre-install check. Changes NOTHING on the server.
# Exit code: 0 = safe, 1 = warnings (review first), 2 = blockers (do not install)
#
#   bash preflight.sh            (install.sh --check runs this)

PORT="${OLDHOME_PORT:-8800}"
PANEL=/www/server/panel
WARN=0; BLOCK=0

hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
info() { printf '  \033[36mℹ\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$*"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; BLOCK=$((BLOCK+1)); }

hdr "النظام"
. /etc/os-release 2>/dev/null
case "$ID" in
  ubuntu|debian) ok "$PRETTY_NAME" ;;
  *) bad "نظام غير مدعوم: ${PRETTY_NAME:-unknown} (Ubuntu/Debian فقط)" ;;
esac
[ "$(id -u)" = 0 ] && ok "صلاحية root" || bad "يجب التشغيل بصلاحية root"
mem_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
swap_mb=$(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo)
disk_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
if [ "$mem_mb" -lt 1000 ] && [ "$swap_mb" -lt 1000 ]; then
  warn "الذاكرة ${mem_mb}MB بدون Swap كافية — أضف Swap قبل التثبيت"
else
  ok "الذاكرة ${mem_mb}MB، Swap ${swap_mb}MB"
fi
[ "${disk_gb:-0}" -ge 5 ] && ok "مساحة القرص المتاحة ${disk_gb}GB" || warn "مساحة القرص المتاحة ${disk_gb}GB فقط"

hdr "aaPanel"
if [ -d "$PANEL" ]; then
  ver=$(grep -m1 -oE "g.version *= *'[^']+'" "$PANEL/class/common.py" 2>/dev/null | grep -oE "[0-9][0-9.]*")
  port=$(cat "$PANEL/data/port.pl" 2>/dev/null)
  if grep -qE '(^|[^a-zA-Z0-9])bt\.cn([^a-zA-Z0-9]|$)' "$PANEL/config/config.json" 2>/dev/null && ! grep -qi aapanel "$PANEL/config/config.json"; then
    bad "هذه لوحة BT الصينية وليست aaPanel — غير مدعومة"
  fi
  case "$ver" in
    8.*) ok "مثبّت مسبقاً — الإصدار $ver (مختبر) — لن يُعاد تثبيته" ;;
    "")  warn "مثبّت لكن تعذّر معرفة الإصدار" ;;
    *)   warn "مثبّت — الإصدار $ver لم يُختبر (المختبر: 8.x)" ;;
  esac
  info "منفذ اللوحة: $port"
  api=$(python3 - <<'EOF' 2>/dev/null
import json
d = json.load(open('/www/server/panel/config/api.json'))
print("%s|%s|%s" % (d.get('open'), bool(d.get('token_crypt') or d.get('token')), ",".join(d.get('limit_addr') or [])))
EOF
)
  IFS='|' read -r api_open api_key api_ips <<<"$api"
  if [ "$api_open" = "True" ] && [ "$api_key" = "True" ]; then
    ok "الـ API مفعّلة مسبقاً — سيُعاد استخدام نفس المفتاح (لن يتعطل أي تطبيق يستخدمها)"
    info "العناوين المسموح لها حالياً: ${api_ips:-لا شيء} — سيُضاف 127.0.0.1 فقط"
  else
    ok "الـ API غير مفعّلة — ستُفعَّل للجهاز المحلي فقط (127.0.0.1)"
  fi
  sites=$(sqlite3 "$PANEL/data/default.db" "select name from sites" 2>/dev/null)
  n=$(printf '%s' "$sites" | grep -c . )
  info "عدد المواقع الموجودة: $n — لن يتغير فيها شيء (تظهر في اللوحة كـ «غير منشور»)"
  for s in $sites; do
    conf="$PANEL/vhost/nginx/$s.conf"
    [ -f "$conf" ] || continue
    if grep -q "HTTP_TO_HTTPS_START" "$conf"; then
      info "  $s — Force HTTPS مفعّل (سيُنشر عبر 443 تلقائياً)"
    elif grep -q "listen 443" "$conf"; then
      info "  $s — فيه SSL"
    fi
  done
else
  info "غير مثبّت — سيُثبَّت تلقائياً (5–10 دقائق)"
fi

hdr "خادم الويب"
if ss -tln | grep -qE "[:.]80\s"; then ok "Nginx يعمل على المنفذ 80"; else info "لا شيء على المنفذ 80 — ثبّت LNMP من aaPanel لاحقاً"; fi
if ss -tln | grep -qE "[:.]3306\s"; then ok "MySQL/MariaDB يعمل"; else info "MySQL غير مثبّت (اختياري)"; fi

hdr "التونلات (Cloudflare)"
if command -v cloudflared >/dev/null; then ok "cloudflared مثبّت ($(cloudflared --version 2>&1 | awk '{print $3}'))"; else info "cloudflared غير مثبّت — سيُثبَّت"; fi
foreign_units=$(systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -i cloudflared | grep -v '^cloudflared@')
for u in $foreign_units; do warn "خدمة تونل أخرى موجودة: $u — ستبقى كما هي، لكن لن تديرها اللوحة"; done
if [ -f /etc/cloudflared/config.yml ] || [ -f /root/.cloudflared/config.yml ]; then
  warn "إعداد تونل بالطريقة الافتراضية موجود (/etc/cloudflared/config.yml أو ~/.cloudflared) — لن يُستورد"
fi
procs=$(ps -eo pid=,comm=,args= | awk '$2=="cloudflared"' | grep -vE -- "--config /etc/cloudflared/[^/]+/config.yml" | grep -v "tunnel login" | sed -E 's/(--token|token) [A-Za-z0-9._=-]+/\1 <HIDDEN>/g')
[ -n "$procs" ] && while read -r l; do warn "عملية تونل لا تتبع اللوحة: $l"; done <<<"$procs"
if command -v docker >/dev/null; then
  dk=$(docker ps --format '{{.Names}} ({{.Image}})' 2>/dev/null | grep -i cloudflared)
  [ -n "$dk" ] && while read -r l; do warn "تونل يعمل داخل Docker: $l — قد يدير نفس الدومينات"; done <<<"$dk"
fi
for d in /etc/cloudflared/*/; do
  [ -f "$d/config.yml" ] && info "حساب بطريقة اللوحة: $(basename "$d") — سيُستورد"
done
tm=$(ps -eo comm=,args= | awk '$1 ~ /^(python|python3|node|php|gunicorn)/' | grep -iE "tunnel[-_]?manager|cloudflare[-_]?manager" | head -3)
[ -n "$tm" ] && while read -r l; do warn "يبدو أن هناك مدير تونلات آخر: $l — وجود مديرين لنفس الحساب يسبب تعارضاً"; done <<<"$tm"

hdr "لوحة Old-Home"
if [ -f /etc/oldhome/state.json ]; then info "مثبّتة مسبقاً — سيكون التثبيت تحديثاً فقط (الإعدادات محفوظة)"; fi
owner=$(ss -tlnp 2>/dev/null | grep -E "[:.]$PORT\s" | grep -oE 'users:\(\("[^"]+"' | cut -d'"' -f2)
if [ -z "$owner" ]; then ok "المنفذ $PORT متاح"
elif [ "$owner" = "gunicorn" ] && systemctl is-active -q oldhome-manager; then ok "المنفذ $PORT تستخدمه اللوحة نفسها"
else bad "المنفذ $PORT يستخدمه برنامج آخر ($owner) — شغّل بـ OLDHOME_PORT=<منفذ آخر>"; fi

echo
if [ $BLOCK -gt 0 ]; then
  printf '\033[31m✗ لا تثبّت: %d مشكلة تمنع التثبيت\033[0m\n' "$BLOCK"; exit 2
elif [ $WARN -gt 0 ]; then
  printf '\033[33m⚠ %d تنبيه — راجعها قبل التثبيت\033[0m\n' "$WARN"; exit 1
else
  printf '\033[32m✓ آمن للتثبيت\033[0m\n'; exit 0
fi
