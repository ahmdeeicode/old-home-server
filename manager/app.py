"""Old-Home Manager — one-click sites, tunnels and health for this server."""
import functools
import os
import re
import secrets

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash

from core import aapanel, cf, state, system

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


def api(fn):
    """JSON endpoint: requires login, and a custom header on writes (CSRF guard)."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("ok"):
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

@app.post("/api/login")
def login():
    pw = (request.get_json(silent=True) or {}).get("password", "")
    hashed = open(os.path.join(ETC, "manager_password.hash")).read().strip()
    if check_password_hash(hashed, pw):
        session.clear()
        session["ok"] = True
        session.permanent = True
        return jsonify(ok=True)
    return jsonify(error="كلمة المرور غير صحيحة"), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(ok=bool(session.get("ok")))


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


def _publish(data, hostname, service="http://localhost:80", kind="site", label=""):
    zone = state.zone_for(data, hostname)
    if not zone:
        raise ValueError("الدومين %s لا يتبع أي حساب Cloudflare مربوط" % hostname)
    cf.dns_create(data, hostname)  # raises if the name belongs to something else
    if not state.find_route(data, hostname):
        data["routes"].append({"hostname": hostname, "service": service, "kind": kind, "label": label})
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
