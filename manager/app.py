"""Old-Home Manager — one-click sites, tunnels and health for this server."""
import functools
import os
import re
import secrets

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash

from core import aapanel, access, accounts, cf, direct, installer, state, system

ETC = state.ETC
HERE = os.path.dirname(os.path.abspath(__file__))
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

app = Flask(__name__, static_folder=None)


def _secret_key():
    path = os.path.join(ETC, "secret.key")
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, secrets.token_bytes(32))
        os.close(fd)
    return open(path, "rb").read()


app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 12)


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
DIRECT = os.environ.get("OLDHOME_DIRECT") == "1"   # the 0.0.0.0:8443 HTTPS listener


@app.before_request
def access_guard():
    """Traffic that arrived through Cloudflare (tunnel) must carry a valid
    Cloudflare Access token for this manager's Access app — otherwise 403,
    before any page or login form is served. SSH-forwarded localhost access
    is unaffected. The direct-by-IP listener (OLDHOME_DIRECT) is filtered by
    the chosen mode: lan = private networks only, public = anyone."""
    if DIRECT:
        mode = direct.mode()
        if mode == "public" or (mode == "lan" and direct.is_private(request.remote_addr)):
            return None
        return "Forbidden — direct access is not allowed from %s" % request.remote_addr, 403
    host = request.host.rsplit(":", 1)[0].lower() if not request.host.startswith("[") else "[::1]"
    via_cf = any(h in request.headers for h in ("Cf-Ray", "Cf-Connecting-Ip", "Cf-Access-Jwt-Assertion"))
    if host in LOCAL_HOSTS and not via_cf:
        return None
    route = state.find_route(state.load(), host)
    if (route or {}).get("kind") == "manager" and route.get("password_only"):
        return None   # owner chose password-only (no Access); login is throttled per visitor IP
    acc = (route or {}).get("access") if (route or {}).get("kind") == "manager" else None
    token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
    if not acc or not token:
        return "Forbidden — Cloudflare Access required", 403
    try:
        access.verify(token, acc["team"], acc["aud"])
    except Exception:
        return "Forbidden — invalid Cloudflare Access token", 403
    return None


PW_FILE = os.path.join(ETC, "manager_password.hash")


def _pw_hash():
    with open(PW_FILE) as f:
        return f.read().strip()


def _pw_version():
    """Changes whenever the password changes → older sessions stop working."""
    import hashlib
    return hashlib.sha256(_pw_hash().encode()).hexdigest()[:16]


