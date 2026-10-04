# Old-Home Server

لوحة إدارة الخادم المنزلي (aaPanel + Cloudflare Tunnel) وأدوات إعادة البناء.

## المكونات
| المسار | الوظيفة |
|---|---|
| `manager/` | لوحة Old-Home Manager (Python/Flask، واجهة عربية) |
| `manager/core/cf.py` | التونلات وسجلات DNS في Cloudflare |
| `manager/core/aapanel.py` | إنشاء المواقع وحذفها عبر API الخاصة بـ aaPanel (مقيّدة بـ 127.0.0.1) |
| `manager/cli.py` | `import` و`set-password` و`apply` |
| `install.sh` | تثبيت اللوحة أو تحديثها كخدمة systemd |

## أين تُحفظ الإعدادات (خارج المستودع)
- `/etc/oldhome/state.json`: المرجع الوحيد للحسابات والدومينات والمسارات
- `/etc/oldhome/aapanel_api.json`: مفتاح API الخاص بـ aaPanel
- `/etc/cloudflared/<account>/`: `cert.pem` + `<tunnel>.json` + `config.yml` (الملف الأخير يُولَّد تلقائياً)

**لا تُرفع أي أسرار إلى هذا المستودع.** تُحفظ النسخ الاحتياطية مشفّرة في Releases.

## الوصول إلى اللوحة
تستمع اللوحة على `127.0.0.1:8800` فقط:
```
ssh -L 8800:localhost:8800 ssh-oldhome.ssaa.site
```
ثم افتح http://localhost:8800

## قواعد الأمان
- لا قاعدة `*` (wildcard) في أي دومين
- لا يُنشأ سجل DNS إلا إذا كان الاسم غير مستخدم، ولا يُحذف إلا إذا كان يشير إلى تونل هذا الخادم
- مسارات اللوحة وSSH محمية (`locked`)، ولا تُحذف من الواجهة
