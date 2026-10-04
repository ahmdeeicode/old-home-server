"""Link another Cloudflare domain (same or different account) from the UI.

Flow: start() runs `cloudflared tunnel login` with a throwaway HOME so the cert
lands in a temp dir, the UI shows the login URL, the user authorizes a zone in
the browser, and finish() files the cert:
  * zone in an account we already have  -> stored as that account's zone cert
  * zone in a new account               -> new dir + tunnel + cloudflared@<name>
"""
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from . import cf, state

_lock = threading.Lock()
_login = None  # dict(proc, home, url, started, output)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")


def _reader(sess):
    for line in sess["proc"].stdout:
        sess["output"] += line
        m = re.search(r"https://dash\.cloudflare\.com/argotunnel\S+", line)
        if m and not sess["url"]:
            sess["url"] = m.group(0)
    sess["proc"].wait()


def start():
    global _login
    with _lock:
        cancel()
        home = tempfile.mkdtemp(prefix="oldhome-login-")
        env = dict(os.environ, HOME=home)
        proc = subprocess.Popen(["cloudflared", "tunnel", "login"], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        _login = {"proc": proc, "home": home, "url": None, "started": time.time(), "output": ""}
        threading.Thread(target=_reader, args=(_login,), daemon=True).start()
    for _ in range(40):
        if _login["url"] or proc.poll() is not None:
            break
        time.sleep(0.25)
    if not _login["url"]:
        raise RuntimeError("تعذّر الحصول على رابط التفويض:\n" + _login["output"][-500:])
    return {"url": _login["url"]}


def _cert_path():
    return os.path.join(_login["home"], ".cloudflared", "cert.pem") if _login else None


def status():
    """pending | authorized (with zone info) | failed | none"""
    if not _login:
        return {"state": "none"}
    path = _cert_path()
    if os.path.exists(path):
        cert = cf.read_cert(path)
        zone = cf._api(cert["apiToken"], "GET", "/zones/%s" % cert["zoneID"])["name"]
        data = state.load()
        existing = next((n for n, a in data["accounts"].items() if a.get("account_id") == cert["accountID"]), None)
        return {"state": "authorized", "zone": zone, "existing_account": existing,
                "already_linked": zone in data["zones"]}
    if _login["proc"].poll() is not None:
        return {"state": "failed", "detail": _login["output"][-500:]}
    if time.time() - _login["started"] > 15 * 60:
        cancel()
        return {"state": "failed", "detail": "انتهت صلاحية رابط التفويض"}
    return {"state": "pending", "url": _login["url"]}


def cancel():
    global _login
    if _login:
        if _login["proc"].poll() is None:
            _login["proc"].kill()
        shutil.rmtree(_login["home"], ignore_errors=True)
        _login = None


def finish(name=None):
    """File the authorized cert; create a tunnel if this is a new account."""
    with _lock, state.LOCK:
        st = status()
        if st["state"] != "authorized":
            raise RuntimeError("لم يكتمل التفويض بعد")
        zone, src = st["zone"], _cert_path()
        cert = cf.read_cert(src)
        data = state.load()
        account = st["existing_account"]

        if zone in data["zones"]:
            cancel()
            return {"zone": zone, "account": data["zones"][zone]["account"], "new_account": False}

        if account:
            dest = os.path.join(cf.CF_DIR, account, "zones", zone + ".pem")
            os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
            shutil.copy2(src, dest)
            os.chmod(dest, 0o600)
            data["zones"][zone] = {"account": account, "cert": dest, "zone_id": cert["zoneID"]}
            state.save(data)
            cancel()
            return {"zone": zone, "account": account, "new_account": False}

        name = (name or zone.split(".")[0]).lower()
        if not NAME_RE.match(name):
            raise ValueError("اسم الحساب: أحرف إنجليزية صغيرة وأرقام و- فقط")
        if name in data["accounts"]:
            raise ValueError("الاسم %s مستخدم لحساب آخر" % name)
        d = os.path.join(cf.CF_DIR, name)
        os.makedirs(d, mode=0o700, exist_ok=False)
        cert_dest = os.path.join(d, "cert.pem")
        shutil.copy2(src, cert_dest)
        os.chmod(cert_dest, 0o600)

        tunnel_name = "old-home"
        creds = os.path.join(d, tunnel_name + ".json")
        r = subprocess.run(["cloudflared", "--origincert", cert_dest, "tunnel", "create",
                            "--credentials-file", creds, tunnel_name], capture_output=True, text=True)
        if r.returncode != 0:
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError("فشل إنشاء التونل: " + (r.stderr or r.stdout).strip()[-400:])
        m = re.search(r"with id ([0-9a-f-]{36})", r.stdout + r.stderr)
        used = {a["metrics_port"] for a in data["accounts"].values()}
        port = next(p for p in range(cf.METRICS_BASE_PORT, cf.METRICS_BASE_PORT + 100) if p not in used)
        data["accounts"][name] = {"tunnel_id": m.group(1), "tunnel_name": tunnel_name, "cert": cert_dest,
                                  "account_id": cert["accountID"], "metrics_port": port}
        data["zones"][zone] = {"account": name, "cert": cert_dest, "zone_id": cert["zoneID"]}
        state.save(data)
        cf.apply_config(data, name)
        subprocess.run(["systemctl", "enable", "cloudflared@%s" % name], capture_output=True)
        cancel()
        return {"zone": zone, "account": name, "new_account": True}