def api(fn):
    """JSON endpoint: requires login, and a custom header on writes (CSRF guard)."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("ok") or session.get("pwv") != _pw_version():
            session.clear()
            return jsonify(error="unauthorized"), 401
        if request.method != "GET" and request.headers.get("X-OldHome") != "1":
            return jsonify(error="bad request"), 400
        try:
            return jsonify(fn(*a, **kw))
        except (cf.CFError, aapanel.PanelError, ValueError, RuntimeError) as e:
            return jsonify(error=str(e)), 400
        except Exception as e:  # never hand the UI an HTML 500 page
            app.logger.exception("unhandled error")
            return jsonify(error="خطأ داخلي: %s" % e), 500
    return wrapper


def _host(value):
    h = (value or "").strip().lower().rstrip(".")
    if not HOST_RE.match(h):
        raise ValueError("اسم الدومين غير صحيح: %s" % (value or ""))
    return h


# ---------- auth ----------

def _client_ip():
    """Real visitor IP. Behind cloudflared every request is from 127.0.0.1;
    Cloudflare sets CF-Connecting-IP (it overwrites any client-sent value)."""
    if request.remote_addr in ("127.0.0.1", "::1") and request.headers.get("Cf-Connecting-Ip"):
        return request.headers["Cf-Connecting-Ip"]
    return request.remote_addr or "?"


_fails = {}  # ip -> [timestamps of failed logins]
MAX_FAILS, WINDOW = 5, 15 * 60


@app.post("/api/login")
def login():
    import time
    ip = _client_ip()
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < WINDOW]
    if len(recent) >= MAX_FAILS:
        wait = int((WINDOW - (now - recent[0])) / 60) + 1
        return jsonify(error="محاولات خاطئة كثيرة — حاول بعد %d دقيقة" % wait), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if check_password_hash(_pw_hash(), pw):
        _fails.pop(ip, None)
        session.clear()
        session["ok"] = True
        session["pwv"] = _pw_version()
        session.permanent = True
        return jsonify(ok=True)
    recent.append(now)
    _fails[ip] = recent
    return jsonify(error="كلمة المرور غير صحيحة (%d/%d)" % (len(recent), MAX_FAILS)), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(ok=bool(session.get("ok")) and session.get("pwv") == _pw_version())


@app.post("/api/settings/password")
@api
def change_password():
    import time
    from werkzeug.security import generate_password_hash
    body = request.get_json(silent=True) or {}
    ip = _client_ip()
    recent = [t for t in _fails.get(ip, []) if time.time() - t < WINDOW]
    if len(recent) >= MAX_FAILS:
        raise ValueError("محاولات خاطئة كثيرة — حاول لاحقاً")
    if not check_password_hash(_pw_hash(), body.get("current", "")):
        _fails[ip] = recent + [time.time()]
        raise ValueError("كلمة المرور الحالية غير صحيحة")
    new = body.get("new", "")
    if len(new) < 10:
        raise ValueError("كلمة المرور الجديدة يجب أن تكون 10 أحرف على الأقل")
    if new != body.get("confirm"):
        raise ValueError("كلمتا المرور الجديدتان غير متطابقتين")
    if new == body.get("current"):
        raise ValueError("اختر كلمة مرور مختلفة عن الحالية")
    tmp = PW_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, generate_password_hash(new).encode())
    os.close(fd)
    os.replace(tmp, PW_FILE)
    session["pwv"] = _pw_version()   # keep THIS session; every other one is now invalid
    return {"ok": True}


# ---------- dashboard ----------

@app.get("/api/overview")
@api
def overview():
    data = state.load()
    tunnels = []
    for name, acct in data["accounts"].items():
        st = cf.tunnel_status(data, name)
        tunnels.append({"account": name, "tunnel": acct["tunnel_name"],
                        "zones": [z for z, v in data["zones"].items() if v["account"] == name], **st})
    return {"stats": system.stats(), "services": system.services(), "tunnels": tunnels,
            "realip": system.realip_enabled()}


@app.post("/api/services/<sid>/restart")
@api
def restart_service(sid):
    if sid.startswith("tunnel:"):
        acct = sid[7:]
        if acct not in state.load()["accounts"]:
            raise ValueError("unknown tunnel")
        cf.restart(acct)
        return {"ok": True}
    ok, out = system.restart_service(sid)
    if not ok:
        raise RuntimeError(out)
    return {"ok": True}


# ---------- sites ----------

@app.get("/api/sites")
@api
def sites():
    data = state.load()
    panel_sites = {s["name"]: s for s in aapanel.list_sites()}
    rows = []
    for r in data["routes"]:
        s = panel_sites.pop(r["hostname"], None)
        rows.append({"hostname": r["hostname"], "kind": r.get("kind", "site"), "label": r.get("label", ""),
                     "locked": r.get("locked", False), "published": True,
                     "account": data["zones"].get(state.zone_for(data, r["hostname"]) or "", {}).get("account"),
                     "panel": s, "service": r["service"]})
    for name, s in panel_sites.items():
        zone = state.zone_for(data, name)
        rows.append({"hostname": name, "kind": "site", "label": "", "locked": False, "published": False,
                     "account": data["zones"][zone]["account"] if zone else None, "panel": s,
                     "publishable": bool(zone)})
    for row in rows:
        if row["kind"] == "site" and row["panel"]:
            row["http"] = system.site_http_status(row["hostname"])
            # Force HTTPS was switched on after publishing → :80 route would loop
            row["needs_fix"] = bool(row["published"] and row.get("service", "").startswith("http://")
                                    and system.site_force_https(row["hostname"]))
    return {"sites": rows, "zones": sorted(data["zones"]), "php": aapanel.php_versions()}


@app.get("/api/dns-check")
@api
def dns_check():
    h = _host(request.args.get("hostname"))
    data = state.load()
    if state.find_route(data, h):
        return {"status": "ours"}
    st, rec = cf.dns_lookup(data, h)
    return {"status": st, "record": rec and {"type": rec["type"], "content": rec["content"]}}


def _publish(data, hostname, label=""):
    """Route a website through the tunnel (or re-sync its route after its
    SSL settings changed). The origin follows the site's Force-HTTPS state."""
    zone = state.zone_for(data, hostname)
    if not zone:
        raise ValueError("الدومين %s لا يتبع أي حساب Cloudflare مربوط" % hostname)
    cf.dns_create(data, hostname)  # raises if the name belongs to something else
    route = state.find_route(data, hostname)
    if route and route.get("locked"):
        raise ValueError("هذا المسار محمي")
    if not route:
        route = {"hostname": hostname, "kind": "site", "label": label}
        data["routes"].append(route)
    for k in ("service", "no_tls_verify", "origin_server_name"):
        route.pop(k, None)
    route.update(system.site_origin(hostname))
    state.save(data)
    cf.apply_config(data, data["zones"][zone]["account"])


@app.post("/api/sites")
@api
def add_site():
    body = request.get_json(silent=True) or {}
    h = _host(body.get("hostname"))
    with state.LOCK:
        data = state.load()
        if not state.zone_for(data, h):
            raise ValueError("الدومين %s لا يتبع أي حساب Cloudflare مربوط" % h)
        st, rec = cf.dns_lookup(data, h)
        if st == "foreign":
            raise ValueError("الاسم %s مستخدم مسبقاً (%s ← %s). اختر اسماً آخر." % (h, rec["type"], rec["content"]))
        res = aapanel.add_site(h, php=body.get("php", ""), with_db=bool(body.get("db")), note=body.get("note", ""))
        try:
            _publish(data, h, label=body.get("note", ""))
        except Exception:
            # roll back so the panel and the tunnel never disagree
            aapanel.delete_site(res["id"], h, delete_files=True, delete_db=bool(body.get("db")))
            raise
    return {"ok": True, "url": "https://" + h, "db": res.get("db")}


@app.post("/api/sites/<hostname>/publish")
@api
def publish(hostname):
    h = _host(hostname)
    with state.LOCK:
        _publish(state.load(), h)
    return {"ok": True, "url": "https://" + h}


@app.post("/api/sites/<hostname>/unpublish")
@api
def unpublish(hostname):
    h = _host(hostname)
    with state.LOCK:
        data = state.load()
        r = state.find_route(data, h)
        if not r:
            raise ValueError("غير منشور")
        if r.get("locked"):
            raise ValueError("هذا المسار محمي (اللوحة أو SSH) ولا يُلغى من هنا")
        account = data["zones"][state.zone_for(data, h)]["account"]
        data["routes"].remove(r)
        state.save(data)
        cf.apply_config(data, account)
        cf.dns_delete(data, h)
    return {"ok": True}


@app.delete("/api/sites/<hostname>")
@api
def delete_site(hostname):
    h = _host(hostname)
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != h:
        raise ValueError("اكتب اسم الموقع للتأكيد")
    with state.LOCK:
        data = state.load()
        r = state.find_route(data, h)
        if r and r.get("locked"):
            raise ValueError("هذا المسار محمي ولا يُحذف")
        if r:
            account = data["zones"][state.zone_for(data, h)]["account"]
            data["routes"].remove(r)
            state.save(data)
            cf.apply_config(data, account)
            cf.dns_delete(data, h)
        site = next((s for s in aapanel.list_sites() if s["name"] == h), None)
        if site:
            aapanel.delete_site(site["id"], h, delete_files=bool(body.get("files")),
                                delete_db=bool(body.get("db")))
    return {"ok": True}


# ---------- Cloudflare accounts / domains ----------

@app.get("/api/accounts")
@api
def accounts_list():
    data = state.load()
    out = []
    for name, acct in data["accounts"].items():
        out.append({"name": name, "tunnel": acct["tunnel_name"],
                    "zones": sorted(z for z, v in data["zones"].items() if v["account"] == name),
                    **cf.tunnel_status(data, name)})
    return {"accounts": out, "login": accounts.status()}


@app.post("/api/accounts/login")
@api
def accounts_login():
    return accounts.start()


@app.get("/api/accounts/login")
@api
def accounts_login_status():
    return accounts.status()


@app.post("/api/accounts/finish")
@api
def accounts_finish():
    return accounts.finish((request.get_json(silent=True) or {}).get("name"))


@app.post("/api/accounts/cancel")
@api
def accounts_cancel():
    accounts.cancel()
    return {"ok": True}


# ---------- server routes: aaPanel / SSH / this manager ----------

MANAGER_PORT = int(os.environ.get("OLDHOME_PORT", "8800"))
SERVER_KINDS = {"panel": "aaPanel", "ssh": "SSH", "manager": "Old-Home"}


def _ssh_port():
    try:
        import subprocess
        out = subprocess.run(["sshd", "-T"], capture_output=True, text=True).stdout
        return int(re.search(r"^port (\d+)", out, re.M).group(1))
    except Exception:
        return 22


@app.get("/api/server-routes")
@api
def server_routes():
    data = state.load()
    rows = [{"hostname": r["hostname"], "kind": r["kind"], "label": SERVER_KINDS[r["kind"]],
             "account": data["zones"].get(state.zone_for(data, r["hostname"]) or "", {}).get("account"),
             "protected": bool(r.get("access")) if r["kind"] == "manager" else None,
             "password_only": bool(r.get("password_only")),
             "admin_path": system.panel_admin_path() if r["kind"] == "panel" else ""}
            for r in data["routes"] if r.get("kind") in SERVER_KINDS]
    return {"routes": rows, "zones": sorted(data["zones"])}


@app.post("/api/server-routes")
@api
def add_server_route():
    body = request.get_json(silent=True) or {}
    kind, h = body.get("kind"), _host(body.get("hostname"))
    if kind not in SERVER_KINDS:
        raise ValueError("نوع غير معروف")
    with state.LOCK:
        data = state.load()
        zone = state.zone_for(data, h)
        if not zone:
            raise ValueError("الدومين %s لا يتبع أي حساب Cloudflare مربوط" % h)
        if state.find_route(data, h):
            raise ValueError("هذا الرابط مستخدم مسبقاً على هذا الخادم")
        route = {"hostname": h, "kind": kind, "label": SERVER_KINDS[kind], "locked": True}
        if kind == "panel":
            if not system.panel_port():
                raise ValueError("aaPanel غير مثبّت بعد — ثبّته من صفحة «التثبيت» ثم أضف رابطه")
            route.update(service="https://localhost:%d" % system.panel_port(), no_tls_verify=True)
        elif kind == "ssh":
            route.update(service="ssh://localhost:%d" % _ssh_port())
        elif body.get("protection") == "password":
            # owner's explicit choice: no Access, password + per-IP throttling only
            route.update(service="http://localhost:%d" % MANAGER_PORT, password_only=True)
        else:
            # DNS first, NO ingress yet: the manager must stay unreachable
            # unless Cloudflare Access is demonstrably in front of it.
            status, _ = cf.dns_lookup(data, h)
            cf.dns_create(data, h)
            pr = access.probe(h, neighbours=[r["hostname"] for r in data["routes"]
                                             if state.zone_for(data, r["hostname"]) == zone] + [zone])
            if not pr["protected"]:
                if status == "free":
                    cf.dns_delete(data, h)
                raise ValueError("لم يُفتح الرابط: Cloudflare Access لا يحمي %s بعد (%s). "
                                 "أنشئ تطبيق Access لهذا الرابط أولاً ثم أعد المحاولة." % (h, pr["detail"]))
            route.update(service="http://localhost:%d" % MANAGER_PORT,
                         access={"team": pr["team"], "aud": pr["aud"]})
        cf.dns_create(data, h)
        data["routes"].append(route)
        state.save(data)
        cf.apply_config(data, data["zones"][zone]["account"])
    return {"ok": True, "url": "https://" + h}


@app.delete("/api/server-routes/<hostname>")
@api
def delete_server_route(hostname):
    h = _host(hostname)
    if (request.get_json(silent=True) or {}).get("confirm") != h:
        raise ValueError("اكتب الرابط للتأكيد")
    with state.LOCK:
        data = state.load()
        r = state.find_route(data, h)
        if not r or r.get("kind") not in SERVER_KINDS:
            raise ValueError("ليس رابط خادم")
        account = data["zones"][state.zone_for(data, h)]["account"]
        data["routes"].remove(r)
        state.save(data)
        cf.apply_config(data, account)
        cf.dns_delete(data, h)
    return {"ok": True}


# ---------- setup: install aaPanel from the official command ----------

@app.get("/api/setup")
@api
def setup_status():
    return installer.status()


@app.post("/api/setup/aapanel")
@api
def setup_aapanel():
    return installer.start((request.get_json(silent=True) or {}).get("command", ""))


# ---------- logs & settings ----------

@app.get("/api/logs")
@api
def logs():
    data = state.load()
    src = request.args.get("source")
    if not src:
        names = [s["name"] for s in aapanel.list_sites()]
        return {"sources": system.log_sources(list(data["accounts"]), names)}
    return {"text": system.read_log(src, request.args.get("lines", 200))}


@app.get("/api/settings/direct")
@api
def direct_get():
    return direct.info()


@app.post("/api/settings/direct")
@api
def direct_set():
    return direct.set_mode((request.get_json(silent=True) or {}).get("mode"))


@app.post("/api/settings/realip")
@api
def realip():
    system.set_realip(bool((request.get_json(silent=True) or {}).get("enable")))
    return {"ok": True, "enabled": system.realip_enabled()}


# ---------- static UI ----------

@app.get("/")
def index():
    return send_from_directory(os.path.join(HERE, "static"), "index.html")


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(os.path.join(HERE, "static"), p)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8800, debug=True)
